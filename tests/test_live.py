"""Live checks against the real ClinicalTrials.gov API (no LLM). Run: pytest -m live"""

import pytest

from tests.conftest import empty_filters, make_plan
from trialviz.agent.graph import run_query
from trialviz.agent.state import Deps
from trialviz.analysis.citations import verify_citation
from trialviz.ctgov.client import CTGovClient
from trialviz.ctgov.fields import DIMENSIONS
from trialviz.schemas.common import Dimension
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import ResponseStatus

pytestmark = pytest.mark.live


class FakeLLM:
    def __init__(self, plan):
        self.plan = plan

    async def parse(self, **_):
        return self.plan


async def _run(settings, plan):
    async with CTGovClient(settings) as ctgov:
        deps = Deps(settings, FakeLLM(plan), ctgov)
        return await run_query(QueryRequest(query="live test"), deps)


@pytest.mark.parametrize("dim", [Dimension.STATUS, Dimension.HAS_RESULTS])
async def test_single_valued_buckets_sum_to_total(settings, dim):
    """Single-valued, so exact bucket counts must add up to totalCount — proves the enum
    list is complete and the bucket filters don't overlap."""
    resp = await _run(settings, make_plan(group_by=dim,
                                          filters=empty_filters(condition="glioblastoma")))
    assert resp.status == ResponseStatus.OK
    assert sum(r.trial_count for r in resp.visualization.data) == resp.meta.total_matching_trials


@pytest.mark.parametrize("dim", [Dimension.PHASE, Dimension.AGE_GROUP, Dimension.HAS_RESULTS])
async def test_citations_match_independently_fetched_records(settings, dim):
    resp = await _run(settings, make_plan(group_by=dim,
                                          filters=empty_filters(condition="glioblastoma")))
    citations = [c for row in resp.visualization.data for c in row.citations]
    assert citations
    async with CTGovClient(settings) as ctgov:
        for c in citations:
            record = await ctgov._get(f"/studies/{c.nct_id}", {})
            assert verify_citation(record, c), c


@pytest.mark.parametrize("dim", [d for d, s in DIMENSIONS.items() if s.enumerable])
async def test_enum_values_match_registry(settings, dim):
    """Detects API enum drift: a missing value would silently drop trials from exact counts."""
    spec = DIMENSIONS[dim]
    async with CTGovClient(settings) as ctgov:
        (stats,) = await ctgov._get("/stats/field/values", {"fields": spec.area})
    if stats["type"] == "BOOLEAN":  # reported as trueCount / falseCount, not topValues
        api_values = {"true", "false"}
    else:
        api_values = {v["value"] for v in stats["topValues"]}
    assert api_values <= set(spec.enum_values)
