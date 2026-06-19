# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="experiment",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "lens_summaries",
        "lens_verdicts",
        "genetics_floor_signals",
    },
    produces={"experiment_results"},
    max_loops=2,
)
