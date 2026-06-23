# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for OpenTargetsAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.retrieval.opentargets.agent import OpenTargetsAgent
from mcp_servers.opentargets.tools import (
    AssociationBundle,
    KnownDrugsBundle,
    MousePhenotypeBundle,
    SafetyBundle,
    TractabilityBundle,
)
from schemas.evidence import DataClass, EvidenceType
from tests.agents.conftest import make_task_msg

_ASSOC = AssociationBundle(
    gene_id="ENSG00000012048",
    disease_id="EFO_0000305",
    overall_score=0.87,
    genetic_score=0.9,
    known_drugs_score=0.7,
)
_TRACT = TractabilityBundle(
    gene_id="ENSG00000012048",
    small_molecule=True,
    antibody=False,
)
_DRUGS = KnownDrugsBundle(gene_id="ENSG00000012048", total_count=0)
_SAFETY = SafetyBundle(gene_id="ENSG00000012048")
_MOUSE = MousePhenotypeBundle(gene_id="ENSG00000012048")

_PATCHES = {
    "get_associations": AsyncMock(return_value=_ASSOC),
    "get_tractability": AsyncMock(return_value=_TRACT),
    "get_known_drugs": AsyncMock(return_value=_DRUGS),
    "get_safety": AsyncMock(return_value=_SAFETY),
    "get_mouse_phenotypes": AsyncMock(return_value=_MOUSE),
}


async def test_opentargets_agent_returns_single_evidence(run_id, trace_id, ctx):
    msg = make_task_msg(
        "opentargets",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "gene_id": "ENSG00000012048",
            "disease_id": "EFO_0000305",
        },
        run_id,
        trace_id,
    )

    with patch.multiple("services.retrieval.opentargets", **_PATCHES):
        result = await OpenTargetsAgent().run(msg, ctx)

    assert result.intent == "result"
    assert len(result.payload) == 1
    ev = result.payload[0]
    assert ev.evidence_type == EvidenceType.GENETICS
    assert ev.classification == DataClass.NON_SENSITIVE


async def test_opentargets_agent_stores_tractability_score(run_id, trace_id, ctx):
    msg = make_task_msg(
        "opentargets",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "gene_id": "ENSG00000012048",
            "disease_id": "EFO_0000305",
        },
        run_id,
        trace_id,
    )

    with patch.multiple("services.retrieval.opentargets", **_PATCHES):
        result = await OpenTargetsAgent().run(msg, ctx)

    extra = result.payload[0].extra
    assert extra["tractability_score"] == 1.0  # small_molecule=True
    assert extra["overall_score"] == pytest.approx(0.87)
    assert "known_drugs_count" in extra
    assert "safety_liability_count" in extra
    assert "mouse_phenotype_count" in extra
