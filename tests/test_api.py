"""HTTP routes that don't need the LLM: the web UI and saved examples."""

import json

import pytest
from fastapi.testclient import TestClient

from trialviz.api.app import app
from trialviz.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "01_demo.json").write_text(json.dumps(
        {"request": {"query": "Trials by phase"}, "response": {"status": "ok"}}))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("EXAMPLES_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_index_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "Clinical Trials Explorer" in r.text


def test_list_and_get_examples(client):
    assert client.get("/v1/examples").json() == [{"name": "01_demo", "query": "Trials by phase"}]
    assert client.get("/v1/examples/01_demo").json()["response"] == {"status": "ok"}


@pytest.mark.parametrize("name", ["missing", "..%2Fpyproject"])
def test_unknown_example_is_404(client, name):
    assert client.get(f"/v1/examples/{name}").status_code == 404
