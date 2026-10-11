"""Planner eval grading and case definitions (offline)."""

from tests.conftest import empty_filters, make_plan
from tests.test_graph import FakeLLM, deps
from trialviz.evals.cases import CASES
from trialviz.evals.grading import Case, grade, plan_query
from trialviz.schemas.common import Dimension, Phase
from trialviz.schemas.plan import AnalysisKind, Filters, PlanStatus, SeriesSpec

TREND = Case("t", "pembrolizumab trend", analysis=AnalysisKind.TIME_TREND,
             group_by=Dimension.START_YEAR, filters={"intervention": "pembrolizumab"})


def trend_plan(**filters):
    return make_plan(analysis=AnalysisKind.TIME_TREND, group_by=Dimension.START_YEAR,
                     filters=empty_filters(**filters))


def failed(checks) -> dict[str, str]:
    return {c.name: c.detail for c in checks if not c.passed}


def test_cases_are_well_formed():
    assert len({c.id for c in CASES}) == len(CASES)
    for c in CASES:
        c.to_request()  # caller fields validate
        for f in [c.filters, *(c.series or [])]:
            assert set(f) <= set(Filters.model_fields), c.id
        assert (c.series is not None) == (c.analysis == AnalysisKind.COMPARISON), c.id
        if c.status != PlanStatus.OK:
            assert not (c.analysis or c.filters or c.series), c.id


def test_matching_plan_passes_case_insensitively():
    assert failed(grade(TREND, trend_plan(intervention="Pembrolizumab"), [])) == {}


def test_wrong_and_invented_filters_fail():
    plan = trend_plan(intervention="Keytruda", keywords="oncology")
    detail = failed(grade(TREND, plan, []))["filters"]
    assert "intervention: got 'Keytruda', expected 'pembrolizumab'" in detail
    assert "keywords: unexpected 'oncology'" in detail


def test_ignored_field_and_alternatives():
    case = Case("t", "q", analysis=AnalysisKind.TIME_TREND, ignore=frozenset({"keywords"}),
                filters={"intervention": "pembrolizumab", "start_year_min": (2015, 2016)})
    plan = trend_plan(intervention="pembrolizumab", start_year_min=2016, keywords="x")
    assert failed(grade(case, plan, [])) == {}


def test_list_filters_compare_as_sets():
    case = Case("t", "q", filters={"phases": [Phase.PHASE2, Phase.PHASE3]})
    plan = make_plan(filters=empty_filters(phases=[Phase.PHASE3, Phase.PHASE2]))
    assert failed(grade(case, plan, [])) == {}


def test_wrong_status_skips_other_checks():
    plan = make_plan(status=PlanStatus.NEEDS_CLARIFICATION, clarification_question="?")
    checks = grade(TREND, plan, [])
    assert [c.name for c in checks] == ["status"] and not checks[0].passed


def test_validator_errors_fail_the_valid_check():
    assert "valid" in failed(grade(TREND, trend_plan(intervention="pembrolizumab"), ["bad"]))


def test_series_match_in_any_order_with_shared_filters_anywhere():
    case = Case("t", "q", analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE,
                filters={"condition": "melanoma"},
                series=[{"intervention": "nivolumab"}, {"intervention": "ipilimumab"}])
    plan = make_plan(
        analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE,
        series=[  # shared condition repeated per series instead of in the base filters
            SeriesSpec(label="I", filters=empty_filters(intervention="ipilimumab",
                                                        condition="Melanoma")),
            SeriesSpec(label="N", filters=empty_filters(intervention="nivolumab",
                                                        condition="Melanoma")),
        ],
    )
    assert failed(grade(case, plan, [])) == {}

    plan.series.pop()
    assert failed(grade(case, plan, []))["series"] == "expected 2 series, got 1"


async def test_plan_query_retries_like_the_graph(settings):
    bad = make_plan(analysis=AnalysisKind.TIME_TREND, group_by=Dimension.PHASE)
    llm = FakeLLM(bad, trend_plan())
    state = await plan_query(TREND.to_request(), deps(settings, llm))
    assert state["plan_attempts"] == 2 and state["plan_errors"] == []
