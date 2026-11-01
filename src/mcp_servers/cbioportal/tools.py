# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Somatic alterations of a gene in the TCGA PanCancer Atlas, via the cBioPortal public API.

Only the 32 TCGA PanCancer Atlas studies (``*_tcga_pan_can_atlas_2018``) are read: their
data are open access (the public MC3 mutation calls and GISTIC copy number; cite per TCGA's
guidelines). cBioPortal's software is Apache 2.0, but each study keeps its own terms, so other
studies on the portal are not read until their terms are checked. COSMIC and OncoKB are not
used (non-commercial or licensed).

Per study: the gene's mutations (protein change, position, mutation type, sample), its deep
deletions and amplifications (GISTIC −2 / +2, fetched by POST with the gene's Entrez id: the
GET form returned other genes' events in the probe), and the sequenced and copy-number
sample counts, so a gene with no event gets explicit zeros.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry, post_with_retry

logger = logging.getLogger(__name__)

_BASE = "https://www.cbioportal.org/api"
STUDY_SUFFIX = "_tcga_pan_can_atlas_2018"
_CONCURRENCY = 6


class SomaticMutation(BaseModel):
    sample_id: str
    protein_change: str = ""
    position: int | None = None
    mutation_type: str = ""


class StudyAlterations(BaseModel):
    study_id: str
    name: str = ""
    cancer_type: str = ""  # OncoTree code
    sequenced_samples: int = 0
    cna_samples: int = 0
    mutations: list[SomaticMutation] = []
    amplified_samples: list[str] = []
    deep_deleted_samples: list[str] = []
    error: str = ""


class SomaticBundle(BaseModel):
    entrez_id: str
    studies: list[StudyAlterations] = []
    source_link: str = ""


async def _studies(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    resp = await get_with_retry(
        client,
        f"{_BASE}/studies",
        params={"keyword": STUDY_SUFFIX.strip("_"), "projection": "DETAILED"},
    )
    if resp.status_code != 200:
        raise MCPToolError(f"cBioPortal studies returned HTTP {resp.status_code}")
    return [s for s in resp.json() if str(s.get("studyId", "")).endswith(STUDY_SUFFIX)]


async def _study(client: httpx.AsyncClient, s: dict[str, Any], entrez: int) -> StudyAlterations:
    sid = s["studyId"]
    out = StudyAlterations(
        study_id=sid,
        name=s.get("name", ""),
        cancer_type=s.get("cancerTypeId", ""),
        sequenced_samples=int(s.get("sequencedSampleCount") or 0),
        cna_samples=int(s.get("cnaSampleCount") or 0),
    )
    try:
        resp = await get_with_retry(
            client,
            f"{_BASE}/molecular-profiles/{sid}_mutations/mutations",
            params={"sampleListId": f"{sid}_all", "entrezGeneId": entrez, "projection": "SUMMARY"},
        )
        if resp.status_code == 200:
            out.mutations = [
                SomaticMutation(
                    sample_id=m.get("sampleId", ""),
                    protein_change=m.get("proteinChange") or "",
                    position=m.get("proteinPosStart"),
                    mutation_type=m.get("mutationType") or "",
                )
                for m in resp.json()
                if str(m.get("entrezGeneId", entrez)) == str(entrez)
            ]
        elif resp.status_code != 404:
            out.error = f"mutations HTTP {resp.status_code}"
        resp = await post_with_retry(
            client,
            f"{_BASE}/molecular-profiles/{sid}_gistic/discrete-copy-number/fetch",
            params={"discreteCopyNumberEventType": "HOMDEL_AND_AMP", "projection": "SUMMARY"},
            json={"sampleListId": f"{sid}_all", "entrezGeneIds": [entrez]},
        )
        if resp.status_code == 200:
            events = [e for e in resp.json() if str(e.get("entrezGeneId")) == str(entrez)]
            out.amplified_samples = sorted(
                {e["sampleId"] for e in events if e.get("alteration") == 2}
            )
            out.deep_deleted_samples = sorted(
                {e["sampleId"] for e in events if e.get("alteration") == -2}
            )
        elif resp.status_code != 404:
            out.error = (out.error + "; " if out.error else "") + f"CNA HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        out.error = f"{type(exc).__name__}: {exc}"[:200]
    return out


async def get_somatic_alterations(entrez_id: str | int, symbol: str = "") -> SomaticBundle:
    """The gene's mutations and GISTIC deep deletions / amplifications in each TCGA
    PanCancer Atlas study, with the studies' sample counts. A study that fails keeps its
    counts and records the error, so a missing study is never read as "no events"."""
    entrez = int(entrez_id)
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient(timeout=60.0) as client:
        studies = await _studies(client)

        async def one(s: dict[str, Any]) -> StudyAlterations:
            async with sem:
                return await _study(client, s, entrez)

        found = await asyncio.gather(*(one(s) for s in studies))
    return SomaticBundle(
        entrez_id=str(entrez),
        studies=sorted(found, key=lambda x: x.study_id),
        source_link="https://www.cbioportal.org/results/mutations?cancer_study_list="
        + ",".join(sorted(s["studyId"] for s in studies))
        + f"&case_set_id=all&gene_list={symbol}"
        if symbol
        else "https://www.cbioportal.org/",
    )
