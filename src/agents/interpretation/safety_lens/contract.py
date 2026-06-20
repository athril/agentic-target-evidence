# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="safety_lens",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "ot_mouse_text",
        "ot_safety_liability_count",
        "ot_safety_liability_events",
        "ot_safety_text",
        "safety_structured_text",
        "faers_text",
        "bulk_tpm",
        "hpa_specificity",
        "disease_tissue",
        "disease_tissue_expression_note",
        "constraint_reading",
        "mechanism_direction",
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["safety_lens"],
)
