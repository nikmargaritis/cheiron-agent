"""Deterministic visualization builders: aggregates → typed Visualization models.

Chart type is a function of the plan (analysis kind + group_by), not an LLM decision,
so the same question class always yields the same chart shape. Building through the
Pydantic models means every spec is validated (encoded fields exist on every row,
edges reference real nodes) before it leaves the service.
"""

from typing import Any

from trialviz.analysis.aggregate import Bin, Bucket, Graph, NumericPoint
from trialviz.analysis.citations import citations_for, make_citation
from trialviz.ctgov.fields import DIMENSIONS, NUMERIC_FIELDS, brief_title, nct_id
from trialviz.schemas.common import Dimension
from trialviz.schemas.plan import AnalysisKind, Filters, QueryPlan
from trialviz.schemas.response import (
    BarChart,
    Channel,
    GroupedBarChart,
    Histogram,
    HistogramEncoding,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
    ScatterPlot,
    TimeSeries,
    XYEncoding,
)

COUNT_FIELD = "trial_count"
SERIES_FIELD = "series"
LONG_LABEL_DIMS = {
    Dimension.SPONSOR, Dimension.INTERVENTION, Dimension.DRUG, Dimension.CONDITION,
}

_COUNT = Channel(field=COUNT_FIELD, type="quantitative", title="Number of trials", unit="trials")


def describe_filters(f: Filters) -> str | None:
    """Human-readable filter summary for chart subtitles."""
    parts: list[str] = []
    for name, value in (
        ("Condition", f.condition), ("Intervention", f.intervention),
        ("Sponsor", f.sponsor), ("Country", f.country), ("Keywords", f.keywords),
    ):
        if value:
            parts.append(f"{name}: {value}")
    if f.phases:
        phase = DIMENSIONS[Dimension.PHASE]
        parts.append("Phase: " + ", ".join(phase.display(p) for p in f.phases))
    if f.statuses:
        status = DIMENSIONS[Dimension.STATUS]
        parts.append("Status: " + ", ".join(status.display(s) for s in f.statuses))
    if f.start_year_min or f.start_year_max:
        lo, hi = f.start_year_min, f.start_year_max
        parts.append(
            f"Started {lo}–{hi}" if lo and hi else f"Started {lo} or later" if lo
            else f"Started {hi} or earlier"
        )
    return " · ".join(parts) or None


def default_title(plan: QueryPlan) -> str:
    a = plan.analysis
    if a == AnalysisKind.NETWORK and plan.network:
        src = DIMENSIONS[plan.network.source].label
        tgt = DIMENSIONS[plan.network.target].label
        if plan.network.source == plan.network.target:
            return f"{src} co-occurrence network"
        return f"{src} ↔ {tgt} network"
    if a == AnalysisKind.HISTOGRAM and plan.x_numeric:
        return f"Distribution of trial {NUMERIC_FIELDS[plan.x_numeric].label.lower()}"
    if a == AnalysisKind.SCATTER and plan.x_numeric and plan.y_numeric:
        return f"{NUMERIC_FIELDS[plan.y_numeric].label} vs. {NUMERIC_FIELDS[plan.x_numeric].label}"
    dim = DIMENSIONS[plan.group_by].label.lower() if plan.group_by else "category"
    title = "Trials per start year" if plan.group_by == Dimension.START_YEAR else f"Trials by {dim}"
    if plan.series:
        title += ": " + " vs. ".join(s.label for s in plan.series)
    return title


