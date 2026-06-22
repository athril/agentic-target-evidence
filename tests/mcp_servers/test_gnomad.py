# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for gnomAD MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.gnomad.tools import (
    ClinVarBundle,
    ClinVarVariant,
    ConstraintBundle,
    LofVariantBundle,
    _consequence_summary,
    _population_skew_note,
    get_clinvar_variants,
    get_constraint,
    get_lof_variants,
)

_GNOMAD_URL = "https://gnomad.broadinstitute.org/api"

_CONSTRAINT_RESPONSE = {
    "data": {
        "gene": {
            "gene_id": "ENSG00000012048",
            "gnomad_constraint": {
                "pLI": 0.99,
                "oe_lof": 0.04,
                "oe_lof_lower": 0.02,
                "oe_lof_upper": 0.12,
                "oe_mis": 0.6,
                "oe_mis_lower": 0.55,
                "oe_mis_upper": 0.65,
                "mis_z": 3.1,
                "syn_z": 0.2,
                "obs_lof": 2,
                "exp_lof": 45.3,
                "obs_mis": 300,
                "exp_mis": 500.0,
                "obs_syn": 200,
                "exp_syn": 210.0,
            },
        }
    }
}

_CLINVAR_RESPONSE = {
    "data": {
        "gene": {
            "clinvar_variants": [
                {
                    "variant_id": "17-41196312-C-T",
                    "clinical_significance": "Pathogenic",
                    "gold_stars": 3,
                    "hgvsc": "NM_007294.4:c.5266dupC",
                    "hgvsp": "NM_007294.4:p.Gln1756ProfsTer74",
                    "major_consequence": "frameshift_variant",
                    "in_gnomad": False,
                },
                {
                    "variant_id": "17-41223094-G-A",
                    "clinical_significance": "Likely Pathogenic",
                    "gold_stars": 1,
                    "hgvsc": "NM_007294.4:c.5123C>A",
                    "hgvsp": "NM_007294.4:p.Ala1708Glu",
                    "major_consequence": "missense_variant",
                    "in_gnomad": True,
                },
                {
                    "variant_id": "17-41243452-A-G",
                    "clinical_significance": "Benign",
                    "gold_stars": 1,
                    "hgvsc": None,
                    "hgvsp": None,
                    "major_consequence": "synonymous_variant",
                    "in_gnomad": True,
                },
            ]
        }
    }
}

_LOF_VARIANTS_RESPONSE = {
    "data": {
        "gene": {
            "variants": [
                {
                    "variant_id": "17-41196312-C-CT",
                    "consequence": "frameshift_variant",
                    "hgvsc": "NM_007294.4:c.5266dupC",
                    "hgvsp": None,
                    "lof": "HC",
                    "lof_filter": None,
                    "lof_flags": None,
                    "genome": {"af": 1.2e-5, "ac": 3, "an": 250000, "homozygote_count": 0},
                },
                {
                    "variant_id": "17-41234567-G-A",
                    "consequence": "stop_gained",
                    "hgvsc": "NM_007294.4:c.4321G>A",
                    "hgvsp": "NM_007294.4:p.Glu1441Ter",
                    "lof": "HC",
                    "lof_filter": None,
                    "lof_flags": None,
                    "genome": {"af": 2.0e-6, "ac": 1, "an": 500000, "homozygote_count": 0},
                },
                {
                    "variant_id": "17-41212345-A-G",
                    "consequence": "missense_variant",
                    "hgvsc": "NM_007294.4:c.1234A>G",
                    "hgvsp": None,
                    "lof": None,  # not a LoF — should be filtered out
                    "lof_filter": None,
                    "lof_flags": None,
                    "genome": {"af": 0.05, "ac": 1000, "an": 20000, "homozygote_count": 5},
                },
            ]
        }
    }
}


@respx.mock
async def test_get_constraint_returns_bundle() -> None:
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(200, json=_CONSTRAINT_RESPONSE))
    bundle = await get_constraint("BRCA1")

    assert isinstance(bundle, ConstraintBundle)
    assert bundle.gene_symbol == "BRCA1"
    assert bundle.ensembl_id == "ENSG00000012048"
    assert bundle.loeuf == pytest.approx(0.12)
    assert bundle.oe_lof == pytest.approx(0.04)
    assert bundle.oe_lof_lower == pytest.approx(0.02)
    assert bundle.pli == pytest.approx(0.99)
    assert bundle.moeuf == pytest.approx(0.65)
    assert bundle.oe_mis == pytest.approx(0.6)
    assert bundle.mis_z == pytest.approx(3.1)
    assert bundle.syn_z == pytest.approx(0.2)
    assert bundle.obs_lof == 2
    assert bundle.obs_mis == 300
    assert bundle.obs_syn == 200
    assert "ENSG00000012048" in bundle.source_link
    assert "LOEUF" in bundle.text


@respx.mock
async def test_get_constraint_raises_on_http_error() -> None:
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_constraint("BRCA1")


@respx.mock
async def test_get_constraint_raises_on_graphql_error() -> None:
    respx.post(_GNOMAD_URL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "gene not found"}]})
    )
    with pytest.raises(MCPToolError, match="GraphQL error"):
        await get_constraint("UNKNOWN_GENE")


@respx.mock
async def test_get_constraint_missing_gene_returns_empty_bundle() -> None:
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(200, json={"data": {"gene": None}}))
    bundle = await get_constraint("NOTREAL")
    assert bundle.loeuf is None
    assert bundle.pli is None


