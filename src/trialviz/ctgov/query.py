"""Translate plan ``Filters`` into ClinicalTrials.gov v2 ``/studies`` query params.

Pure functions, no I/O — the easiest part of the system to unit-test exhaustively.
"""

from trialviz.schemas.plan import Filters


def merge_filters(base: Filters, override: Filters) -> Filters:
    """Layer ``override`` on top of ``base`` (non-null override fields win)."""
    merged = base.model_dump()
    merged.update({k: v for k, v in override.model_dump().items() if v not in (None, [])})
    return Filters.model_validate(merged)


def advanced_terms(f: Filters) -> list[str]:
    terms: list[str] = []
    if f.phases:
        terms.append("AREA[Phase](" + " OR ".join(p.value for p in f.phases) + ")")
    if f.start_year_min or f.start_year_max:
        lo = f"{f.start_year_min}-01-01" if f.start_year_min else "MIN"
        hi = f"{f.start_year_max}-12-31" if f.start_year_max else "MAX"
        terms.append(f"AREA[StartDate]RANGE[{lo},{hi}]")
    return terms


def build_params(f: Filters, extra_advanced: list[str] | None = None) -> dict[str, str]:
    """Params for ``GET /studies``. ``extra_advanced`` is AND-ed in (used for bucket counts)."""
    params: dict[str, str] = {}
    if f.condition:
        params["query.cond"] = f.condition
    if f.intervention:
        params["query.intr"] = f.intervention
    if f.sponsor:
        params["query.spons"] = f.sponsor
    if f.country:
        params["query.locn"] = f.country
    if f.keywords:
        params["query.term"] = f.keywords
    if f.statuses:
        params["filter.overallStatus"] = ",".join(s.value for s in f.statuses)

    terms = advanced_terms(f) + (extra_advanced or [])
    if terms:
        params["filter.advanced"] = " AND ".join(terms)
    return params


def describe(f: Filters) -> dict[str, str | int | list[str]]:
    """Human/JSON-friendly view of the non-empty filters, for ``meta.filters``."""
    return {
        k: ([str(x) for x in v] if isinstance(v, list) else v)
        for k, v in f.model_dump(mode="json").items()
        if v not in (None, [])
    }
