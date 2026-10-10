"""Extractors + citations against real API records (tests/fixtures/studies_page.json)."""

import pytest

from trialviz.analysis.citations import citations_for, make_citation, verify_citation
from trialviz.ctgov.fields import DIMENSIONS, NUMERIC_FIELDS, nct_id, pieces_for
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
