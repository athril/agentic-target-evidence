# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="investigator",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "review_gaps",
        "agreement_map",
        "lens_summary",
    },
    produces={"investigation_summary", "tools_used"},
    max_loops=1,
    skills=["investigator"],
)
