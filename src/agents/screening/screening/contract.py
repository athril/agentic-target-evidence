# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="screening",
    # pass_type: "first" (abstract-level) | "second" (full-text re-screen of uncertain)
    consumes={"target_gene", "disease", "direction", "pass_type"},
    produces=set(),  # screened Evidence list is returned in payload
    max_loops=2,
)
