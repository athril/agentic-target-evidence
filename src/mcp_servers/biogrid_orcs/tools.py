# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CRISPR screens that scored a gene, from BioGRID ORCS.

ORCS is an open repository of published CRISPR screens, far beyond cancer-cell fitness
(drug and toxin exposure, infection, immune and other phenotype screens). BioGRID's data are
free for academic and commercial use under the MIT License (Copyright 2005 Mike Tyers Lab;
https://downloads.thebiogrid.org/File/LICENSE.txt). The REST service needs a free access key
(register at https://orcsws.thebiogrid.org/), read from ``BIOGRID_ORCS_ACCESS_KEY``.

The key travels in the query string, so nothing here lets an exception's text out: callers
write error text into run records and the shared retry logs it, and httpx's own request log
is redacted.

Each score row carries the screen's own ``HIT`` call; what makes a hit differs per screen
(``SIGNIFICANCE_CRITERIA``), so calls are not comparable across screens.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_BASE = "https://orcsws.thebiogrid.org"
_HUMAN = "9606"
_SCREEN_BATCH = 100
_KEY_RE = re.compile(r"accesskey=[^&\s\"]*")
_ATTEMPTS = 3
_BACKOFF_S = 1.0
ENV_KEY = "BIOGRID_ORCS_ACCESS_KEY"


class ScreenResult(BaseModel):
    """One screen's call on the gene, with the screen's annotation."""

    screen_id: str
    hit: str = ""  # YES / NO, as the screen called it
    score_1: str = ""
    score_1_type: str = ""
    significance_criteria: str = ""
    phenotype: str = ""
    screen_type: str = ""
    experimental_setup: str = ""
    condition: str = ""
    cell_line: str = ""
    cell_type: str = ""
    methodology: str = ""
    author: str = ""
    pmid: str = ""


class OrcsBundle(BaseModel):
    entrez_id: str
    screens: list[ScreenResult] = []
    source_link: str = ""


def orcs_configured() -> bool:
    return bool(os.environ.get(ENV_KEY, "").strip())


class _RedactKey(logging.Filter):
    """httpx logs each request's full URL at INFO; the access key must not go with it."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "accesskey=" in message:
            record.msg, record.args = _KEY_RE.sub("accesskey=***", message), None
        return True


logging.getLogger("httpx").addFilter(_RedactKey())


async def _get(client: httpx.AsyncClient, url: str, query: dict[str, Any]) -> httpx.Response:
    """GET with a short retry. Not ``core.http.get_with_retry``: that records the exception
    text in the degradation log, and here the text may hold the access key."""
    for attempt in range(_ATTEMPTS):
        try:
            resp = await client.get(url, params=query)
        except httpx.TransportError as exc:
            if attempt == _ATTEMPTS - 1:
                # from None: nothing that may quote the URL leaves this function
                raise MCPToolError(f"BioGRID ORCS request failed: {type(exc).__name__}") from None
        else:
            if resp.status_code != 429 and resp.status_code < 500 or attempt == _ATTEMPTS - 1:
                return resp
        await asyncio.sleep(_BACKOFF_S * (attempt + 1))
    raise MCPToolError("BioGRID ORCS request failed")


async def _json(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    query = {"accesskey": os.environ.get(ENV_KEY, "").strip(), "format": "json", **params}
    resp = await _get(client, f"{_BASE}{path}", query)
    if resp.status_code != 200:
        raise MCPToolError(f"BioGRID ORCS {path} returned HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise MCPToolError(f"BioGRID ORCS {path} returned no JSON") from None
    if isinstance(data, dict) and data.get("STATUS") == "ERROR":
        raise MCPToolError(f"BioGRID ORCS {path}: {str(data.get('MESSAGE'))[:120]}")
    return data


def _dash(value: Any) -> str:
    """ORCS writes "-" for an empty field."""
    text = str(value if value is not None else "").strip()
    return "" if text == "-" else text


def _result(score: dict[str, Any], screen: dict[str, Any]) -> ScreenResult:
    pubmed = str(screen.get("SOURCE_TYPE") or "").lower() == "pubmed"
    return ScreenResult(
        screen_id=str(score.get("SCREEN_ID") or ""),
        hit=_dash(score.get("HIT")),
        score_1=_dash(score.get("SCORE.1")),
        score_1_type=_dash(screen.get("SCORE.1_TYPE")),
        significance_criteria=_dash(screen.get("SIGNIFICANCE_CRITERIA")),
        phenotype=_dash(screen.get("PHENOTYPE")),
        screen_type=_dash(screen.get("SCREEN_TYPE")),
        experimental_setup=_dash(screen.get("EXPERIMENTAL_SETUP")),
        condition=_dash(screen.get("CONDITION_NAME")),
        cell_line=_dash(screen.get("CELL_LINE")),
        cell_type=_dash(screen.get("CELL_TYPE")),
        methodology=_dash(screen.get("METHODOLOGY")),
        author=_dash(screen.get("AUTHOR")),
        pmid=_dash(screen.get("SOURCE_ID")) if pubmed else "",
    )


async def get_gene_screens(entrez_id: str | int) -> OrcsBundle:
    """Every human ORCS screen scoring the gene, hit or not, with its annotation."""
    entrez = str(entrez_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        scores = await _json(client, f"/gene/{entrez}", organismID=_HUMAN, hit="all") or []
        ids = list(dict.fromkeys(str(s.get("SCREEN_ID")) for s in scores if s.get("SCREEN_ID")))
        annotation: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), _SCREEN_BATCH):
            batch = "|".join(ids[i : i + _SCREEN_BATCH])
            for row in await _json(client, "/screens/", screenID=batch) or []:
                annotation[str(row.get("SCREEN_ID"))] = row
    return OrcsBundle(
        entrez_id=entrez,
        screens=[_result(s, annotation.get(str(s.get("SCREEN_ID")), {})) for s in scores],
        source_link=f"https://orcs.thebiogrid.org/Gene/{entrez}",
    )