def build_category_chart(
    plan: QueryPlan, buckets: list[Bucket], max_citations: int
) -> BarChart | GroupedBarChart | TimeSeries:
    """Rows like {"phase": "Phase 3", "trial_count": 41, "citations": [...]}.

    ``buckets`` must already be filtered, ordered, and (for comparisons) complete for
    every key × series combination — see ``agent.nodes.build``.
    """
    assert plan.group_by is not None
    dim = plan.group_by
    spec = DIMENSIONS[dim]
    is_time = dim == Dimension.START_YEAR

    rows: list[dict[str, Any]] = []
    for b in buckets:
        row: dict[str, Any] = {
            dim.value: int(b.key) if is_time else b.label,
            COUNT_FIELD: b.count,
            "citations": citations_for(b.evidence, max_citations),
        }
        if plan.series:
            row[SERIES_FIELD] = b.series
        rows.append(row)

    x = Channel(
        field=dim.value,
        type="temporal" if is_time else spec.field_type,
        title=spec.label,
        time_unit="year" if is_time else None,
        sort="ascending" if is_time else list(dict.fromkeys(b.label for b in buckets)),
    )
    color = (
        Channel(field=SERIES_FIELD, type="nominal", title="Series",
                sort=[s.label for s in plan.series])
        if plan.series else None
    )
    encoding = XYEncoding(x=x, y=_COUNT, color=color)
    common = dict(
        title=default_title(plan), subtitle=describe_filters(plan.filters),
        encoding=encoding, data=rows,
    )
    if is_time:
        return TimeSeries(**common)
    orientation = "horizontal" if dim in LONG_LABEL_DIMS or dim == Dimension.COUNTRY else "vertical"
    if plan.series:
        return GroupedBarChart(**common, orientation=orientation)
    return BarChart(**common, orientation=orientation)


def _fmt(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.2f}"


def build_histogram(plan: QueryPlan, bins: list[Bin], max_citations: int) -> Histogram:
    assert plan.x_numeric is not None
    num = NUMERIC_FIELDS[plan.x_numeric]
    rows = [
        {
            "bin_start": b.start,
            "bin_end": b.end,
            "bin_label": f"≥ {_fmt(b.start)}" if b.overflow
            else f"{_fmt(b.start)}–{_fmt(b.end)}",
            COUNT_FIELD: b.count,
            "citations": citations_for(b.evidence, max_citations),
        }
        for b in bins
    ]
    return Histogram(
        title=default_title(plan),
        subtitle=describe_filters(plan.filters),
        encoding=HistogramEncoding(
            bin_start=Channel(field="bin_start", type="quantitative", title=num.label,
                              unit=num.unit),
            bin_end=Channel(field="bin_end", type="quantitative", title=num.label, unit=num.unit),
            y=_COUNT,
        ),
        data=rows,
    )


def build_scatter(plan: QueryPlan, points: list[NumericPoint]) -> ScatterPlot:
    """One row per trial; its citations are the two source values it was plotted from."""
    assert plan.x_numeric is not None and plan.y_numeric is not None
    nx, ny = NUMERIC_FIELDS[plan.x_numeric], NUMERIC_FIELDS[plan.y_numeric]
    rows = []
    for p in points:
        assert p.y_fv is not None
        rows.append({
            "nct_id": nct_id(p.study),
            "brief_title": brief_title(p.study),
            nx.key.value: p.x,
            ny.key.value: p.y,
            COUNT_FIELD: 1,
            "citations": [make_citation(p.study, p.x_fv), make_citation(p.study, p.y_fv)],
        })
    return ScatterPlot(
        title=default_title(plan),
        subtitle=describe_filters(plan.filters),
        encoding=XYEncoding(
            x=Channel(field=nx.key.value, type="quantitative", title=nx.label, unit=nx.unit),
            y=Channel(field=ny.key.value, type="quantitative", title=ny.label, unit=ny.unit),
            tooltip=[
                Channel(field="nct_id", type="nominal", title="NCT ID"),
                Channel(field="brief_title", type="nominal", title="Title"),
            ],
        ),
        data=rows,
    )


def build_network(plan: QueryPlan, graph: Graph, max_citations: int) -> NetworkGraph:
    nodes = [
        NetworkNode(
            id=n.id, label=n.label, group=n.group.value, trial_count=len(n.evidence),
            citations=citations_for(n.evidence, max_citations),
        )
        for n in graph.nodes
    ]
    edges = []
    for e in graph.edges:
        citations = []
        for study, a, b in e.evidence[:max_citations]:
            citations += [make_citation(study, a), make_citation(study, b)]
        edges.append(NetworkEdge(source=e.source, target=e.target,
                                 trial_count=len(e.evidence), citations=citations))
    return NetworkGraph(
        title=default_title(plan), subtitle=describe_filters(plan.filters),
        nodes=nodes, edges=edges,
    )
