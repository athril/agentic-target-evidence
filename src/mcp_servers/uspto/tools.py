# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""USPTO patent search via the Open Data Portal (ODP) API.

Endpoint: POST https://api.uspto.gov/api/v1/patent/applications/search
Docs:     https://data.uspto.gov/apis/getting-started
Auth:     X-API-KEY header — register at https://data.uspto.gov/apis/getting-started

Rate limits (per ODP docs):
  - Burst limit: 1 request at a time per API key (no parallel calls).
  - Sustained rate varies by endpoint/call type; throttled per-endpoint below.
  - A 429 ("Too Many Requests") means the limit was exceeded — wait at least
    5 s before retrying.
  - Download URLs are signed and expire 5 s after the redirect is issued.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from mcp_servers.uspto.abstract_pdf import fetch_abstract_pdf
from schemas.evidence import DataClass

_ODP_BASE = "https://api.uspto.gov/api/v1"
_ODP_SEARCH_URL = f"{_ODP_BASE}/patent/applications/search"
_ODP_DOCS_URL = f"{_ODP_BASE}/patent/applications/{{app_num}}/documents"

_PAGE_SIZE = 100
_MAX_RESULTS = 100
_RETRY_429_DELAY = 5.0  # seconds — wait at least this long after a 429 before retrying

# Sustained-rate budget per endpoint (requests/second). ODP's documented rate
# varies by call type; 5 req/s is the default for each endpoint we call.
_ENDPOINT_REQUESTS_PER_SECOND: dict[str, float] = {
    "search": 5.0,
    "documents": 5.0,
}

# ODP enforces burst=1: one in-flight request per API key at a time, across
# all endpoints. All calls that use the same key must go through this semaphore.
_ODP_SEM = asyncio.Semaphore(1)
_last_request_at: dict[str, float] = {}


logger = logging.getLogger(__name__)


async def _throttle(endpoint: str) -> None:
    """Sleep if needed so consecutive requests to `endpoint` respect its req/s budget.

    Must be called while holding _ODP_SEM.
    """
    min_interval = 1.0 / _ENDPOINT_REQUESTS_PER_SECOND[endpoint]
    loop = asyncio.get_event_loop()
    elapsed = loop.time() - _last_request_at.get(endpoint, 0.0)
    if elapsed < min_interval:
        await asyncio.sleep(min_interval - elapsed)
    _last_request_at[endpoint] = loop.time()


def _api_key() -> str:
    key = os.environ.get("USPTO_API_KEY", "")
    if not key:
        raise MCPToolError(
            "USPTO_API_KEY is not set. Register at https://data.uspto.gov/apis/getting-started"
        )
    return key


_GOOGLE_PATENTS = "https://patents.google.com/patent/US"
_USPTO_APP = "https://patentcenter.uspto.gov/applications"


class PatentRecord(BaseModel):
    patent_id: str
    app_number: str = ""
    title: str
    abstract: str = ""
    assignee: str = ""
    filing_date: str = ""
    source_link: str = ""
    uspto_link: str = ""
    classification: DataClass = DataClass.NON_SENSITIVE
    query_used: str = ""


async def _odp_get(
    client: httpx.AsyncClient, url: str, key: str, *, endpoint: str, **kwargs
) -> httpx.Response:
    """Serialized GET against ODP, throttled to `endpoint`'s req/s budget, with one 429-retry."""
    async with _ODP_SEM:
        await _throttle(endpoint)
        resp = await client.get(
            url, headers={"X-API-KEY": key, "accept": "application/json"}, **kwargs
        )
        if resp.status_code == 429:
            logger.warning("ODP rate-limited (429); waiting %.0fs before retry", _RETRY_429_DELAY)
            await asyncio.sleep(_RETRY_429_DELAY)
            await _throttle(endpoint)
            resp = await client.get(
                url, headers={"X-API-KEY": key, "accept": "application/json"}, **kwargs
            )
    return resp


async def _odp_post(
    client: httpx.AsyncClient, url: str, key: str, body: dict, *, endpoint: str
) -> httpx.Response:
    """Serialized POST against ODP, throttled to `endpoint`'s req/s budget, with one 429-retry."""
    async with _ODP_SEM:
        await _throttle(endpoint)
        resp = await client.post(
            url,
            json=body,
            headers={"X-API-KEY": key, "accept": "application/json"},
        )
        if resp.status_code == 429:
            logger.warning("ODP rate-limited (429); waiting %.0fs before retry", _RETRY_429_DELAY)
            await asyncio.sleep(_RETRY_429_DELAY)
            await _throttle(endpoint)
            resp = await client.post(
                url,
                json=body,
                headers={"X-API-KEY": key, "accept": "application/json"},
            )
    return resp


async def search_patents(gene: str, disease: str) -> list[PatentRecord]:
    """Search USPTO ODP for granted patents referencing both gene and disease."""
    query = f'applicationMetaData.inventionTitle:("{gene}" "{disease}")'
    base_body: dict = {
        "q": query,
        "filters": [
            {
                "name": "applicationMetaData.applicationStatusDescriptionText",
                "value": ["Patented Case"],
            }
        ],
        "sort": [{"field": "applicationMetaData.filingDate", "order": "desc"}],
        "fields": [
            "applicationNumberText",
            "applicationMetaData.inventionTitle",
            "applicationMetaData.filingDate",
            "applicationMetaData.grantDate",
            "applicationMetaData.patentNumber",
            "applicationMetaData.patentApplicationPublicationNumber",
            "applicationMetaData.firstApplicantName",
            "applicationMetaData.cpcClassificationBag",
        ],
    }

    key = _api_key()
    raw_items: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while len(raw_items) < _MAX_RESULTS:
            body = {**base_body, "pagination": {"offset": offset, "limit": _PAGE_SIZE}}
            response = await _odp_post(client, _ODP_SEARCH_URL, key, body, endpoint="search")

            if response.status_code == 401:
                raise MCPToolError("USPTO ODP API key is invalid or expired (HTTP 401)")
            if response.status_code != 200:
                raise MCPToolError(
                    f"USPTO ODP API returned HTTP {response.status_code}: {response.text[:200]}"
                )

            data = response.json()
            page_items = data.get("patentFileWrapperDataBag") or []
            raw_items.extend(page_items)

            total = data.get("count", 0)
            if not page_items or len(raw_items) >= total:
                break
            offset += _PAGE_SIZE

        records: list[PatentRecord] = []
        for item in raw_items:
            meta = item.get("applicationMetaData", {})
            patent_num = meta.get("patentNumber", "")
            pub_num = meta.get("patentApplicationPublicationNumber", "")
            app_num = str(item.get("applicationNumberText", ""))
            pid = patent_num or app_num
            # Google Patents resolves by grant number or publication number only —
            # bare application numbers (e.g. 19035409) return 404.
            google_id = patent_num or pub_num
            record = PatentRecord(
                patent_id=pid,
                app_number=app_num,
                title=meta.get("inventionTitle", ""),
                assignee=meta.get("firstApplicantName", ""),
                filing_date=meta.get("filingDate", ""),
                source_link=f"{_GOOGLE_PATENTS}{google_id}" if google_id else "",
                uspto_link=f"{_USPTO_APP}/{app_num}" if app_num else "",
                classification=DataClass.NON_SENSITIVE,
                query_used=query,
            )
            # ODP search metadata has no abstract field — the only USPTO-native
            # source is the Documents API's ABST PDF (often scanned, OCR'd below).
            record.abstract = await fetch_abstract_pdf(client, app_num, key)
            records.append(record)

    return records
