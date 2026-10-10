from datetime import UTC, datetime

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import TypeAdapter, ValidationError

from trialviz.schemas.plan import QueryPlan
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import (
    BarChart,
    Channel,
    NetworkGraph,
    QueryResponse,
    ResponseMeta,
    ResponseStatus,
    Visualization,
    XYEncoding,
)


def _meta() -> ResponseMeta:
    return ResponseMeta(query="q", generated_at=datetime.now(UTC))


def _bar(rows: list[dict]) -> BarChart:
    return BarChart(
        title="t",
        encoding=XYEncoding(
            x=Channel(field="phase", type="ordinal", title="Phase"),
            y=Channel(field="trial_count", type="quantitative", title="Trials"),
        ),
        data=rows,
    )


class TestRequest:
    def test_minimal(self):
        assert QueryRequest(query="trials by phase").drug_name is None

    def test_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            QueryRequest(query="trials by phase", drug="x")

    def test_rejects_inverted_years(self):
        with pytest.raises(ValidationError, match="start_year"):
            QueryRequest(query="trials by phase", start_year=2020, end_year=2010)

    def test_rejects_bad_phase(self):
        with pytest.raises(ValidationError):
            QueryRequest(query="trials by phase", trial_phase=["PHASE9"])


class TestPlanSchema:
    def test_is_openai_strict_compatible(self):
        """Fails fast (no API key needed) if the plan schema can't be used in strict mode."""
        schema = to_strict_json_schema(QueryPlan)
        assert set(schema["required"]) == set(QueryPlan.model_fields)


class TestVisualization:
    def test_bar_chart_ok(self):
        chart = _bar([{"phase": "Phase 1", "trial_count": 3, "trial_count_unused": 1,
                       "citations": []}])
        assert chart.data[0].trial_count == 3

    def test_bar_chart_missing_encoded_field(self):
        with pytest.raises(ValidationError, match="missing encoded fields"):
            _bar([{"phase": "Phase 1", "trial_count": 3}, {"trial_count": 2}])

    def test_network_edges_must_reference_nodes(self):
        with pytest.raises(ValidationError, match="unknown node"):
            NetworkGraph(
                title="t",
                nodes=[{"id": "a", "label": "A", "group": "drug", "trial_count": 1}],
                edges=[{"source": "a", "target": "b", "trial_count": 1}],
            )

    def test_discriminated_union_round_trip(self):
        chart = _bar([{"phase": "Phase 1", "trial_count": 3}])
        parsed = TypeAdapter(Visualization).validate_json(chart.model_dump_json())
        assert isinstance(parsed, BarChart)


class TestEnvelope:
    def test_ok_requires_visualization(self):
        with pytest.raises(ValidationError):
            QueryResponse(status=ResponseStatus.OK, meta=_meta())

    def test_clarification_has_no_visualization(self):
        r = QueryResponse(
            status=ResponseStatus.NEEDS_CLARIFICATION, message="Which drug?", meta=_meta()
        )
        assert r.visualization is None
