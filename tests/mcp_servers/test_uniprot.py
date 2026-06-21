# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for UniProt MCP tools (protein profile)."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.uniprot.tools import ProteinProfile, get_protein_profile

_UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/search"

_UNIPROT_RESPONSE = {
    "results": [
        {
            "primaryAccession": "P00533",
            "proteinDescription": {
                "recommendedName": {"fullName": {"value": "Epidermal growth factor receptor"}}
            },
            "keywords": [{"name": "Kinase"}, {"name": "Receptor"}, {"name": "Transferase"}],
            "comments": [
                {"commentType": "FUNCTION", "texts": [{"value": "Receptor tyrosine kinase."}]},
                {
                    "commentType": "SUBCELLULAR LOCATION",
                    "subcellularLocations": [{"location": {"value": "Cell membrane"}}],
                },
            ],
            "uniProtKBCrossReferences": [{"database": "ChEMBL", "id": "CHEMBL203"}],
        }
    ]
}


@respx.mock
async def test_get_protein_profile_parses_uniprot() -> None:
    respx.get(_UNIPROT_URL).mock(return_value=httpx.Response(200, json=_UNIPROT_RESPONSE))
    profile = await get_protein_profile("EGFR")

    assert isinstance(profile, ProteinProfile)
    assert profile.uniprot_accession == "P00533"
    assert profile.chembl_target_id == "CHEMBL203"
    assert "Kinase" in profile.protein_classes
    assert "Cell membrane" in profile.subcellular_location
    assert profile.function == "Receptor tyrosine kinase."
    assert "P00533" in profile.source_link


@respx.mock
async def test_get_protein_profile_empty_results() -> None:
    respx.get(_UNIPROT_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    profile = await get_protein_profile("NOTREAL")
    assert profile.uniprot_accession == ""
    assert profile.chembl_target_id == ""


@respx.mock
async def test_get_protein_profile_raises_on_http_error() -> None:
    respx.get(_UNIPROT_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(MCPToolError, match="HTTP 500"):
        await get_protein_profile("EGFR")
