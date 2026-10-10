"""Run example queries end-to-end and write the actual outputs to examples/.

Usage: python scripts/run_examples.py
"""

import asyncio
import json
import re
from pathlib import Path

from trialviz.agent.graph import run_query
from trialviz.agent.llm import OpenAIStructuredLLM
from trialviz.agent.state import Deps
from trialviz.config import get_settings
from trialviz.ctgov.client import CTGovClient
from trialviz.schemas.request import QueryRequest

OUT = Path(__file__).resolve().parent.parent / "examples"

EXAMPLES: list[dict] = [
    {"query": "How has the number of trials for this drug changed over time?",
     "drug_name": "Pembrolizumab"},
    {"query": "How are breast cancer trials distributed across phases?"},
    {"query": "Which countries have the most recruiting trials for type 2 diabetes?"},
    {"query": "Compare phases for trials involving nivolumab vs ipilimumab."},
    {"query": "Show a network of sponsors and drugs for glioblastoma trials."},
]


async def main() -> None:
    settings = get_settings()
    llm = OpenAIStructuredLLM(settings)
    OUT.mkdir(exist_ok=True)
    for i, ex in enumerate(EXAMPLES, 1):
        req = QueryRequest(**ex)
        async with CTGovClient(settings) as ctgov:
            resp = await run_query(req, Deps(settings=settings, llm=llm, ctgov=ctgov))
        slug = re.sub(r"[^a-z0-9]+", "_", req.query.lower()).strip("_")[:50]
        path = OUT / f"{i:02d}_{slug}.json"
        path.write_text(json.dumps({
            "request": req.model_dump(exclude_none=True, mode="json"),
            "response": resp.model_dump(exclude_none=True, mode="json"),
        }, indent=2))
        print(f"{resp.status:>20}  {path.name}")


if __name__ == "__main__":
    asyncio.run(main())
