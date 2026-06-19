# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for druggability MCP tools (UniProt protein profile + ChEMBL chemistry)."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.druggability.tools import (
    ChemistryBundle,
    ProteinProfile,
    get_chemistry,
    get_protein_profile,
)

_UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/search"
_CHEMBL_MECH = "https://www.ebi.ac.uk/chembl/api/data/mechanism.json"
_CHEMBL_ACT = "https://www.ebi.ac.uk/chembl/api/data/activity.json"

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

_CHEMBL_MECH_RESPONSE = {
    "page_meta": {"total_count": 2},
    "mechanisms": [
        {
            "action_type": "INHIBITOR",
            "mechanism_of_action": "EGFR inhibitor",
            "max_phase": 4,
            "molecule_chembl_id": "CHEMBL553",
        },
        {
            "action_type": "INHIBITOR",
            "mechanism_of_action": "EGFR inhibitor",
            "max_phase": 3,
            "molecule_chembl_id": "CHEMBL1421",
        },
    ],
}

_CHEMBL_ACT_TOTAL_RESPONSE = {"page_meta": {"total_count": 15234}, "activities": []}

_CHEMBL_ACT_POTENCY_RESPONSE = {
    "page_meta": {"total_count": 8000},
    "activities": [
        {"pchembl_value": "8.5", "standard_type": "IC50", "assay_type": "B"},
        {"pchembl_value": "7.2", "standard_type": "IC50", "assay_type": "B"},
        {"pchembl_value": "6.0", "standard_type": "Ki", "assay_type": "B"},
        {"pchembl_value": "5.0", "standard_type": "EC50", "assay_type": "F"},
        {"pchembl_value": "9.1", "standard_type": "IC50", "assay_type": "B"},
    ],
}

_CHEMBL_ACT_CLINICAL_RESPONSE = {
    "page_meta": {"total_count": 50},
    "activities": [
        {
            "molecule_chembl_id": "CHEMBL553",
            "molecule_pref_name": "GEFITINIB",
            "molecule_max_phase": 4,
            "standard_type": "IC50",
            "assay_type": "B",
        },
        {
            "molecule_chembl_id": "CHEMBL1421",
            "molecule_pref_name": "ERLOTINIB",
            "molecule_max_phase": 3,
            "standard_type": "IC50",
            "assay_type": "B",
        },
        # Duplicate row for CHEMBL553 — should be de-duplicated
        {
            "molecule_chembl_id": "CHEMBL553",
            "molecule_pref_name": "GEFITINIB",
            "molecule_max_phase": 4,
            "standard_type": "IC50",
            "assay_type": "F",
        },
    ],
}


def _mock_all_chembl(
    mech=None,
    total=None,
    potency=None,
    clinical=None,
):
    """Register respx mocks for all 4 ChEMBL calls. Uses defaults if not specified."""
    mech_json = mech if mech is not None else _CHEMBL_MECH_RESPONSE
    total_json = total if total is not None else _CHEMBL_ACT_TOTAL_RESPONSE
    potency_json = potency if potency is not None else _CHEMBL_ACT_POTENCY_RESPONSE
    clinical_json = clinical if clinical is not None else _CHEMBL_ACT_CLINICAL_RESPONSE

    # respx matches by URL; we need to distinguish the 3 activity calls by query params.
    # Register specific param patterns first (most specific wins in respx).
    respx.get(_CHEMBL_MECH).mock(return_value=httpx.Response(200, json=mech_json))
    respx.get(_CHEMBL_ACT, params={"target_chembl_id": "CHEMBL203", "limit": "1"}).mock(
        return_value=httpx.Response(200, json=total_json)
    )
    respx.get(
        _CHEMBL_ACT,
        params={"target_chembl_id": "CHEMBL203", "pchembl_value__isnull": "false", "limit": "1000"},
    ).mock(return_value=httpx.Response(200, json=potency_json))
    respx.get(
        _CHEMBL_ACT,
        params={"target_chembl_id": "CHEMBL203", "molecule_max_phase__gte": "1", "limit": "100"},
    ).mock(return_value=httpx.Response(200, json=clinical_json))


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


@respx.mock
async def test_get_chemistry_aggregates_mechanisms_and_bioactivity() -> None:
    _mock_all_chembl()
    chem = await get_chemistry("CHEMBL203", gene_symbol="EGFR")

    assert isinstance(chem, ChemistryBundle)
    assert chem.num_mechanisms == 2
    assert chem.max_phase == pytest.approx(4.0)
    assert chem.action_types == ["INHIBITOR"]  # de-duplicated
    assert chem.num_bioactivities == 15234


