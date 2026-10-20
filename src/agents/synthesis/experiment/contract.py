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
        "candidate_liabilities",
        "liability_statuses",  # the safety lens's status per candidate liability (or None)
        "requested_drug_approved",  # a drug with the requested effect is approved here
        "human_exposure",
        "direction_reading",  # direction-of-effect reading: its gate caps the score
    },
    produces={"experiment_results"},
    max_loops=2,
)
