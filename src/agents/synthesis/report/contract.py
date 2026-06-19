# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="report",
    consumes={"target_gene", "disease", "direction", "disease_id", "gene_id"},
    produces={"artifact_uri", "full_report_uri"},
    max_loops=1,
)
