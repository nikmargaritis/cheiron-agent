"""End-to-end graph runs: fake LLM plan + respx-mocked ClinicalTrials.gov API.

The mock answers from an in-memory corpus, interpreting the same params the real API
gets (bucket filters, pageSize, countTotal), so these tests exercise both ingestion
strategies, aggregation, chart building, and citation integrity together.
"""

import re

import httpx
import pytest
import respx

from tests.conftest import empty_filters, make_plan, make_study
from trialviz.agent.graph import run_query
from trialviz.agent.state import Deps
from trialviz.analysis.citations import verify_citation
from trialviz.ctgov.client import CTGovClient
from trialviz.ctgov.fields import nct_id
from trialviz.schemas.common import Dimension, NumericField
from trialviz.schemas.plan import AnalysisKind, NetworkSpec, SeriesSpec
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import ResponseStatus

BASE = "https://clinicaltrials.gov/api/v2"

CORPUS = [
    make_study(1, phases=["PHASE1"], drugs=["Gemcitabine", "Nab-paclitaxel"], sponsor="Celgene",
               countries=["United States"], start="2019-03-01", enrollment=40),
    make_study(2, phases=["PHASE1", "PHASE2"], drugs=["Gemcitabine HCl", "nab paclitaxel"],
               sponsor="Celgene", countries=["United States", "Canada"], start="2020-05",
               enrollment=120),
    make_study(3, phases=["PHASE3"], drugs=["Gemcitabine", "Cisplatin"], sponsor="NCI",
               countries=["Canada"], start="2020-01-01", enrollment=600),
    make_study(4, phases=["PHASE3"], drugs=["Oxaliplatin", "Irinotecan"], sponsor="NCI",
               start="2022-07-01", enrollment=350),
]


class FakeLLM:
    def __init__(self, plan):
        self.plan = plan

    async def parse(self, **_):
        return self.plan


def _matches(study: dict, advanced: str) -> bool:
    """Tiny interpreter for the AREA[...] terms our code emits."""
    ps = study["protocolSection"]
    for term in filter(None, advanced.split(" AND ")):
        if m := re.fullmatch(r"AREA\[Phase\](\w+)", term):
            if m[1] not in ps["designModule"]["phases"]:
                return False
        elif m := re.fullmatch(r"AREA\[StartDate\]RANGE\[(\S+),(\S+)\]", term):
            d = ps["statusModule"].get("startDateStruct", {}).get("date", "")
            lo, hi = m[1], m[2]
            if not d or (lo != "MIN" and d < lo) or (hi != "MAX" and d[:10] > hi):
                return False
        elif term == "AREA[StartDate]MISSING":
            if ps["statusModule"].get("startDateStruct"):
                return False
        else:
            raise AssertionError(f"unexpected filter term {term!r}")
    return True


