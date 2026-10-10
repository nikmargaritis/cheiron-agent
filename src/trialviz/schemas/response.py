"""Public response schema for ``POST /v1/query``.

Design notes for frontend implementers
--------------------------------------
* ``visualization`` is a discriminated union on ``type``. Switch on it.
* Tabular charts carry ``data`` as a list of flat rows. ``encoding`` tells you
  which row key feeds which visual channel; every key referenced by ``encoding``
  is guaranteed (validated server-side) to exist on every row.
* Every row/node/edge carries ``citations`` (up to N source records, each with the
  NCT ID, the API field path, and the verbatim value read from that field) and
  ``trial_count`` (the full number of trials behind the datum — citations may be a
  subset).
* ``network_graph`` uses ``nodes``/``edges`` instead of ``data``.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trialviz.schemas.plan import QueryPlan

FieldType = Literal["nominal", "ordinal", "quantitative", "temporal"]
Scalar = str | int | float | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(_Strict):
    nct_id: str = Field(pattern=r"^NCT\d{8}$")
    field: str = Field(description="API field path, e.g. protocolSection.designModule.phases")
    excerpt: str = Field(description="Verbatim value of `field` in the API response.")
    title: str | None = Field(None, description="Trial briefTitle, for human context.")
    url: str = Field(description="Link to the trial record on clinicaltrials.gov.")


class Provenance(BaseModel):
    """Mixed into every datum. Extra keys (the actual data fields) are allowed."""

    model_config = ConfigDict(extra="allow")

    trial_count: int = Field(ge=0, description="Distinct trials contributing to this datum.")
    citations: list[Citation] = Field(default_factory=list)


class Channel(_Strict):
    field: str
    type: FieldType
    title: str
    unit: str | None = None
    sort: Literal["ascending", "descending"] | list[str] | None = None
    time_unit: Literal["year"] | None = None


class XYEncoding(_Strict):
    x: Channel
    y: Channel
    color: Channel | None = None
    tooltip: list[Channel] = Field(default_factory=list)


class HistogramEncoding(_Strict):
    bin_start: Channel
    bin_end: Channel
    y: Channel


class NetworkEncoding(_Strict):
    node_id: str = "id"
    node_label: str = "label"
    node_group: str = "group"
    node_size: str = "trial_count"
    edge_source: str = "source"
    edge_target: str = "target"
    edge_weight: str = "trial_count"


class _VizBase(_Strict):
    title: str
    subtitle: str | None = None


class _TabularViz(_VizBase):
    data: list[Provenance]

    def _channels(self) -> list[Channel]:
        raise NotImplementedError

    @model_validator(mode="after")
    def _encoding_fields_exist(self) -> Self:
        required = {c.field for c in self._channels()}
        for i, row in enumerate(self.data):
            keys = set(row.model_dump().keys())
            if missing := required - keys:
                raise ValueError(f"data[{i}] missing encoded fields: {sorted(missing)}")
        return self


class _XYViz(_TabularViz):
    encoding: XYEncoding

    def _channels(self) -> list[Channel]:
        e = self.encoding
        return [e.x, e.y, *([e.color] if e.color else []), *e.tooltip]


class BarChart(_XYViz):
    type: Literal["bar_chart"] = "bar_chart"
    orientation: Literal["vertical", "horizontal"] = "vertical"


class GroupedBarChart(_XYViz):
    type: Literal["grouped_bar_chart"] = "grouped_bar_chart"
    orientation: Literal["vertical", "horizontal"] = "vertical"

    @model_validator(mode="after")
    def _needs_color(self) -> Self:
        if self.encoding.color is None:
            raise ValueError("grouped_bar_chart requires encoding.color (the series field)")
        return self


class TimeSeries(_XYViz):
    type: Literal["time_series"] = "time_series"


class ScatterPlot(_XYViz):
    type: Literal["scatter_plot"] = "scatter_plot"


class Histogram(_TabularViz):
    type: Literal["histogram"] = "histogram"
    encoding: HistogramEncoding

    def _channels(self) -> list[Channel]:
        e = self.encoding
        return [e.bin_start, e.bin_end, e.y]


class NetworkNode(Provenance):
    id: str
    label: str
    group: str = Field(description="Dimension the node belongs to, e.g. 'sponsor'.")


class NetworkEdge(Provenance):
    source: str
    target: str


class NetworkGraph(_VizBase):
    type: Literal["network_graph"] = "network_graph"
    directed: bool = False
    encoding: NetworkEncoding = Field(default_factory=NetworkEncoding)
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> Self:
        ids = {n.id for n in self.nodes}
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"edge {e.source}->{e.target} references unknown node")
        return self


Visualization = Annotated[
    BarChart | GroupedBarChart | TimeSeries | ScatterPlot | Histogram | NetworkGraph,
    Field(discriminator="type"),
]


class ResponseStatus(StrEnum):
    OK = "ok"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    NO_RESULTS = "no_results"


class ResponseMeta(_Strict):
    source: Literal["clinicaltrials.gov"] = "clinicaltrials.gov"
    query: str
    filters: dict[str, Scalar | list[str]] = Field(
        default_factory=dict, description="Effective filters actually sent to the API."
    )
    plan: QueryPlan | None = None
    total_matching_trials: int | None = Field(
        None, description="totalCount reported by the API for the base filters."
    )
    trials_analyzed: int | None = Field(
        None, description="Records actually downloaded and aggregated."
    )
    count_method: Literal["exact", "sample"] | None = Field(
        None,
        description="'exact' = per-bucket server-side counts; 'sample' = aggregated over "
        "the first `trials_analyzed` records (see notes).",
    )
    time_granularity: Literal["year"] | None = None
    assumptions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    api_requests: list[str] = Field(
        default_factory=list, description="Exact API URLs called, for reproducibility."
    )
    timings_ms: dict[str, float] = Field(default_factory=dict)
    generated_at: datetime


class QueryResponse(_Strict):
    status: ResponseStatus
    visualization: Visualization | None = None
    message: str | None = Field(
        None, description="Clarification question or reason when status != ok."
    )
    meta: ResponseMeta

    @model_validator(mode="after")
    def _viz_iff_ok(self) -> Self:
        if (self.status == ResponseStatus.OK) != (self.visualization is not None):
            raise ValueError("visualization must be present iff status == 'ok'")
        return self
