"""Node 1 — plan (LLM). Natural language → QueryPlan.

The only node that calls an LLM on the critical path. Its output is a constrained
structure (enums + filter strings), never data.
"""

from trialviz.agent.prompts import PLANNER_SYSTEM, planner_user_message
from trialviz.agent.state import AgentState, Deps
from trialviz.ctgov.query import merge_filters
from trialviz.schemas.plan import Filters, QueryPlan
from trialviz.schemas.request import QueryRequest


def request_filters(req: QueryRequest) -> Filters:
    """Caller-supplied structured fields, expressed as plan Filters."""
    return Filters(
        condition=req.condition,
        intervention=req.drug_name,
        sponsor=req.sponsor,
        country=req.country,
        phases=req.trial_phase,
        statuses=req.status,
        start_year_min=req.start_year,
        start_year_max=req.end_year,
        keywords=None,
    )


async def plan_node(state: AgentState, deps: Deps) -> AgentState:
    req = state["request"]
    structured = req.model_dump(exclude={"query"}, exclude_none=True, mode="json")

    plan = await deps.llm.parse(
        model=deps.settings.planner_model,
        system=PLANNER_SYSTEM,
        user=planner_user_message(req.query, structured, state.get("plan_errors")),
        schema=QueryPlan,
    )

    # Caller-supplied fields always win over LLM extraction.
    filters = merge_filters(plan.filters, request_filters(req))
    plan = plan.model_copy(update={"filters": filters})

    return {
        "plan": plan,
        "filters": filters,
        "plan_attempts": state.get("plan_attempts", 0) + 1,
    }
