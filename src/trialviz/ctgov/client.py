"""Async client for the ClinicalTrials.gov v2 API.

Two access patterns, chosen by the executor:

* ``count(params)``        — ``countTotal=true&pageSize=1``: one cheap request returns the
                             exact number of matching trials. Fan out one per bucket for
                             exact aggregations over arbitrarily large result sets.
* ``fetch_studies(params)``— token-paginated download of minimal ``fields=``, capped at
                             ``max_records``. Needed for open-ended dimensions (sponsor,
                             country, intervention), networks, and citations.

Every URL requested is recorded in ``self.request_log`` and surfaced in the response
metadata so any number in a chart can be reproduced by hand.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from trialviz.config import Settings

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class CTGovError(RuntimeError):
    pass


@dataclass
class StudyPage:
    studies: list[dict[str, Any]]
    total_count: int


@dataclass
class CTGovClient:
    settings: Settings
    http: httpx.AsyncClient | None = None
    request_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._own_http = self.http is None
        self.http = self.http or httpx.AsyncClient(
            base_url=self.settings.ctgov_base_url,
            timeout=self.settings.ctgov_timeout_s,
            headers={"Accept": "application/json", "User-Agent": "trialviz/0.1"},
        )
        self._sem = asyncio.Semaphore(self.settings.ctgov_max_concurrency)

    async def aclose(self) -> None:
        if self._own_http and self.http is not None:
            await self.http.aclose()

    async def __aenter__(self) -> "CTGovClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.http is not None
        attempt = 0
        while True:
            async with self._sem:
                resp = await self.http.get(path, params=params)
            if resp.status_code == 200:
                self.request_log.append(str(resp.request.url))
                return resp.json()
            if resp.status_code in RETRY_STATUSES and attempt < self.settings.ctgov_max_retries:
                attempt += 1
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            # 400s carry a useful plain-text message (e.g. bad field name / Essie syntax).
            raise CTGovError(f"{resp.status_code} from {resp.request.url}: {resp.text[:300]}")

    async def count(self, params: dict[str, str]) -> int:
        data = await self._get(
            "/studies", {**params, "countTotal": "true", "pageSize": 1, "fields": "NCTId"}
        )
        return int(data.get("totalCount", 0))

    async def fetch_studies(
        self,
        params: dict[str, str],
        fields: list[str],
        max_records: int | None = None,
        sort: str | None = None,
    ) -> StudyPage:
        """Download up to ``max_records`` studies. With ``max_records <= page size`` this is a
        single request that returns both the exact ``totalCount`` and a record sample."""
        cap = max_records or self.settings.ctgov_max_records
        page_size = min(self.settings.ctgov_page_size, cap)
        query = {**params, "fields": ",".join(fields), "pageSize": page_size, "countTotal": "true"}
        if sort:
            query["sort"] = sort

        studies: list[dict[str, Any]] = []
        total = 0
        token: str | None = None
        while True:
            data = await self._get("/studies", {**query, **({"pageToken": token} if token else {})})
            if token is None:  # first page; totalCount is only returned here
                total = int(data.get("totalCount", 0))
            studies.extend(data.get("studies", []))
            token = data.get("nextPageToken")
            if not token or len(studies) >= cap:
                break

        return StudyPage(studies=studies[:cap], total_count=total)
