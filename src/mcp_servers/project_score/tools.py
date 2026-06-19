# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Project Score (Sanger) CRISPR fitness tools via the Cell Model Passports API.

Project Score is the Wellcome Sanger Institute's genome-wide CRISPR-Cas9
knockout-fitness screen across cancer cell lines — the Sanger counterpart to
Broad's DepMap (`mcp_servers/depmap`), run on a largely non-overlapping cell
line panel. Both sources feed the same `functional` evidence domain.
"""

from __future__ import annotations

import statistics

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_API_BASE = "https://api.cellmodelpassports.sanger.ac.uk"

# The 19 tissue lineages Project Score reports per-gene ADaM (Adaptive Daisy
# Model) calls for, as `adm_status_<tissue>` fields on the essentiality profile.
_TISSUES = (
    "biliary_tract",
    "bladder",
    "bone",
    "breast",
    "cervix",
    "cns",
    "colon",
    "endometrium",
    "esophagus",
    "haem_lymph",
    "head_neck",
    "kidney",
    "liver",
    "lung",
    "ovary",
    "pancreas",
    "pns",
    "skin",
    "soft_tissue",
    "stomach",
)

# ADaM's per-tissue call for a cancer-specific core fitness gene.
_CSCF = "CSCF"

# BAGEL2 scaled-Bayes-Factor sign convention: positive = essential (the
# opposite sign of DepMap's Chronos score, where more negative = essential).
_FITNESS_BF_THRESHOLD = 0.0


class ProjectScoreBundle(BaseModel):
    gene_symbol: str
    sidg_id: str = ""
    bf_scaled_mean: float | None = None
    bf_scaled_std: float | None = None
    bf_scaled_median: float | None = None
    num_fitness_lines: int | None = None
    total_lines: int | None = None
    fitness_fraction: float | None = None
    is_pancan_core_fitness: bool = False
    cancer_specific_core_fitness_tissues: list[str] = []
    source_link: str = ""
    text: str = ""


async def _resolve_sidg_id(client: httpx.AsyncClient, gene_symbol: str) -> str | None:
    resp = await client.get(
        "/genes",
        params={"filter": f'[{{"name":"symbol","op":"eq","val":"{gene_symbol}"}}]'},
    )
    if resp.status_code != 200:
        raise MCPToolError(f"Cell Model Passports API returned HTTP {resp.status_code}")
    data = resp.json().get("data") or []
    return data[0]["id"] if data else None


async def _get_essentiality_profile(client: httpx.AsyncClient, sidg_id: str) -> dict:
    resp = await client.get(f"/genes/{sidg_id}/essentiality_profiles")
    if resp.status_code != 200:
        raise MCPToolError(f"Cell Model Passports API returned HTTP {resp.status_code}")
    data = resp.json().get("data") or []
    return data[0]["attributes"] if data else {}


async def _get_sanger_bf_scaled(client: httpx.AsyncClient, sidg_id: str) -> list[float]:
    resp = await client.get(
        f"/genes/{sidg_id}/datasets/crispr_ko",
        params={
            "filter": (
                '[{"name":"source","op":"eq","val":"Sanger"},'
                '{"name":"qc_pass","op":"eq","val":"true"}]'
            ),
            "page[size]": 1000,
        },
    )
    if resp.status_code != 200:
        raise MCPToolError(f"Cell Model Passports API returned HTTP {resp.status_code}")
    data = resp.json().get("data") or []
    return [
        d["attributes"]["bf_scaled"] for d in data if d["attributes"].get("bf_scaled") is not None
    ]


async def get_project_score(gene_symbol: str) -> ProjectScoreBundle:
    """Fetch Project Score (Sanger) CRISPR fitness data for a gene."""
    async with httpx.AsyncClient(base_url=_API_BASE, timeout=30.0) as client:
        sidg_id = await _resolve_sidg_id(client, gene_symbol)
        if sidg_id is None:
            return ProjectScoreBundle(
                gene_symbol=gene_symbol,
                text=f"Project Score: no gene record found for {gene_symbol}.",
            )

        profile = await _get_essentiality_profile(client, sidg_id)
        bf_scaled = await _get_sanger_bf_scaled(client, sidg_id)

    is_pancan = str(profile.get("common_essential", "")).lower() == "true" or bool(
        profile.get("core_fitness_pancan")
    )
    cscf_tissues = [t for t in _TISSUES if profile.get(f"adm_status_{t}") == _CSCF]

    mean_bf = std_bf = median_bf = None
    n_fit = total = None
    fit_fraction = None
    if bf_scaled:
        mean_bf = round(statistics.mean(bf_scaled), 4)
        median_bf = round(statistics.median(bf_scaled), 4)
        total = len(bf_scaled)
        n_fit = sum(1 for v in bf_scaled if v > _FITNESS_BF_THRESHOLD)
        fit_fraction = round(n_fit / total, 4)
        if total >= 2:
            std_bf = round(statistics.stdev(bf_scaled), 4)

    frac_txt = f"{n_fit}/{total}" if n_fit is not None else "unknown"
    score_txt = f" Mean scaled BF: {mean_bf:.3f}." if mean_bf is not None else ""
    pancan_txt = " Pan-cancer core fitness gene." if is_pancan else ""
    cscf_txt = (
        f" Cancer-specific core fitness in: {', '.join(cscf_tissues)}." if cscf_tissues else ""
    )

    return ProjectScoreBundle(
        gene_symbol=gene_symbol,
        sidg_id=sidg_id,
        bf_scaled_mean=mean_bf,
        bf_scaled_std=std_bf,
        bf_scaled_median=median_bf,
        num_fitness_lines=n_fit,
        total_lines=total,
        fitness_fraction=fit_fraction,
        is_pancan_core_fitness=is_pancan,
        cancer_specific_core_fitness_tissues=cscf_tissues,
        source_link=f"https://score.depmap.sanger.ac.uk/gene/{sidg_id}",
        text=(
            f"Project Score: {gene_symbol} a fitness gene in {frac_txt} Sanger cell "
            f"lines (BAGEL2 scaled BF > 0).{score_txt}{pancan_txt}{cscf_txt}"
        ),
    )