@respx.mock
async def test_get_clinvar_variants_classifies_correctly() -> None:
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(200, json=_CLINVAR_RESPONSE))
    bundle = await get_clinvar_variants("ENSG00000012048", "BRCA1")

    assert isinstance(bundle, ClinVarBundle)
    assert bundle.total_clinvar == 3
    assert len(bundle.pathogenic) == 1
    assert bundle.pathogenic[0].variant_id == "17-41196312-C-T"
    assert bundle.pathogenic[0].gold_stars == 3
    assert len(bundle.likely_pathogenic) == 1
    assert len(bundle.benign) == 1
    assert "Pathogenic" in bundle.text


@respx.mock
async def test_get_clinvar_variants_empty_gene() -> None:
    respx.post(_GNOMAD_URL).mock(
        return_value=httpx.Response(200, json={"data": {"gene": {"clinvar_variants": []}}})
    )
    bundle = await get_clinvar_variants("ENSG00000000001")
    assert bundle.total_clinvar == 0
    assert bundle.pathogenic == []
    assert "No ClinVar" in bundle.text


@respx.mock
async def test_get_lof_variants_filters_hc_only() -> None:
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(200, json=_LOF_VARIANTS_RESPONSE))
    bundle = await get_lof_variants("ENSG00000012048", "BRCA1")

    assert isinstance(bundle, LofVariantBundle)
    assert bundle.hc_lof_count == 2
    assert len(bundle.reported_variants) == 2
    # Sorted by AF descending
    assert bundle.reported_variants[0].af == pytest.approx(1.2e-5)
    assert bundle.max_af == pytest.approx(1.2e-5)
    assert not bundle.any_homozygous
    assert "HC pLoF" in bundle.text


@respx.mock
async def test_get_lof_variants_no_hc_lof() -> None:
    respx.post(_GNOMAD_URL).mock(
        return_value=httpx.Response(200, json={"data": {"gene": {"variants": []}}})
    )
    bundle = await get_lof_variants("ENSG00000012048", "BRCA1")
    assert bundle.hc_lof_count == 0
    assert bundle.reported_variants == []
    assert "No high-confidence" in bundle.text


# ── population AF / ancestry skew ───────────────────────────────────────────

_LOF_VARIANTS_SKEWED_RESPONSE = {
    "data": {
        "gene": {
            "variants": [
                {
                    "variant_id": "17-41196312-C-CT",
                    "consequence": "frameshift_variant",
                    "hgvsc": "NM_007294.4:c.5266dupC",
                    "hgvsp": None,
                    "lof": "HC",
                    "lof_filter": None,
                    "lof_flags": None,
                    "genome": {
                        "af": 1.2e-3,
                        "ac": 300,
                        "an": 250000,
                        "homozygote_count": 0,
                        "populations": [
                            {"id": "afr", "ac": 50, "an": 10000},
                            {"id": "eas", "ac": 1, "an": 10000},
                            {"id": "amr", "ac": 1, "an": 500},  # below AN floor — ignored
                        ],
                    },
                },
            ]
        }
    }
}


@respx.mock
async def test_get_lof_variants_flags_ancestry_skew() -> None:
    respx.post(_GNOMAD_URL).mock(
        return_value=httpx.Response(200, json=_LOF_VARIANTS_SKEWED_RESPONSE)
    )
    bundle = await get_lof_variants("ENSG00000012048", "BRCA1")

    assert bundle.ancestry_skewed
    top = bundle.reported_variants[0]
    assert top.population_af == {"afr": pytest.approx(5.0e-3), "eas": pytest.approx(1.0e-4)}
    assert "ancestry-skewed" in bundle.text
    assert "afr" in bundle.text and "eas" in bundle.text


@respx.mock
async def test_get_lof_variants_uniform_af_not_flagged() -> None:
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(200, json=_LOF_VARIANTS_RESPONSE))
    bundle = await get_lof_variants("ENSG00000012048", "BRCA1")

    assert not bundle.ancestry_skewed
    assert "ancestry-skewed" not in bundle.text


def test_population_skew_note_below_ratio_returns_none():
    assert _population_skew_note({"afr": 1.0e-3, "eas": 5.0e-4}) is None


def test_population_skew_note_single_population_returns_none():
    assert _population_skew_note({"afr": 1.0e-3}) is None


def test_population_skew_note_above_ratio_returns_text():
    note = _population_skew_note({"afr": 5.0e-3, "eas": 1.0e-4})
    assert note is not None
    assert "afr" in note and "eas" in note


# ── _consequence_summary ──────────────────────────────────────────────────────


def test_consequence_summary_groups_by_consequence():
    variants = [
        ClinVarVariant(variant_id="v1", major_consequence="missense_variant"),
        ClinVarVariant(variant_id="v2", major_consequence="missense_variant"),
        ClinVarVariant(variant_id="v3", major_consequence="stop_gained"),
    ]
    summary = _consequence_summary(variants)
    assert "2 missense_variant" in summary
    assert "1 stop_gained" in summary


def test_consequence_summary_handles_none_consequence():
    variants = [
        ClinVarVariant(variant_id="v1", major_consequence=None),
        ClinVarVariant(variant_id="v2", major_consequence="missense_variant"),
    ]
    summary = _consequence_summary(variants)
    assert "1 missense_variant" in summary
    assert "unknown" in summary


@respx.mock
async def test_get_clinvar_variants_text_includes_consequence_breakdown() -> None:
    """ClinVar text must include major_consequence counts for LLM/lens inference."""
    respx.post(_GNOMAD_URL).mock(return_value=httpx.Response(200, json=_CLINVAR_RESPONSE))
    bundle = await get_clinvar_variants("ENSG00000012048", "BRCA1")

    # frameshift_variant is the consequence of the one Pathogenic variant in _CLINVAR_RESPONSE
    assert "frameshift_variant" in bundle.text
    # missense_variant is the consequence of the one Likely Pathogenic variant
    assert "missense_variant" in bundle.text
