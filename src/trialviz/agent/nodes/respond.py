"""Terminal node for every non-chart outcome, plus the shared metadata builder.

Graceful failure is a first-class response, not an exception: the client always gets
a ``QueryResponse`` with a machine-readable ``status`` and a human ``message``.
"""

from datetime import UTC, datetime
from typing import Any

from trialviz.agent.state import AgentState, Deps
from trialviz.ctgov.query import describe
from trialviz.schemas.plan import PlanStatus
from trialviz.schemas.response import QueryResponse, ResponseMeta, ResponseStatus


def build_meta(state: AgentState, deps: Deps, **overrides: Any) -> ResponseMeta:
    plan = state.get("plan")
    fetch = state.get("fetch")
    filters = state.get("filters")
    fields: dict[str, Any] = dict(
        query=state["request"].query,
        filters=describe(filters) if filters else {},
        plan=plan,
        total_matching_trials=fetch.total_count if fetch else None,
        assumptions=plan.assumptions if plan else [],
        notes=state.get("notes", []),
        api_requests=list(deps.ctgov.request_log),
        timings_ms=state.get("timings_ms", {}),
        generated_at=datetime.now(UTC),
    )
    return ResponseMeta(**{**fields, **overrides})


def respond_node(state: AgentState, deps: Deps) -> AgentState:
    plan = state.get("plan")
    meta = build_meta(state, deps)

    if state.get("plan_errors"):
        status = ResponseStatus.UNSUPPORTED
        message = "Could not build a valid query plan: " + "; ".join(state["plan_errors"])
    elif plan and plan.status == PlanStatus.NEEDS_CLARIFICATION:
        status, message = ResponseStatus.NEEDS_CLARIFICATION, plan.clarification_question
    elif plan and plan.status == PlanStatus.UNSUPPORTED:
        status, message = ResponseStatus.UNSUPPORTED, plan.clarification_question
    else:
        status = ResponseStatus.NO_RESULTS
        message = "No trials on ClinicalTrials.gov match these filters."

    return {"response": QueryResponse(status=status, message=message, meta=meta)}
