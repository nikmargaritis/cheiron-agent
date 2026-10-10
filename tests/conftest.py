import json
from pathlib import Path

import pytest

from trialviz.config import Settings
from trialviz.schemas.plan import AnalysisKind, Filters, PlanStatus, QueryPlan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def studies() -> list[dict]:
    """Three real records: query.intr=pembrolizumab & query.cond=lung cancer."""
    return json.loads((FIXTURES / "studies_page.json").read_text())["studies"]


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, openai_api_key="test", ctgov_max_concurrency=4)


def empty_filters(**kw) -> Filters:
    base = dict.fromkeys(Filters.model_fields)
    return Filters(**{**base, **kw})


def make_plan(**kw) -> QueryPlan:
    base = dict(
        status=PlanStatus.OK,
        rationale="test",
        analysis=AnalysisKind.DISTRIBUTION,
        filters=empty_filters(),
        group_by=None,
        series=[],
        x_numeric=None,
        y_numeric=None,
        network=None,
        top_n=None,
        assumptions=[],
        clarification_question=None,
    )
    return QueryPlan(**{**base, **kw})


def make_study(
    nct: int, *, phases=(), drugs=(), sponsor=None, countries=(), start=None, enrollment=None,
) -> dict:
    """Synthetic study in the exact v2 payload shape. ``drugs``: names or (name, type)."""
    interventions = [
        {"name": d, "type": "DRUG"} if isinstance(d, str) else {"name": d[0], "type": d[1]}
        for d in drugs
    ]
    ps: dict = {
        "identificationModule": {"nctId": f"NCT{nct:08d}", "briefTitle": f"Study {nct}"},
        "designModule": {"phases": list(phases)},
        "armsInterventionsModule": {"interventions": interventions},
        "contactsLocationsModule": {"locations": [{"country": c} for c in countries]},
        "statusModule": {"startDateStruct": {"date": start}} if start else {},
    }
    if sponsor:
        ps["sponsorCollaboratorsModule"] = {"leadSponsor": {"name": sponsor, "class": "INDUSTRY"}}
    if enrollment is not None:
        ps["designModule"]["enrollmentInfo"] = {"count": enrollment}
    return {"protocolSection": ps}
