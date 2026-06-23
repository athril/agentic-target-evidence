# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""UniProt tools — protein biology (name, class, location, function).

Public REST source, NON_SENSITIVE: UniProt KB → protein name, family/class
keywords, subcellular location, function summary, and the cross-referenced
ChEMBL target id (consumed downstream by ``mcp_servers/chembl`` to chain
into drug-mechanism and bioactivity data for the same target).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0

_UNIPROT_FIELDS = (
    "accession,id,protein_name,cc_function,cc_subcellular_location,keyword,xref_chembl"
)


async def _get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET with retries on transient transport errors."""
    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.get(url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(delay)
                delay *= 2
    raise MCPToolError(
        f"Request to {url} failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


class ProteinProfile(BaseModel):
    gene_symbol: str
    uniprot_accession: str = ""
    protein_name: str = ""
    protein_classes: list[str] = []  # UniProt keywords (e.g. Kinase, Receptor, Transferase)
    subcellular_location: list[str] = []
    function: str = ""
    chembl_target_id: str = ""  # cross-referenced ChEMBL target (e.g. CHEMBL203)
    source_link: str = ""
    text: str = ""


async def get_protein_profile(gene_symbol: str) -> ProteinProfile:
    """Fetch the reviewed (Swiss-Prot) human protein profile for a gene symbol."""
    params = {
        "query": f"gene:{gene_symbol} AND organism_id:9606 AND reviewed:true",
        "fields": _UNIPROT_FIELDS,
        "format": "json",
        "size": "1",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _get(client, _UNIPROT_SEARCH, params=params)
    if resp.status_code != 200:
        raise MCPToolError(f"UniProt API returned HTTP {resp.status_code} for {gene_symbol}")
    results = (resp.json() or {}).get("results") or []
    if not results:
        return ProteinProfile(
            gene_symbol=gene_symbol,
            source_link=f"https://www.uniprot.org/uniprotkb?query=gene:{gene_symbol}+AND+organism_id:9606",
            text=f"No reviewed human UniProt entry found for {gene_symbol}.",
        )

    entry = results[0]
    accession = entry.get("primaryAccession", "")

    name_block = (entry.get("proteinDescription") or {}).get("recommendedName") or {}
    protein_name = (name_block.get("fullName") or {}).get("value", "")

    classes = [k.get("name", "") for k in (entry.get("keywords") or []) if k.get("name")]

    function = ""
    locations: list[str] = []
    for comment in entry.get("comments") or []:
        ctype = comment.get("commentType")
        if ctype == "FUNCTION" and not function:
            texts = comment.get("texts") or []
            if texts:
                function = texts[0].get("value", "")
        elif ctype == "SUBCELLULAR LOCATION":
            for loc in comment.get("subcellularLocations") or []:
                val = (loc.get("location") or {}).get("value")
                if val:
                    locations.append(val)

    chembl_id = ""
    for xref in entry.get("uniProtKBCrossReferences") or []:
        if xref.get("database") == "ChEMBL":
            chembl_id = xref.get("id", "")
            break

    cls_text = f" Classes: {', '.join(classes[:6])}." if classes else ""
    loc_text = f" Localization: {', '.join(locations[:4])}." if locations else ""
    return ProteinProfile(
        gene_symbol=gene_symbol,
        uniprot_accession=accession,
        protein_name=protein_name,
        protein_classes=classes,
        subcellular_location=locations,
        function=function,
        chembl_target_id=chembl_id,
        source_link=f"https://www.uniprot.org/uniprotkb/{accession}",
        text=(f"UniProt {accession}: {protein_name or gene_symbol}.{cls_text}{loc_text}"),
    )
