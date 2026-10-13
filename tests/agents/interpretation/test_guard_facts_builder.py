# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""build_guard_facts derives the GWAS-presence and trial-attribution inputs from the spec."""

from __future__ import annotations

from agents.interpretation._lens_base import build_guard_facts


def test_guard_facts_carry_gwas_traits_and_trial_values() -> None:
    spec = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "floor_signals": {"gwas_traits_in_scope": 3},
        "trial_facts": [
            {
                "nct_id": "NCT01111111",
                "phase": "PHASE3",
                "status": "COMPLETED",
                "phase_numbers": [3],
                "publication_ids": ["doc-1"],
            }
        ],
        "extracted_claims": [
            {"source_evidence_id": "doc-1", "claim_text": "The composite fell (HR 0.79)."},
            {"source_evidence_id": "doc-2", "claim_text": "An unrelated cohort: OR 1.31."},
        ],
    }
    facts = build_guard_facts(spec)
    assert facts.gwas_traits_in_scope == 3
    assert facts.trial_values == {"NCT01111111": (0.79,)}


def test_no_trial_facts_leaves_attribution_unarmed() -> None:
    facts = build_guard_facts({"target_gene": "GENE-A", "disease": "DISEASE-A"})
    assert facts.trial_values is None
    assert facts.gwas_traits_in_scope == 0
