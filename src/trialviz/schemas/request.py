"""Public request schema for ``POST /v1/query``.

Structured fields are optional. When present they take precedence over whatever
the planner extracts from ``query`` — the LLM may *add* filters, but it can never
override a filter the caller set explicitly.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trialviz.schemas.common import OverallStatus, Phase


class QueryRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "query": "How has the number of trials for this drug changed over time?",
                    "drug_name": "Pembrolizumab",
                }
            ]
        },
    )

    query: str = Field(
        ..., min_length=3, max_length=1000, description="Natural-language question."
    )

    drug_name: str | None = Field(
        None, max_length=200, description="Intervention/drug name (maps to query.intr)."
    )
    condition: str | None = Field(
        None, max_length=200, description="Condition/disease (maps to query.cond)."
    )
    sponsor: str | None = Field(
        None, max_length=200, description="Lead sponsor or collaborator (query.spons)."
    )
    country: str | None = Field(
        None, max_length=100, description="Country with at least one trial site."
    )
    trial_phase: list[Phase] | None = Field(
        None, description="Restrict to these phases (OR-ed together)."
    )
    status: list[OverallStatus] | None = Field(
        None, description="Restrict to these overall statuses (OR-ed together)."
    )
    start_year: int | None = Field(
        None, ge=1900, le=2100, description="Earliest trial start year (inclusive)."
    )
    end_year: int | None = Field(
        None, ge=1900, le=2100, description="Latest trial start year (inclusive)."
    )

    @model_validator(mode="after")
    def _check_year_range(self) -> Self:
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be <= end_year")
        return self
