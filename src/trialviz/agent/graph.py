"""LangGraph wiring.

    plan ──► validate ──ok──► fetch ──results──► build ──► END
     ▲          │                 │
     └─retry────┤                 └─no results─┐
                └─clarify / unsupported / gave up ──► respond ──► END

Only ``plan`` uses an LLM. ``validate`` can send the plan back once with concrete
error messages before giving up.
"""

import time
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from langgraph.graph import END, START, StateGraph

from trialviz.agent.nodes.build import build_node
from trialviz.agent.nodes.fetch import fetch_node
from trialviz.agent.nodes.plan import plan_node
from trialviz.agent.nodes.respond import respond_node
from trialviz.agent.nodes.validate import validate_node
from trialviz.agent.state import AgentState, Deps
from trialviz.schemas.plan import PlanStatus
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import QueryResponse

MAX_PLAN_ATTEMPTS = 2


def _timed(name: str, fn: Callable[..., Any], *args: Any) -> Callable[[AgentState], Awaitable]:
    """Inject deps and record per-node wall time into state['timings_ms']."""

    async def node(state: AgentState) -> AgentState:
        t0 = time.perf_counter()
        out = fn(state, *args)
        if isawaitable(out):
            out = await out
        timings = {**state.get("timings_ms", {}), name: (time.perf_counter() - t0) * 1000}
        return {**out, "timings_ms": timings}

    return node


def _after_validate(state: AgentState) -> str:
    if state["plan_errors"]:
        return "plan" if state.get("plan_attempts", 0) < MAX_PLAN_ATTEMPTS else "respond"
    return "fetch" if state["plan"].status == PlanStatus.OK else "respond"


def _after_fetch(state: AgentState) -> str:
    return "build" if state["fetch"].total_count > 0 else "respond"


def build_graph(deps: Deps):
    g = StateGraph(AgentState)
    g.add_node("plan", _timed("plan", plan_node, deps))
    g.add_node("validate", _timed("validate", validate_node))
    g.add_node("fetch", _timed("fetch", fetch_node, deps))
    g.add_node("build", _timed("build", build_node, deps))
    g.add_node("respond", _timed("respond", respond_node, deps))

    g.add_edge(START, "plan")
    g.add_edge("plan", "validate")
    g.add_conditional_edges("validate", _after_validate, ["plan", "fetch", "respond"])
    g.add_conditional_edges("fetch", _after_fetch, ["build", "respond"])
    g.add_edge("build", END)
    g.add_edge("respond", END)
    return g.compile()


async def run_query(request: QueryRequest, deps: Deps) -> QueryResponse:
    final = await build_graph(deps).ainvoke({"request": request})
    return final["response"]
