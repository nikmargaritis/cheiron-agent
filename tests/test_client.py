import httpx
import pytest
import respx

from trialviz.ctgov.client import CTGovClient, CTGovError

BASE = "https://clinicaltrials.gov/api/v2"


def _study(i: int) -> dict:
    return {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}}


@respx.mock
async def test_count(settings):
    route = respx.get(f"{BASE}/studies").respond(json={"totalCount": 42, "studies": []})
    async with CTGovClient(settings) as c:
        assert await c.count({"query.cond": "x"}) == 42
        assert len(c.request_log) == 1
    assert route.calls.last.request.url.params["countTotal"] == "true"


@respx.mock
async def test_fetch_paginates_and_caps(settings):
    settings = settings.model_copy(update={"ctgov_page_size": 2})
    respx.get(f"{BASE}/studies").mock(side_effect=[
        httpx.Response(200, json={"totalCount": 10, "studies": [_study(1), _study(2)],
                                  "nextPageToken": "t1"}),
        httpx.Response(200, json={"studies": [_study(3), _study(4)], "nextPageToken": "t2"}),
    ])
    async with CTGovClient(settings) as c:
        page = await c.fetch_studies({}, ["NCTId"], max_records=3)
    assert len(page.studies) == 3
    assert page.total_count == 10


@respx.mock
async def test_retries_on_503_then_succeeds(settings, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    respx.get(f"{BASE}/studies").mock(side_effect=[
        httpx.Response(503), httpx.Response(200, json={"totalCount": 1, "studies": []}),
    ])
    async with CTGovClient(settings) as c:
        assert await c.count({}) == 1


@respx.mock
async def test_400_surfaces_api_message(settings):
    respx.get(f"{BASE}/studies").respond(400, text="Parameter 'fields' contains invalid field")
    async with CTGovClient(settings) as c:
        with pytest.raises(CTGovError, match="invalid field"):
            await c.count({})


async def _no_sleep(_: float) -> None:
    return None
