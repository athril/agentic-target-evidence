# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="reviewer",
    consumes={"target_gene", "disease", "direction", "stage_counts"},
    produces={"review_gaps"},
    max_loops=2,
)
