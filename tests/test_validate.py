import pytest

from tests.conftest import empty_filters, make_plan
from trialviz.agent.nodes.validate import check_plan
from trialviz.schemas.common import Dimension, NumericField
from trialviz.schemas.plan import AnalysisKind, NetworkSpec, PlanStatus, SeriesSpec


def series(label: str) -> SeriesSpec:
    return SeriesSpec(label=label, filters=empty_filters(intervention=label))


@pytest.mark.parametrize(
    "plan",
    [
        make_plan(analysis=AnalysisKind.DISTRIBUTION, group_by=Dimension.PHASE),
        make_plan(analysis=AnalysisKind.TIME_TREND, group_by=Dimension.START_YEAR),
        make_plan(analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE,
                  series=[series("a"), series("b")]),
        make_plan(analysis=AnalysisKind.HISTOGRAM, x_numeric=NumericField.ENROLLMENT),
        make_plan(analysis=AnalysisKind.NETWORK, network=NetworkSpec(
            source=Dimension.SPONSOR, target=Dimension.INTERVENTION,
            min_edge_trials=2, max_nodes=40)),
        make_plan(status=PlanStatus.UNSUPPORTED, analysis=AnalysisKind.DISTRIBUTION),
    ],
)
def test_valid_plans(plan):
    assert check_plan(plan) == []


@pytest.mark.parametrize(
    ("plan", "fragment"),
    [
        (make_plan(analysis=AnalysisKind.TIME_TREND, group_by=Dimension.PHASE), "start_year"),
        (make_plan(analysis=AnalysisKind.DISTRIBUTION), "requires group_by"),
        (make_plan(analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE,
                   series=[series("a")]), "2-6 series"),
        (make_plan(analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE,
                   series=[SeriesSpec(label="a", filters=empty_filters()), series("b")]),
         "series 'a' has no filters"),
        (make_plan(analysis=AnalysisKind.COMPARISON, group_by=Dimension.PHASE,
                   series=[series("a"), series("a")]), "different filters"),
        (make_plan(analysis=AnalysisKind.SCATTER, x_numeric=NumericField.ENROLLMENT),
         "y_numeric"),
        (make_plan(analysis=AnalysisKind.NETWORK), "network spec"),
        (make_plan(group_by=Dimension.PHASE, top_n=500), "top_n"),
        (make_plan(group_by=Dimension.PHASE,
                   filters=empty_filters(start_year_min=2020, start_year_max=2010)),
         "start_year_min > start_year_max"),
    ],
)
def test_invalid_plans(plan, fragment):
    errors = check_plan(plan)
    assert any(fragment in e for e in errors), errors