def _api(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    hits = [s for s in CORPUS if _matches(s, p.get("filter.advanced", ""))]
    if sort := p.get("sort"):
        hits.sort(key=lambda s: s["protocolSection"]["statusModule"]
                  .get("startDateStruct", {}).get("date", ""), reverse=sort.endswith(":desc"))
    page = hits[: int(p.get("pageSize", 10))]
    return httpx.Response(200, json={"totalCount": len(hits), "studies": page})


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/studies").mock(side_effect=_api)
        yield mock


async def run(settings, plan):
    async with CTGovClient(settings) as ctgov:
        return await run_query(QueryRequest(query="test question"),
                               Deps(settings, FakeLLM(plan), ctgov))


def assert_citations_verify(resp):
    by_id = {nct_id(s): s for s in CORPUS}
    viz = resp.visualization
    items = viz.nodes + viz.edges if viz.type == "network_graph" else viz.data
    n = 0
    for item in items:
        for c in item.citations:
            assert verify_citation(by_id[c.nct_id], c), c
            n += 1
    assert n > 0


async def test_phase_distribution_uses_exact_bucket_counts(settings, api):
    resp = await run(settings, make_plan(group_by=Dimension.PHASE))
    assert resp.status == ResponseStatus.OK
    v = resp.visualization
    assert v.type == "bar_chart"
    counts = {r.phase: r.trial_count for r in v.data}
    assert counts == {"Early Phase 1": 0, "Phase 1": 2, "Phase 2": 1, "Phase 3": 2,
                      "Phase 4": 0, "Not applicable": 0}
    assert [r.phase for r in v.data][:2] == ["Early Phase 1", "Phase 1"]  # phase order
    assert resp.meta.count_method == "exact"
    assert resp.meta.total_matching_trials == 4
    assert any("counted under each" in n for n in resp.meta.notes)
    assert_citations_verify(resp)


async def test_time_trend_fills_years_and_reports_missing(settings, api):
    CORPUS.append(make_study(99, phases=["PHASE2"]))  # no start date
    try:
        resp = await run(settings, make_plan(analysis=AnalysisKind.TIME_TREND,
                                             group_by=Dimension.START_YEAR))
    finally:
        CORPUS.pop()
    v = resp.visualization
    assert v.type == "time_series"
    assert [(r.start_year, r.trial_count) for r in v.data] == [
        (2019, 1), (2020, 2), (2021, 0), (2022, 1)]
    assert any("1 trials have no start date" in n for n in resp.meta.notes)
    assert_citations_verify(resp)


async def test_comparison_grid_is_complete(settings, api):
    plan = make_plan(analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE, series=[
        SeriesSpec(label="A", filters=empty_filters(intervention="a")),
        SeriesSpec(label="B", filters=empty_filters(intervention="b")),
    ])
    resp = await run(settings, plan)
    v = resp.visualization
    assert v.type == "grouped_bar_chart"
    assert len(v.data) == 6 * 2  # every phase × every series, zeros included
    assert v.encoding.color.field == "series"


async def test_open_ended_dimension_downloads_and_counts(settings, api):
    resp = await run(settings, make_plan(group_by=Dimension.DRUG, top_n=2))
    v = resp.visualization
    assert [(r.drug, r.trial_count) for r in v.data] == [("Gemcitabine", 3),
                                                          ("Nab-paclitaxel", 2)]
    assert v.orientation == "horizontal"
    assert any("top 2 of 5" in n for n in resp.meta.notes)
    assert_citations_verify(resp)


async def test_histogram(settings, api):
    resp = await run(settings, make_plan(analysis=AnalysisKind.HISTOGRAM,
                                         x_numeric=NumericField.ENROLLMENT))
    v = resp.visualization
    assert v.type == "histogram"
    assert sum(r.trial_count for r in v.data) == 4
    assert_citations_verify(resp)


async def test_scatter(settings, api):
    resp = await run(settings, make_plan(analysis=AnalysisKind.SCATTER,
                                         x_numeric=NumericField.START_YEAR,
                                         y_numeric=NumericField.ENROLLMENT))
    v = resp.visualization
    assert v.type == "scatter_plot" and len(v.data) == 4
    assert_citations_verify(resp)


async def test_drug_network(settings, api):
    resp = await run(settings, make_plan(
        analysis=AnalysisKind.NETWORK,
        network=NetworkSpec(source=Dimension.DRUG, target=Dimension.DRUG,
                            min_edge_trials=2, max_nodes=10)))
    v = resp.visualization
    assert v.type == "network_graph"
    assert [(e.source, e.target, e.trial_count) for e in v.edges] == [
        ("drug:gemcitabine", "drug:nab paclitaxel", 2)]
    assert_citations_verify(resp)


async def test_network_with_no_edges_is_no_results(settings, api):
    resp = await run(settings, make_plan(
        analysis=AnalysisKind.NETWORK,
        network=NetworkSpec(source=Dimension.DRUG, target=Dimension.DRUG,
                            min_edge_trials=50, max_nodes=10)))
    assert resp.status == ResponseStatus.NO_RESULTS
    assert "at least 50" in resp.message


async def test_zero_matches_is_no_results(settings, api):
    resp = await run(settings, make_plan(
        group_by=Dimension.PHASE,
        filters=empty_filters(start_year_min=2030)))
    assert resp.status == ResponseStatus.NO_RESULTS
    assert resp.visualization is None
