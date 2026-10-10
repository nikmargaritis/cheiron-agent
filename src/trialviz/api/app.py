"""HTTP surface. Run with: uvicorn trialviz.api.app:app --reload"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from trialviz import __version__
from trialviz.agent.graph import run_query
from trialviz.agent.llm import LLMError, OpenAIStructuredLLM
from trialviz.agent.state import Deps
from trialviz.config import get_settings
from trialviz.ctgov.client import CTGovClient, CTGovError
from trialviz.schemas.request import QueryRequest
from trialviz.schemas.response import QueryResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # One pooled HTTP client for the process; one CTGovClient (own request log) per request.
    app.state.http = httpx.AsyncClient(
        base_url=settings.ctgov_base_url,
        timeout=settings.ctgov_timeout_s,
        headers={"Accept": "application/json", "User-Agent": f"trialviz/{__version__}"},
    )
    app.state.llm = OpenAIStructuredLLM(settings)
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="trialviz",
    version=__version__,
    description="Natural-language questions about clinical trials → visualization specs "
    "grounded in ClinicalTrials.gov data.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/v1/query", response_model=QueryResponse, response_model_exclude_none=True)
async def query(req: QueryRequest) -> QueryResponse:
    settings = get_settings()
    deps = Deps(
        settings=settings,
        llm=app.state.llm,
        ctgov=CTGovClient(settings, http=app.state.http),
    )
    try:
        return await run_query(req, deps)
    except CTGovError as e:
        raise HTTPException(502, f"ClinicalTrials.gov API error: {e}") from e
    except LLMError as e:
        raise HTTPException(502, f"LLM error: {e}") from e


def _example_files() -> dict[str, Path]:
    return {p.stem: p for p in sorted(get_settings().examples_dir.glob("*.json"))}


@app.get("/v1/examples")
async def list_examples() -> list[dict[str, str]]:
    """Saved example runs: name + the question that produced it."""
    return [
        {"name": name, "query": json.loads(path.read_text())["request"]["query"]}
        for name, path in _example_files().items()
    ]


@app.get("/v1/examples/{name}")
async def get_example(name: str) -> dict[str, Any]:
    path = _example_files().get(name)
    if path is None:
        raise HTTPException(404, f"No example named {name!r}")
    return json.loads(path.read_text())
