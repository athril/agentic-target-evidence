# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="critic",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "extracted_claims",
        "lens_verdicts",
        "source_quality",
    },
    produces={"critiques"},
    max_loops=3,
    skills=["claim_extraction", "verdict_qa"],
)
