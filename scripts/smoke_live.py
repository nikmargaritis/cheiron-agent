"""Run the full graph against the live ClinicalTrials.gov API with hand-written plans
(no LLM needed). Useful for checking fetch/build without spending tokens.

Usage: python scripts/smoke_live.py [out_dir]
"""

import asyncio
import json
import sys
from pathlib import Path

from trialviz.agent.graph import run_query
from trialviz.agent.state import Deps
from trialviz.config import get_settings
from trialviz.ctgov.client import CTGovClient
from trialviz.schemas.common import Dimension, NumericField, OverallStatus
from trialviz.schemas.plan import (
    AnalysisKind,
    Filters,
    NetworkSpec,
    PlanStatus,
    QueryPlan,
    SeriesSpec,
)
from trialviz.schemas.request import QueryRequest


def F(**kw) -> Filters:
    return Filters(**{**dict.fromkeys(Filters.model_fields), **kw})


def P(analysis, filters, **kw) -> QueryPlan:
    base = dict(status=PlanStatus.OK, rationale="scripted", analysis=analysis, filters=filters,
                group_by=None, series=[], x_numeric=None, y_numeric=None, network=None,
                top_n=None, assumptions=[], clarification_question=None)
    return QueryPlan(**{**base, **kw})


class ScriptedLLM:
    def __init__(self, plan: QueryPlan):
        self.plan = plan

    async def parse(self, **_):
        return self.plan


CASES = {
    "time_trend": (QueryRequest(query="Trials per year for pembrolizumab since 2015",
                                drug_name="Pembrolizumab", start_year=2015),
                   P(AnalysisKind.TIME_TREND, F(), group_by=Dimension.START_YEAR)),
    "phase_distribution": (QueryRequest(query="Breast cancer trials by phase"),
                           P(AnalysisKind.DISTRIBUTION, F(condition="breast cancer"),
                             group_by=Dimension.PHASE)),
    "countries": (QueryRequest(query="Countries with most recruiting T2D trials"),
                  P(AnalysisKind.DISTRIBUTION,
                    F(condition="type 2 diabetes", statuses=[OverallStatus.RECRUITING]),
                    group_by=Dimension.COUNTRY, top_n=10)),
    "comparison": (QueryRequest(query="Phases: nivolumab vs ipilimumab"),
                   P(AnalysisKind.COMPARISON, F(), group_by=Dimension.PHASE, series=[
                       SeriesSpec(label="Nivolumab", filters=F(intervention="nivolumab")),
                       SeriesSpec(label="Ipilimumab", filters=F(intervention="ipilimumab")),
                   ])),
    "histogram": (QueryRequest(query="Enrollment sizes of melanoma trials"),
                  P(AnalysisKind.HISTOGRAM, F(condition="melanoma"),
                    x_numeric=NumericField.ENROLLMENT)),
    "network": (QueryRequest(query="Sponsor-drug network for glioblastoma"),
                P(AnalysisKind.NETWORK, F(condition="glioblastoma"),
                  network=NetworkSpec(source=Dimension.SPONSOR, target=Dimension.DRUG,
                                      min_edge_trials=2, max_nodes=40))),
    "drug_drug": (QueryRequest(query="Drug co-occurrence in pancreatic cancer"),
                  P(AnalysisKind.NETWORK, F(condition="pancreatic cancer"),
                    network=NetworkSpec(source=Dimension.DRUG, target=Dimension.DRUG,
                                        min_edge_trials=3, max_nodes=30))),
}


async def main(out: Path | None) -> None:
    settings = get_settings()
    for name, (req, plan) in CASES.items():
        async with CTGovClient(settings) as ctgov:
            resp = await run_query(req, Deps(settings, ScriptedLLM(plan), ctgov))
        v = resp.visualization
        size = (len(v.nodes), len(v.edges)) if v and v.type == "network_graph" else (
            len(v.data) if v else 0)
        m = resp.meta
        print(f"{name:20} {resp.status:12} {v.type if v else '-':18} n={size} "
              f"total={m.total_matching_trials} analyzed={m.trials_analyzed} "
              f"method={m.count_method} reqs={len(m.api_requests)} "
              f"fetch={m.timings_ms.get('fetch', 0):.0f}ms")
        for note in m.notes:
            print(f"{'':20} note: {note}")
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{name}.json").write_text(
                json.dumps(resp.model_dump(mode="json", exclude_none=True), indent=2))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
