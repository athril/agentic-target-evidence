# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="gap_detection",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "review_gaps",
        "agreement_map",
        "replan_count",
    },
    produces={"replan_decision", "gap_guidance"},
    max_loops=2,  # initial pass + one replan check
    skills=["gap_detection"],
)