@respx.mock
async def test_get_chemistry_potency_distribution() -> None:
    _mock_all_chembl()
    chem = await get_chemistry("CHEMBL203", gene_symbol="EGFR")

    # 5 pChEMBL values: 8.5, 7.2, 6.0, 5.0, 9.1
    assert chem.num_quantitative == 5
    assert chem.num_actives == 4  # >= 6.0: 8.5, 7.2, 6.0, 9.1
    assert chem.num_potent == 3  # >= 7.0: 8.5, 7.2, 9.1
    assert chem.num_highly_potent == 2  # >= 8.0: 8.5, 9.1
    assert chem.median_pchembl == pytest.approx(7.2)


@respx.mock
async def test_get_chemistry_activity_type_counts() -> None:
    _mock_all_chembl()
    chem = await get_chemistry("CHEMBL203", gene_symbol="EGFR")

    assert chem.activity_type_counts.get("IC50") == 3
    assert chem.activity_type_counts.get("Ki") == 1
    assert chem.activity_type_counts.get("EC50") == 1
    assert chem.assay_type_counts.get("B") == 4
    assert chem.assay_type_counts.get("F") == 1


@respx.mock
async def test_get_chemistry_clinical_candidates_deduplication() -> None:
    _mock_all_chembl()
    chem = await get_chemistry("CHEMBL203", gene_symbol="EGFR")

    # 2 unique molecules (CHEMBL553 appears twice in clinical response, must be collapsed)
    assert chem.num_clinical_candidates == 2
    ids = {c.molecule_chembl_id for c in chem.clinical_candidates}
    assert ids == {"CHEMBL553", "CHEMBL1421"}
    # Sorted by descending max_phase
    assert chem.clinical_candidates[0].max_phase == pytest.approx(4.0)
    assert chem.clinical_candidates[0].pref_name == "GEFITINIB"


@respx.mock
async def test_get_chemistry_clinical_candidates_from_mechanism_fallback() -> None:
    """Mechanisms annotated with molecule_chembl_id are surfaced as clinical candidates."""
    _mock_all_chembl(clinical={"page_meta": {"total_count": 0}, "activities": []})
    chem = await get_chemistry("CHEMBL203", gene_symbol="EGFR")

    # Mechanism rows have molecule_chembl_id + max_phase → should populate candidates
    assert chem.num_clinical_candidates == 2
    ids = {c.molecule_chembl_id for c in chem.clinical_candidates}
    assert "CHEMBL553" in ids
    assert "CHEMBL1421" in ids


@respx.mock
async def test_get_chemistry_max_phase_elevated_by_clinical_candidate() -> None:
    """max_phase is updated from clinical candidates when higher than mechanism-reported."""
    high_clinical = {
        "page_meta": {"total_count": 1},
        "activities": [
            {
                "molecule_chembl_id": "CHEMBL9999",
                "molecule_pref_name": "NEWDRUG",
                "molecule_max_phase": 4,
            }
        ],
    }
    # Mechanisms report max_phase=3; clinical query has phase=4 molecule
    low_mech = {
        "mechanisms": [
            {"action_type": "INHIBITOR", "mechanism_of_action": "EGFR inhibitor", "max_phase": 3}
        ]
    }
    _mock_all_chembl(mech=low_mech, clinical=high_clinical)
    chem = await get_chemistry("CHEMBL203", gene_symbol="EGFR")

    assert chem.max_phase == pytest.approx(4.0)


async def test_get_chemistry_without_target_id_skips_http() -> None:
    # No respx mock registered: if it tried an HTTP call it would error.
    chem = await get_chemistry("", gene_symbol="EGFR")
    assert chem.num_mechanisms == 0
    assert chem.num_bioactivities == 0
    assert chem.chembl_target_id == ""
    assert chem.clinical_candidates == []


@respx.mock
async def test_get_chemistry_raises_on_client_error() -> None:
    # 4xx (client error) raises MCPToolError; 5xx returns graceful empty bundle
    respx.get(_CHEMBL_MECH).mock(return_value=httpx.Response(404))
    respx.get(_CHEMBL_ACT).mock(return_value=httpx.Response(200, json=_CHEMBL_ACT_TOTAL_RESPONSE))
    with pytest.raises(MCPToolError, match="HTTP 404"):
        await get_chemistry("CHEMBL203", gene_symbol="EGFR")
