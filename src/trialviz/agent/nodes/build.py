"""Node 4 — build (deterministic). FetchResult → validated Visualization.

Dispatches on ``plan.analysis`` to ``analysis.aggregate`` (counting, binning,
co-occurrence) and then ``analysis.specs`` (typed chart models with citations).
No LLM involvement: every number in the output is computed here from API data.
"""

from trialviz.agent.nodes.respond import build_meta
from trialviz.agent.state import AgentState, Deps, FetchResult
from trialviz.analysis.aggregate import (
    Bucket,
    apply_exact_counts,
    cooccurrence_graph,
    count_by_dimension,
    histogram_bins,
    numeric_values,
    order_buckets,
    top_keys,
)
from trialviz.analysis.specs import (
    build_category_chart,
    build_histogram,
    build_network,
    build_scatter,
)
from trialviz.ctgov.fields import DIMENSIONS
from trialviz.schemas.common import Dimension
from trialviz.schemas.plan import AnalysisKind, QueryPlan
from trialviz.schemas.response import QueryResponse, ResponseStatus

DEFAULT_TOP_N = 15  # for open-ended dimensions when the plan doesn't say


def category_buckets(plan: QueryPlan, fetch: FetchResult, notes: list[str]) -> list[Bucket]:
    """Per-series buckets → one ordered, complete key × series grid."""
    dim = plan.group_by
    assert dim is not None
    spec = DIMENSIONS[dim]

    buckets: list[Bucket] = []
    for s in fetch.series:
        b = count_by_dimension(s.studies, dim, s.label)
        if s.exact_counts is not None:
            b = apply_exact_counts(b, s.exact_counts, dim, s.label)
        buckets += b

    # Totals per key across series decide which keys are shown and in what order.
    totals: dict[str, Bucket] = {}
    for b in buckets:
        t = totals.setdefault(b.key, Bucket(b.key, b.label, 0))
        t.count += b.count
    if spec.field_type == "nominal":  # zero bars on an ordered axis are information
        totals = {k: t for k, t in totals.items() if t.count > 0}

    n = None if dim == Dimension.START_YEAR else plan.top_n
    if n is None and not spec.enumerable and dim != Dimension.START_YEAR:
        n = DEFAULT_TOP_N
    if n is not None and len(totals) > n:
        kept = set(top_keys(list(totals.values()), n))
        notes.append(f"Showing the top {n} of {len(totals):,} {spec.label.lower()} values.")
        totals = {k: t for k, t in totals.items() if k in kept}

    if spec.multi_valued:
        notes.append(
            f"A trial can list several {spec.label.lower()} values and is counted under each, "
            "so bars can sum to more than the number of trials."
        )

    by_key_series = {(b.key, b.series): b for b in buckets}
    return [
        by_key_series.get((t.key, s.label)) or Bucket(t.key, t.label, 0, s.label)
        for t in order_buckets(list(totals.values()), dim)
        for s in fetch.series
    ]


def _empty(state: AgentState, deps: Deps, notes: list[str], message: str) -> AgentState:
    meta = build_meta(state, deps, notes=notes)
    return {"response": QueryResponse(status=ResponseStatus.NO_RESULTS, message=message,
                                      meta=meta)}


def build_node(state: AgentState, deps: Deps) -> AgentState:
    plan, fetch = state["plan"], state["fetch"]
    notes = list(state.get("notes", []))
    limit = deps.settings.max_citations_per_datum
    studies = [s for series in fetch.series for s in series.studies]

    match plan.analysis:
        case AnalysisKind.TIME_TREND | AnalysisKind.DISTRIBUTION | AnalysisKind.COMPARISON:
            buckets = category_buckets(plan, fetch, notes)
            if not buckets:
                return _empty(state, deps, notes, "Matching trials have no values for this field.")
            viz = build_category_chart(plan, buckets, limit)

        case AnalysisKind.HISTOGRAM:
            assert plan.x_numeric is not None
            points, missing = numeric_values(studies, plan.x_numeric)
            if missing:
                notes.append(f"{missing:,} trials without a value were excluded.")
            if not points:
                return _empty(state, deps, notes, "No matching trials report this value.")
            bins = histogram_bins(points)
            if bins[-1].overflow:
                notes.append(
                    f"The last bin groups the top ~5% of values (≥ {bins[-1].start:,.0f})."
                )
            viz = build_histogram(plan, bins, limit)

        case AnalysisKind.SCATTER:
            assert plan.x_numeric is not None and plan.y_numeric is not None
            points, missing = numeric_values(studies, plan.x_numeric, plan.y_numeric)
            if missing:
                notes.append(f"{missing:,} trials missing either value were excluded.")
            if not points:
                return _empty(state, deps, notes, "No matching trials report both values.")
            viz = build_scatter(plan, points)

        case AnalysisKind.NETWORK:
            net = plan.network
            assert net is not None
            graph = cooccurrence_graph(
                studies, net.source, net.target, net.min_edge_trials, net.max_nodes
            )
            if not graph.edges:
                return _empty(
                    state, deps, notes,
                    f"No pairs co-occur in at least {net.min_edge_trials} trials.",
                )
            notes.append(
                f"Showing {len(graph.nodes)} of {graph.candidate_nodes:,} entities: edges need "
                f"≥ {net.min_edge_trials} shared trials, then the most-connected nodes are kept."
            )
            if {Dimension.INTERVENTION, Dimension.DRUG} & {net.source, net.target}:
                notes.append("Placebo / standard-of-care arms are excluded from the network.")
            viz = build_network(plan, graph, limit)

    meta = build_meta(
        state, deps,
        notes=notes,
        trials_analyzed=fetch.trials_analyzed,
        count_method=fetch.count_method,
        time_granularity="year" if plan.group_by == Dimension.START_YEAR else None,
    )
    return {"response": QueryResponse(status=ResponseStatus.OK, visualization=viz, meta=meta)}
