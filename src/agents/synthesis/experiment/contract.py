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
        "human_exposure",
        "precedent_status",  # drug_precedent status: gates the computed score
        "direction_reading",  # direction-of-effect reading: its gate caps the score
        "programme_strength",  # falsification strength of the failed programmes (or None)
    },
    produces={"experiment_results"},
    max_loops=2,
)
