# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Genebass connector: rows near significance joined to their phenotype, with the mask's
tested count and QC inputs; any HTTP failure raises, never reads as "no association"."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.genebass.tools import get_gene_burden

_B = "https://main.genebass.org/api"
_GENE = "ENSG0001"


def _row(code: str, p: float, burden: float, beta: float = -0.04) -> dict:
    return {"trait_type": "continuous", "phenocode": code, "pheno_sex": "both_sexes", "coding": "",
            "modifier": "irnt", "Pvalue": p, "Pvalue_Burden": burden, "BETA_Burden": beta,
            "total_variants": 58}  # fmt: skip


_PHENOTYPES = [
    {"trait_type": "continuous", "phenocode": "30780", "pheno_sex": "both_sexes", "coding": "",
     "modifier": "irnt", "description": "LDL direct", "coding_description": None, "n_cases": 376106,
     "n_controls": None, "category": "Biomarkers"},
]  # fmt: skip


def _mock_common() -> None:
    respx.get(f"{_B}/phenotypes").mock(return_value=httpx.Response(200, json=_PHENOTYPES))
    respx.get(f"{_B}/gene/{_GENE}/gene-analysis-qc").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"annotation": "pLoF", "CAF": 0.001, "mean_coverage": 41.5},
            ],
        )  # fmt: skip
    )


@respx.mock
async def test_keeps_rows_near_significance_with_their_phenotype_and_qc() -> None:
    _mock_common()
    respx.get(f"{_B}/phewas/{_GENE}").mock(
        return_value=httpx.Response(
            200,
            json={
                "gene": {},
                "phewas": [
                    _row("30780", 2e-134, 3.6e-132),
                    _row("999", 0.4, 0.5),
                ],
            },
        )  # fmt: skip
    )
    b = await get_gene_burden(_GENE)
    plof, missense = b.masks
    assert (plof.mask, plof.phenotypes_tested, plof.caf) == ("pLoF", 2, 0.001)
    [row] = plof.rows
    assert (row.phenotype, row.n_cases, row.total_variants) == ("LDL direct", 376106, 58)
    assert missense.mask == "missense|LC" and missense.caf is None  # no QC row for this mask


@respx.mock
async def test_a_server_error_raises_and_is_not_an_empty_result() -> None:
    _mock_common()
    respx.get(f"{_B}/phewas/{_GENE}").mock(return_value=httpx.Response(500))
    with pytest.raises(MCPToolError, match="HTTP 500"):
        await get_gene_burden(_GENE)
