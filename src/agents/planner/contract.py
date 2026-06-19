# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="planner",
    consumes={"target_gene", "disease", "direction", "population", "tissue", "step_budget"},
    produces={"run_id", "status"},
    max_loops=1,
)
