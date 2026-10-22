# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The perturbation reading in the biology lens node (workflow._perturbation_reading),
from the run's target statements: no model call."""

from __future__ import annotations

from unittest.mock import AsyncMock

import capabilities.target_validation.workflow as wf
from schemas.statement import TargetStatement


def _statement(pmid: str) -> dict:
    return TargetStatement(
        source=f"PMID:{pmid}",
        sentence=3,
        text="x",
        exposure_kind="genetic_perturbation",
        exposure_text="Gene-a knockout",
        species="10090",
        effect_on_target="decrease",
        effect_source="text",
        outcome_kind="disease",
        outcome_text="phenotype two",
        outcome_direction="higher",
        design="preclinical",
    ).model_dump(mode="json")


async def test_the_reading_is_built_from_statements_per_paper(monkeypatch):
    index = {"papers": {f"e{p}": {"pmid": p, "scope": "related"} for p in ("1", "2")}}
    monkeypatch.setattr(wf, "_literature_index", AsyncMock(return_value=index))
    state = {
        "target_gene": "GENE-A",
        "direction": "inhibit",
        "target_statements": [_statement("1"), _statement("2")],
    }

    reading = await wf._perturbation_reading(state)

    assert reading["verdict"] == "contradicts"
    assert [o["source"] for o in reading["observations"]] == ["PMID:1", "PMID:2"]


async def test_no_perturbation_statement_no_reading(monkeypatch):
    monkeypatch.setattr(wf, "_literature_index", AsyncMock(return_value={"papers": {}}))

    assert await wf._perturbation_reading({"target_gene": "GENE-A"}) == {}
