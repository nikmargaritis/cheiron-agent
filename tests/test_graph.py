"""Graph routing with a fake LLM. Exercises every path that doesn't need fetch/build."""

from tests.conftest import make_plan
from trialviz.agent.graph import run_query
from trialviz.agent.state import Deps
from trialviz.ctgov.client import CTGovClient
from trialviz.schemas.common import Dimension, Phase
from trialviz.schemas.plan import AnalysisKind, PlanStatus
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import ResponseStatus


class FakeLLM:
    def __init__(self, *plans):
        self.plans = list(plans)
        self.calls: list[str] = []

    async def parse(self, *, model, system, user, schema):
        self.calls.append(user)
        return self.plans.pop(0)


def deps(settings, llm) -> Deps:
    return Deps(settings=settings, llm=llm, ctgov=CTGovClient(settings))


async def test_clarification_short_circuits(settings):
    llm = FakeLLM(make_plan(status=PlanStatus.NEEDS_CLARIFICATION,
                            clarification_question="Which drug?"))
    req = QueryRequest(query="trials for this drug over time")
    resp = await run_query(req, deps(settings, llm))
    assert resp.status == ResponseStatus.NEEDS_CLARIFICATION
    assert resp.message == "Which drug?"
    assert resp.meta.api_requests == []  # never touched the API
    assert set(resp.meta.timings_ms) == {"plan", "validate", "respond"}


async def test_unsupported(settings):
    llm = FakeLLM(make_plan(status=PlanStatus.UNSUPPORTED,
                            clarification_question="Efficacy isn't in registry data."))
    resp = await run_query(QueryRequest(query="does keytruda work?"), deps(settings, llm))
    assert resp.status == ResponseStatus.UNSUPPORTED


async def test_invalid_plan_is_retried_with_feedback_then_gives_up(settings):
    bad = make_plan(analysis=AnalysisKind.TIME_TREND, group_by=Dimension.PHASE)
    llm = FakeLLM(bad, bad)
    resp = await run_query(QueryRequest(query="trend of trials"), deps(settings, llm))
    assert resp.status == ResponseStatus.UNSUPPORTED
    assert len(llm.calls) == 2
    assert "rejected by the validator" in llm.calls[1]


async def test_caller_fields_override_llm_filters(settings):
    llm = FakeLLM(make_plan(status=PlanStatus.NEEDS_CLARIFICATION, clarification_question="?"))
    req = QueryRequest(query="phases?", drug_name="Pembrolizumab", trial_phase=[Phase.PHASE3])
    resp = await run_query(req, deps(settings, llm))
    assert resp.meta.filters == {"intervention": "Pembrolizumab", "phases": ["PHASE3"]}
    assert "Pembrolizumab" in llm.calls[0]
