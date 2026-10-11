"""Extractors + citations against real API records (tests/fixtures/studies_page.json)."""

import pytest

from trialviz.analysis.citations import citations_for, make_citation, verify_citation
from trialviz.ctgov.fields import (
    DIMENSIONS,
    NUMERIC_FIELDS,
    nct_id,
    normalize_person,
    normalize_site,
    pieces_for,
)
from trialviz.schemas.common import Dimension, NumericField


def by_id(studies, nid):
    return next(s for s in studies if nct_id(s) == nid)


def test_phase(studies):
    vals = DIMENSIONS[Dimension.PHASE].extract(by_id(studies, "NCT04344795"))
    assert [v.value for v in vals] == ["PHASE1"]


def test_country_deduplicated_per_trial(studies):
    s = by_id(studies, "NCT06558799")  # 24 sites
    vals = DIMENSIONS[Dimension.COUNTRY].extract(s)
    assert len(vals) == len({v.value for v in vals}) < 24


def test_trial_without_locations_has_no_country(studies):
    assert DIMENSIONS[Dimension.COUNTRY].extract(by_id(studies, "NCT06045767")) == []


def test_interventions_keep_verbatim_excerpt(studies):
    vals = DIMENSIONS[Dimension.INTERVENTION].extract(by_id(studies, "NCT06045767"))
    assert vals[0].excerpt == "Pembrolizumab 50 MG Injection [Keytruda]"
    assert vals[0].value == "pembrolizumab"


def test_start_year(studies):
    s = studies[0]
    (fv,) = DIMENSIONS[Dimension.START_YEAR].extract(s)
    assert len(fv.value) == 4 and fv.excerpt.startswith(fv.value)


def test_enrollment(studies):
    value, fv = NUMERIC_FIELDS[NumericField.ENROLLMENT].extract(by_id(studies, "NCT06558799"))
    assert value == 31.0 and fv.excerpt == "31"


@pytest.mark.parametrize("dim", list(Dimension))
def test_every_citation_verifies_against_source(studies, dim):
    for s in studies:
        for fv in DIMENSIONS[dim].extract(s):
            assert verify_citation(s, make_citation(s, fv)), (dim, fv)


@pytest.mark.parametrize("num", list(NumericField))
def test_every_numeric_citation_verifies_against_source(studies, num):
    for s in studies:
        if (out := NUMERIC_FIELDS[num].extract(s)) is not None:
            assert verify_citation(s, make_citation(s, out[1])), (num, out)


def test_investigators_keep_verbatim_name(studies):
    (fv,) = DIMENSIONS[Dimension.INVESTIGATOR].extract(by_id(studies, "NCT04344795"))
    assert (fv.value, fv.excerpt) == ("samuel whiting", "Samuel Whiting, MD PhD")


def test_self_sponsored_investigator_is_kept(studies):
    s = by_id(studies, "NCT06045767")  # lead sponsor "Ari Raphael" (class OTHER)
    assert [v.value for v in DIMENSIONS[Dimension.INVESTIGATOR].extract(s)] == ["ari raphael"]


def test_official_named_after_industry_sponsor_is_dropped():
    s = {"protocolSection": {
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Boehringer Ingelheim",
                                                       "class": "INDUSTRY"}},
        "contactsLocationsModule": {"overallOfficials": [{"name": "Boehringer Ingelheim"},
                                                         {"name": "Jane Doe, MD"}]},
    }}
    assert [v.value for v in DIMENSIONS[Dimension.INVESTIGATOR].extract(s)] == ["jane doe"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Gregory J. Czarnota, Ph.D. M.D.", "gregory j czarnota"),
        ("Dr. Jane Smith", "jane smith"),
        ("John Smith MD PhD", "john smith"),
        ("Pfizer CT.gov Call Center", ""),
        ("Global Clinical Registry (GCR, 1452)", ""),
        ("Study Director", ""),
        ("Novartis Pharmaceuticals", ""),
        ("MD", ""),
    ],
)
def test_normalize_person(raw, expected):
    assert normalize_person(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dana-Farber Cancer Institute", "dana farber cancer institute"),
        ("Mayo Clinic", "mayo clinic"),
        ("Research Site", ""),
        ("Novo Nordisk Investigational Site", ""),
        ("Local Institution", ""),
        ("For additional information regarding investigative sites, call 1-877-285-4559", ""),
    ],
)
def test_normalize_site(raw, expected):
    assert normalize_site(raw) == expected


def test_age_group_and_has_results(studies):
    s = by_id(studies, "NCT04344795")
    assert [v.value for v in DIMENSIONS[Dimension.AGE_GROUP].extract(s)] == ["ADULT", "OLDER_ADULT"]
    (fv,) = DIMENSIONS[Dimension.HAS_RESULTS].extract(s)
    assert (fv.value, fv.field) == ("false", "hasResults")


def test_duration_months(studies):
    value, fv = NUMERIC_FIELDS[NumericField.DURATION_MONTHS].extract(by_id(studies, "NCT04344795"))
    assert value == 52.0 and fv.excerpt == "2024-09-25"  # 2020-05-06 -> 2024-09-25


def test_duration_skips_completion_before_start():
    s = {"protocolSection": {"statusModule": {"startDateStruct": {"date": "2020-05"},
                                              "completionDateStruct": {"date": "2019-01"}}}}
    assert NUMERIC_FIELDS[NumericField.DURATION_MONTHS].extract(s) is None


def test_tampered_citation_fails_verification(studies):
    s = studies[0]
    (fv,) = DIMENSIONS[Dimension.PHASE].extract(s)
    c = make_citation(s, fv).model_copy(update={"excerpt": "PHASE4"})
    assert not verify_citation(s, c)


def test_citations_for_dedupes_and_limits(studies):
    evidence = [(s, fv) for s in studies for fv in DIMENSIONS[Dimension.INTERVENTION].extract(s)]
    cites = citations_for(evidence, limit=2)
    assert len(cites) == 2 and len({c.nct_id for c in cites}) == 2


def test_pieces_for_is_minimal_and_ordered():
    assert pieces_for([Dimension.PHASE, Dimension.PHASE], [NumericField.ENROLLMENT]) == [
        "NCTId", "BriefTitle", "Phase", "EnrollmentCount",
    ]
