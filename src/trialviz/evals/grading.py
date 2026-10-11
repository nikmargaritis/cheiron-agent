"""Eval case definition, the planner-only runner, and plan grading.

A case lists only what it cares about. Expected filter values may be a tuple of
acceptable alternatives, e.g. ``("type 2 diabetes", "type 2 diabetes mellitus")``.
Filter fields a case doesn't mention must be left empty, so invented filters fail
(unless the field is listed in ``ignore``).
"""

from dataclasses import dataclass, field
from itertools import permutations
from typing import Any

from trialviz.agent.graph import MAX_PLAN_ATTEMPTS
from trialviz.agent.nodes.plan import plan_node
from trialviz.agent.nodes.validate import validate_node
from trialviz.agent.state import AgentState, Deps
from trialviz.ctgov.query import merge_filters
from trialviz.schemas.common import Dimension, NumericField
from trialviz.schemas.plan import AnalysisKind, Filters, PlanStatus, QueryPlan
from trialviz.schemas.request import QueryRequest


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    tags: tuple[str, ...] = ()
    request: dict[str, Any] = field(default_factory=dict)  # extra QueryRequest fields
    status: PlanStatus = PlanStatus.OK
    analysis: AnalysisKind | None = None
    group_by: Dimension | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    series: list[dict[str, Any]] | None = None  # per-series filters, any order
    numeric: set[NumericField] | None = None  # {x} for histogram, {x, y} for scatter
    network: set[Dimension] | None = None  # {source, target}; one value if source == target
    top_n: int | None = None
    ignore: frozenset[str] = frozenset()  # filter fields allowed to take any value

    def to_request(self) -> QueryRequest:
        return QueryRequest(query=self.query, **self.request)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


async def plan_query(request: QueryRequest, deps: Deps) -> AgentState:
    """Run only the graph's plan -> validate loop, with the same retry rule."""
    state: AgentState = {"request": request}
    while True:
        state |= await plan_node(state, deps)
        state |= validate_node(state)
        if not state["plan_errors"] or state["plan_attempts"] >= MAX_PLAN_ATTEMPTS:
            return state


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        return " ".join(v.casefold().replace("’", "'").split())
    if isinstance(v, list):
        return frozenset(_norm(x) for x in v) or None
    return v


def _show(v: Any) -> str:
    if isinstance(v, tuple):
        return " | ".join(_show(x) for x in v)
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return repr(v) if isinstance(v, str) else str(v)


def value_matches(actual: Any, expected: Any) -> bool:
    options = expected if isinstance(expected, tuple) else (expected,)
    return _norm(actual) in {_norm(o) for o in options}


def filter_problems(actual: Filters, expected: dict[str, Any], ignore=frozenset()) -> list[str]:
    problems = []
    for name in Filters.model_fields:
        if name in ignore:
            continue
        got = getattr(actual, name)
        if name in expected:
            if not value_matches(got, expected[name]):
                problems.append(f"{name}: got {_show(got)}, expected {_show(expected[name])}")
        elif _norm(got) is not None:
            problems.append(f"{name}: unexpected {_show(got)}")
    return problems


def series_problems(plan: QueryPlan, case: Case) -> list[str]:
    """Compare each series' effective filters (base + series), in any order.

    Shared filters may legitimately sit in the base or be repeated in every series.
    """
    expected = [{**case.filters, **s} for s in case.series or []]
    actual = [merge_filters(plan.filters, s.filters) for s in plan.series]
    if len(actual) != len(expected):
        return [f"expected {len(expected)} series, got {len(actual)}"]

    best: list[str] | None = None
    for order in permutations(actual):
        problems = [
            f"series[{i}] {p}"
            for i, (got, want) in enumerate(zip(order, expected, strict=True))
            for p in filter_problems(got, want, case.ignore)
        ]
        if best is None or len(problems) < len(best):
            best = problems
    return best or []


def grade(case: Case, plan: QueryPlan, plan_errors: list[str]) -> list[Check]:
    checks = [Check("status", plan.status == case.status, f"got {plan.status}")]
    if not checks[0].passed or case.status != PlanStatus.OK:
        return checks  # the remaining checks are meaningless for a wrong or non-ok status

    checks.append(Check("valid", not plan_errors, "; ".join(plan_errors)))
    if case.analysis:
        checks.append(Check("analysis", plan.analysis == case.analysis, f"got {plan.analysis}"))
    if case.group_by:
        checks.append(Check("group_by", plan.group_by == case.group_by, f"got {plan.group_by}"))

    if case.series is None:
        problems = filter_problems(plan.filters, case.filters, case.ignore)
        checks.append(Check("filters", not problems, "; ".join(problems)))
    else:
        problems = series_problems(plan, case)
        checks.append(Check("series", not problems, "; ".join(problems)))

    if case.numeric:
        got = {v for v in (plan.x_numeric, plan.y_numeric) if v}
        checks.append(Check("numeric", got == case.numeric, f"got {sorted(got)}"))
    if case.network:
        got = {plan.network.source, plan.network.target} if plan.network else set()
        checks.append(Check("network", got == case.network, f"got {sorted(got)}"))
    if case.top_n:
        checks.append(Check("top_n", plan.top_n == case.top_n, f"got {plan.top_n}"))
    return checks
