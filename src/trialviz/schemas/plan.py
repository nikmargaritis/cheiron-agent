"""The QueryPlan: the *only* thing the LLM produces.

The planner LLM translates a natural-language question into this constrained,
enum-heavy structure. Everything downstream (API calls, counting, chart building,
citations) is deterministic code driven by the plan. The LLM never sees or emits
data values, so it has no opportunity to hallucinate them.

These models are sent to OpenAI structured outputs (strict mode), so:
  * every field is required (nullable instead of defaulted),
  * no numeric/length constraints here — those are enforced afterwards by
    ``trialviz.agent.nodes.validate``, which can produce a useful error message.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from trialviz.schemas.common import Dimension, NumericField, OverallStatus, Phase


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filters(_Strict):
    """Filters that narrow the set of trials. Maps 1:1 onto API query params."""

    condition: str | None = Field(description="Disease/condition, e.g. 'breast cancer'.")
    intervention: str | None = Field(description="Drug or intervention name.")
    sponsor: str | None = Field(description="Sponsor organisation name.")
    country: str | None = Field(description="Country with at least one site.")
    phases: list[Phase] | None
    statuses: list[OverallStatus] | None
    start_year_min: int | None
    start_year_max: int | None
    keywords: str | None = Field(
        description="Free-text fallback for concepts not covered by other fields."
    )


class AnalysisKind(StrEnum):
    TIME_TREND = "time_trend"  # count of trials per start year            -> time_series
    DISTRIBUTION = "distribution"  # count of trials per category (incl. geo) -> bar_chart
    COMPARISON = "comparison"  # distribution split by 2+ series          -> grouped_bar_chart
    HISTOGRAM = "histogram"  # distribution of a per-trial numeric value -> histogram
    SCATTER = "scatter"  # one point per trial, two numeric values    -> scatter_plot
    NETWORK = "network"  # co-occurrence of two dimensions on trials  -> network_graph


class PlanStatus(StrEnum):
    OK = "ok"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class SeriesSpec(_Strict):
    """One side of a comparison, e.g. 'Drug A' vs 'Drug B'.

    Its filters are layered on top of the plan's base filters.
    """

    label: str
    filters: Filters


class NetworkSpec(_Strict):
    """Bipartite (source != target) or co-occurrence (source == target) network.

    Nodes are dimension values; an edge links two values that appear on the same
    trial, weighted by the number of such trials.
    """

    source: Dimension
    target: Dimension
    min_edge_trials: int = Field(description="Drop edges supported by fewer trials.")
    max_nodes: int = Field(description="Keep only the most-connected nodes.")


class QueryPlan(_Strict):
    status: PlanStatus
    rationale: str = Field(description="One or two sentences explaining the interpretation.")
    analysis: AnalysisKind
    filters: Filters
    group_by: Dimension | None = Field(
        description="Category axis for distribution/comparison; start_year for time_trend."
    )
    series: list[SeriesSpec] = Field(
        description="Two or more series for comparison; empty otherwise."
    )
    x_numeric: NumericField | None = Field(description="Value for histogram, x for scatter.")
    y_numeric: NumericField | None = Field(description="y for scatter only.")
    network: NetworkSpec | None
    top_n: int | None = Field(description="Limit categories to the N largest.")
    assumptions: list[str] = Field(description="Interpretation choices the user should know.")
    clarification_question: str | None = Field(
        description="Set when status is needs_clarification or unsupported."
    )
