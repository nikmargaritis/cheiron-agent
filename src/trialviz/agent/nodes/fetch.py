"""Node 3 — fetch (deterministic). Plan → ClinicalTrials.gov API calls.

Ingestion strategy (the answer to "what if 10,000+ trials match?")
------------------------------------------------------------------
The v2 API has no filtered aggregation endpoint (``/stats/field/values`` is
registry-wide and rejects query params), so counts must come from ``/studies``.

* **bucket_counts** — group_by is enumerable (phase, status, sponsor_class,
  intervention_type, study_type) or start_year (one RANGE filter per year). One request
  per bucket with ``countTotal=true&pageSize=<citations>``: each response carries the
  bucket's EXACT count *and* the records to cite for it. Cost is O(buckets), independent
  of how many trials match, and every bucket is guaranteed citations.
* **download** — open-ended dimensions (sponsor, country, intervention, condition),
  networks, histograms, scatter. Token-paginate up to ``ctgov_max_records`` with a
  minimal ``fields=`` list (~1s per 1000 records) and aggregate locally. If more trials
  match than were downloaded, the response says so (``count_method: "sample"``).

Comparisons run the chosen strategy once per series, concurrently.
"""

import asyncio
from datetime import date

from trialviz.agent.state import AgentState, Deps, FetchResult, SeriesData
from trialviz.ctgov.client import CTGovClient
from trialviz.ctgov.fields import DIMENSIONS, nct_id, pieces_for
from trialviz.ctgov.query import build_params, merge_filters
from trialviz.schemas.common import Dimension
from trialviz.schemas.plan import AnalysisKind, Filters, QueryPlan

MAX_YEAR_BUCKETS = 30  # when the earliest year is inferred rather than requested


def series_targets(plan: QueryPlan, base: Filters) -> list[tuple[str | None, Filters]]:
    if plan.series:
        return [(s.label, merge_filters(base, s.filters)) for s in plan.series]
    return [(None, base)]


def uses_bucket_counts(plan: QueryPlan) -> bool:
    return (
        plan.analysis
        in (AnalysisKind.TIME_TREND, AnalysisKind.DISTRIBUTION, AnalysisKind.COMPARISON)
        and plan.group_by is not None
        and (plan.group_by == Dimension.START_YEAR or DIMENSIONS[plan.group_by].enumerable)
    )


def year_filter(lo: int | str, hi: int | str) -> str:
    lo_s = f"{lo}-01-01" if isinstance(lo, int) else lo
    hi_s = f"{hi}-12-31" if isinstance(hi, int) else hi
    return f"AREA[StartDate]RANGE[{lo_s},{hi_s}]"


async def _bucket_counts(
    client: CTGovClient, label: str | None, filters: Filters, buckets: dict[str, str],
    pieces: list[str], per_bucket: int,
) -> SeriesData:
    base = build_params(filters)
    total, *pages = await asyncio.gather(
        client.count(base),
        *(
            client.fetch_studies(build_params(filters, [expr]), pieces, max_records=per_bucket)
            for expr in buckets.values()
        ),
    )
    studies = {nct_id(s): s for page in pages for s in page.studies}
    exact = {key: page.total_count for key, page in zip(buckets, pages, strict=True)}
    return SeriesData(label, filters, total, list(studies.values()), exact)


async def _download(
    client: CTGovClient, label: str | None, filters: Filters, pieces: list[str], cap: int
) -> SeriesData:
    page = await client.fetch_studies(build_params(filters), pieces, max_records=cap)
    return SeriesData(label, filters, page.total_count, page.studies)


async def _year_bounds(client: CTGovClient, filters: Filters) -> tuple[int, int] | None:
    # Exclude undated trials explicitly rather than relying on where the API sorts them.
    params = build_params(filters, [year_filter("MIN", "MAX")])
    first, last = await asyncio.gather(
        client.fetch_studies(params, ["NCTId", "StartDate"], 1, sort="StartDate:asc"),
        client.fetch_studies(params, ["NCTId", "StartDate"], 1, sort="StartDate:desc"),
    )
    years = [
        DIMENSIONS[Dimension.START_YEAR].extract(p.studies[0])
        for p in (first, last) if p.studies
    ]
    if len(years) != 2 or not all(years):
        return None
    return int(years[0][0].value), int(years[1][0].value)


async def _plan_years(
    client: CTGovClient, targets: list[tuple[str | None, Filters]], notes: list[str]
) -> list[int]:
    bounds = [b for b in await asyncio.gather(*(_year_bounds(client, f) for _, f in targets)) if b]
    if not bounds:
        return []
    # Explicit bounds are shared by all series (they come from the base filters).
    f0 = targets[0][1]
    hi = f0.start_year_max or max(b[1] for b in bounds)
    lo = f0.start_year_min or min(b[0] for b in bounds)
    if f0.start_year_min is None and hi - lo + 1 > MAX_YEAR_BUCKETS:
        lo = hi - MAX_YEAR_BUCKETS + 1
        before = await asyncio.gather(
            *(client.count(build_params(f, [year_filter("MIN", lo - 1)])) for _, f in targets)
        )
        for (label, _), n in zip(targets, before, strict=True):
            if n:
                notes.append(f"{_who(label)}{n} trials started before {lo} are not shown.")
    this_year = date.today().year
    if hi > this_year:
        notes.append(f"Years after {this_year} are anticipated start dates.")
    return list(range(lo, hi + 1))


def _who(label: str | None) -> str:
    return f"{label}: " if label else ""


async def fetch_node(state: AgentState, deps: Deps) -> AgentState:
    plan, client, settings = state["plan"], deps.ctgov, deps.settings
    notes = list(state.get("notes", []))
    targets = series_targets(plan, state["filters"])

    if uses_bucket_counts(plan):
        dim = plan.group_by
        assert dim is not None
        if dim == Dimension.START_YEAR:
            years = await _plan_years(client, targets, notes)
            buckets = {str(y): year_filter(y, y) for y in years}
            missing = await asyncio.gather(
                *(client.count(build_params(f, ["AREA[StartDate]MISSING"])) for _, f in targets)
            )
            for (label, _), n in zip(targets, missing, strict=True):
                if n:
                    notes.append(f"{_who(label)}{n} trials have no start date and are not shown.")
        else:
            spec = DIMENSIONS[dim]
            buckets = {v: spec.bucket_filter(v) for v in spec.enum_values or ()}

        pieces = pieces_for([dim])
        per_bucket = max(1, settings.max_citations_per_datum)
        series = await asyncio.gather(
            *(_bucket_counts(client, lbl, f, buckets, pieces, per_bucket) for lbl, f in targets)
        )
        result = FetchResult(list(series))
    else:
        net = plan.network
        pieces = pieces_for(
            [plan.group_by, net.source if net else None, net.target if net else None],
            [plan.x_numeric, plan.y_numeric],
        )
        series = await asyncio.gather(
            *(_download(client, lbl, f, pieces, settings.ctgov_max_records) for lbl, f in targets)
        )
        result = FetchResult(list(series))
        for s in series:
            if len(s.studies) < s.total_count:
                notes.append(
                    f"{_who(s.label)}aggregated the first {len(s.studies):,} of "
                    f"{s.total_count:,} matching trials (API default order); counts are a sample."
                )

    if plan.series:
        notes.append(
            "Trials per series: "
            + ", ".join(f"{s.label} = {s.total_count:,}" for s in result.series)
            + ". A trial matching several series is counted in each."
        )
    return {"fetch": result, "notes": notes}
