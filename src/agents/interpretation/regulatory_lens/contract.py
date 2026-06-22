# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="regulatory_lens",
    consumes={
        "disease_classes",
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "fda_label_text",
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["regulatory_lens"],
)
