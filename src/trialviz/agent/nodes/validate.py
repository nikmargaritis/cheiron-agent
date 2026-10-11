"""Node 2 — validate (deterministic). Semantic checks the JSON schema can't express.

Structured outputs guarantee the plan *parses*; this node checks it *makes sense*.
Errors are fed back to the planner for one retry; after that the request ends with
an explanatory status instead of a guessed chart.
"""

from datetime import date

from trialviz.agent.state import AgentState
from trialviz.ctgov.query import describe
from trialviz.schemas.common import Dimension
from trialviz.schemas.plan import AnalysisKind, PlanStatus, QueryPlan

MAX_TOP_N = 50
MAX_SERIES = 6
MAX_NETWORK_NODES = 150


def check_plan(plan: QueryPlan) -> list[str]:
    if plan.status != PlanStatus.OK:
        return []

    errs: list[str] = []
    a = plan.analysis
    f = plan.filters

    if a == AnalysisKind.TIME_TREND and plan.group_by != Dimension.START_YEAR:
        errs.append("time_trend requires group_by = start_year")
    if a in (AnalysisKind.DISTRIBUTION, AnalysisKind.COMPARISON) and plan.group_by is None:
        errs.append(f"{a} requires group_by")
    if a == AnalysisKind.COMPARISON and not 2 <= len(plan.series) <= MAX_SERIES:
        errs.append(f"comparison requires 2-{MAX_SERIES} series, got {len(plan.series)}")
    if a == AnalysisKind.COMPARISON:
        # Labels alone don't filter anything: every series would count the same trials.
        series_filters = [describe(s.filters) for s in plan.series]
        for s, sf in zip(plan.series, series_filters, strict=True):
            if not sf:
                errs.append(f"series '{s.label}' has no filters; put what distinguishes it "
                            "(e.g. its intervention or condition) in its filters")
        if any(sf and series_filters.count(sf) > 1 for sf in series_filters):
            errs.append("comparison series must have different filters")
    if a != AnalysisKind.COMPARISON and plan.series:
        errs.append("series is only allowed for comparison")
    if a == AnalysisKind.HISTOGRAM and plan.x_numeric is None:
        errs.append("histogram requires x_numeric")
    if a == AnalysisKind.SCATTER and (plan.x_numeric is None or plan.y_numeric is None):
        errs.append("scatter requires x_numeric and y_numeric")
    if a == AnalysisKind.SCATTER and plan.x_numeric and plan.x_numeric == plan.y_numeric:
        errs.append("scatter needs two different numeric fields")
    if a == AnalysisKind.NETWORK and plan.network is None:
        errs.append("network requires a network spec")

    this_year = date.today().year
    for name, yr in (("start_year_min", f.start_year_min), ("start_year_max", f.start_year_max)):
        if yr is not None and not 1900 <= yr <= this_year + 10:
            errs.append(f"{name}={yr} is out of range")
    if f.start_year_min and f.start_year_max and f.start_year_min > f.start_year_max:
        errs.append("start_year_min > start_year_max")

    if plan.top_n is not None and not 1 <= plan.top_n <= MAX_TOP_N:
        errs.append(f"top_n must be between 1 and {MAX_TOP_N}")
    if plan.network and not 2 <= plan.network.max_nodes <= MAX_NETWORK_NODES:
        errs.append(f"network.max_nodes must be between 2 and {MAX_NETWORK_NODES}")
    if plan.network and plan.network.min_edge_trials < 1:
        errs.append("network.min_edge_trials must be >= 1")
    return errs


def validate_node(state: AgentState) -> AgentState:
    return {"plan_errors": check_plan(state["plan"])}
