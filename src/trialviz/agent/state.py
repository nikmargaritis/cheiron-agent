"""LangGraph state and injected dependencies."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from trialviz.agent.llm import StructuredLLM
from trialviz.config import Settings
from trialviz.ctgov.client import CTGovClient
from trialviz.schemas.plan import Filters, QueryPlan
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import QueryResponse


@dataclass
class Deps:
    settings: Settings
    llm: StructuredLLM
    ctgov: CTGovClient


@dataclass
class SeriesData:
    """Everything fetched for one series (a comparison side, or the whole query)."""

    label: str | None  # None when the plan has no comparison series
    filters: Filters
    total_count: int
    studies: list[dict[str, Any]] = field(default_factory=list)
    # Exact server-side counts per bucket value (exact strategy only). When set, these
    # are authoritative and ``studies`` is only the citation pool.
    exact_counts: dict[str, int] | None = None

    @property
    def exact(self) -> bool:
        return self.exact_counts is not None or len(self.studies) >= self.total_count


@dataclass
class FetchResult:
    """Raw material for aggregation, produced by the fetch node."""

    series: list[SeriesData]

    @property
    def total_count(self) -> int:
        return sum(s.total_count for s in self.series)

    @property
    def trials_analyzed(self) -> int:
        return sum(
            s.total_count if s.exact_counts is not None else len(s.studies) for s in self.series
        )

    @property
    def count_method(self) -> Literal["exact", "sample"]:
        return "exact" if all(s.exact for s in self.series) else "sample"


class AgentState(TypedDict, total=False):
    request: QueryRequest
    plan: QueryPlan
    plan_errors: list[str]  # validation feedback, fed back to the planner on retry
    plan_attempts: int
    filters: Filters  # effective base filters after merging caller overrides
    fetch: FetchResult
    response: QueryResponse
    notes: list[str]
    timings_ms: dict[str, float]
