# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="source_quality",
    consumes={"target_gene", "disease", "direction"},
    produces={"source_quality"},
    max_loops=1,
    skills=["source_quality_sjr"],
)
