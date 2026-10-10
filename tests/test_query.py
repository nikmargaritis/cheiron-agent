from tests.conftest import empty_filters
from trialviz.ctgov.query import build_params, describe, merge_filters
from trialviz.schemas.common import OverallStatus, Phase


def test_empty_filters_produce_no_params():
    assert build_params(empty_filters()) == {}


def test_text_filters_map_to_query_params():
    p = build_params(empty_filters(condition="breast cancer", intervention="pembrolizumab",
                                   sponsor="Merck", country="Germany", keywords="adjuvant"))
    assert p == {
        "query.cond": "breast cancer",
        "query.intr": "pembrolizumab",
        "query.spons": "Merck",
        "query.locn": "Germany",
        "query.term": "adjuvant",
    }


def test_phases_years_and_extra_terms_are_anded():
    p = build_params(
        empty_filters(phases=[Phase.PHASE2, Phase.PHASE3], start_year_min=2015),
        extra_advanced=["AREA[OverallStatus]RECRUITING"],
    )
    assert p["filter.advanced"] == (
        "AREA[Phase](PHASE2 OR PHASE3) AND AREA[StartDate]RANGE[2015-01-01,MAX] "
        "AND AREA[OverallStatus]RECRUITING"
    )


def test_open_lower_bound_year_range():
    p = build_params(empty_filters(start_year_max=2010))
    assert p["filter.advanced"] == "AREA[StartDate]RANGE[MIN,2010-12-31]"


def test_statuses_comma_joined():
    p = build_params(empty_filters(statuses=[OverallStatus.RECRUITING, OverallStatus.COMPLETED]))
    assert p["filter.overallStatus"] == "RECRUITING,COMPLETED"


def test_merge_override_wins_but_nulls_do_not_erase():
    base = empty_filters(condition="melanoma", intervention="nivolumab")
    merged = merge_filters(base, empty_filters(intervention="Pembrolizumab"))
    assert merged.condition == "melanoma"
    assert merged.intervention == "Pembrolizumab"


def test_describe_drops_empty():
    assert describe(empty_filters(phases=[Phase.PHASE1], condition="x")) == {
        "condition": "x",
        "phases": ["PHASE1"],
    }
