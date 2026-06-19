# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="genetics",
    consumes={"target_gene", "disease", "direction", "gene_id", "disease_id"},
    produces=set(),
    max_loops=1,
)
