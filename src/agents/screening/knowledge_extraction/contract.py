# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="knowledge_extraction",
    consumes={"target_gene", "disease", "direction"},
    produces=set(),  # updated Evidence list returned in payload
    max_loops=2,
)
