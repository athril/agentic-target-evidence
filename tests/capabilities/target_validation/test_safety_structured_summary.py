# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _safety_structured_summary helper (safety_lens_node evidence injection)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capabilities.target_validation.workflow import _safety_structured_summary
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _prov(trace_id: str = "t") -> Provenance:
    return Provenance(
        agent_name="test",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id=trace_id,
    )


def _ev(
    evidence_type: EvidenceType,
    claim_text: str = "",
    extra: dict | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="TRPC6",
        disease="FSGS",
        evidence_type=evidence_type,
        scope="abstract",
        source="test",
        source_link="https://example.com",
        classification=DataClass.NON_SENSITIVE,
        provenance=_prov(),
        claim_text=claim_text,
        extra=extra or {},
    )


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


def test_empty_rows_returns_empty_string():
    assert _safety_structured_summary([]) == ""


def test_ignores_non_safety_types():
    rows = [
        _ev(EvidenceType.ARTICLE, "Some literature claim."),
        _ev(EvidenceType.PATENT, "A patent claim."),
        _ev(EvidenceType.CLINICAL_TRIAL, "A trial claim."),
    ]
    assert _safety_structured_summary(rows) == ""


def test_includes_expression_claim_text():
    row = _ev(EvidenceType.EXPRESSION, "TRPC6 GTEx v8 expression in Lung: 23.2 TPM median.")
    result = _safety_structured_summary([row])
    assert "Lung: 23.2 TPM" in result


def test_includes_omics_claim_text():
    row = _ev(EvidenceType.OMICS, "TRPC6 internal RNA-seq: Kidney_Cortex 4.1 TPM.")
    result = _safety_structured_summary([row])
    assert "Kidney_Cortex 4.1 TPM" in result


def test_includes_constraint_bundle_text_from_extra():
    row = _ev(
        EvidenceType.CONSTRAINT,
        claim_text="",
        extra={"text": "LOEUF=0.759, pLI=0.0000; LoF-tolerant.", "loeuf": 0.759, "pli": 0.0},
    )
    result = _safety_structured_summary([row])
    assert "LOEUF=0.759" in result


def test_prefers_claim_text_over_extra_text():
    row = _ev(
        EvidenceType.CONSTRAINT,
        claim_text="ClinVar: 38 Pathogenic variants in TRPC6.",
        extra={"text": "This should not appear."},
    )
    result = _safety_structured_summary([row])
    assert "38 Pathogenic" in result
    assert "should not appear" not in result


def test_skips_rows_with_no_text():
    rows = [
        _ev(EvidenceType.EXPRESSION, claim_text="", extra={}),
        _ev(EvidenceType.CONSTRAINT, claim_text="", extra={"loeuf": 0.5}),
    ]
    assert _safety_structured_summary(rows) == ""


def test_includes_genetics_claim_text():
    row = _ev(
        EvidenceType.GENETICS,
        "TRPC6 GWAS hit: associated with FSGS (p=2.3e-12).",
    )
    result = _safety_structured_summary([row])
    assert "FSGS" in result


def test_header_present_when_lines_exist():
    row = _ev(EvidenceType.EXPRESSION, "TRPC6 GTEx v8 expression in Lung: 23.2 TPM median.")
    result = _safety_structured_summary([row])
    assert result.startswith("Structured expression / constraint / genetics evidence:")


def test_multiple_rows_all_included():
    rows = [
        _ev(EvidenceType.EXPRESSION, "TRPC6 GTEx v8 expression in Lung: 23.2 TPM median."),
        _ev(EvidenceType.EXPRESSION, "TRPC6 RNA tissue specificity: Tissue enhanced (HPA)."),
        _ev(EvidenceType.CONSTRAINT, claim_text="", extra={"text": "LOEUF=0.759, pLI=0.00."}),
        _ev(EvidenceType.CONSTRAINT, claim_text="ClinVar: 38 Pathogenic variants."),
        _ev(EvidenceType.ARTICLE, "Ignored literature row."),
    ]
    result = _safety_structured_summary(rows)
    assert "Lung: 23.2 TPM" in result
    assert "Tissue enhanced" in result
    assert "LOEUF=0.759" in result
    assert "38 Pathogenic" in result
    assert "Ignored" not in result
