# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="genetics_lens",
    consumes={
        "disease_classes",
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "missing_inputs",  # required structured inputs absent from the run (lens_inputs)
        "direction_reading",  # direction-of-effect reading (direction_of_effect)
        "direction_reading_text",  # its rendered prompt block
        "source_evidence_text",  # pre-rendered genetics+constraint evidence summaries
        "floor_signals",  # {max_genetic_score, plp_count, high_star_plp}
        "drug_precedent",  # therapeutic-hypothesis precedent (services/evidence/drug_precedent)
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["genetics_lens"],
)
