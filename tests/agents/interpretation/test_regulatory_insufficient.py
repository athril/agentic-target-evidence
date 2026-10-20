# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regulatory "insufficient evidence" means no label data at all."""

from __future__ import annotations

import uuid

import pytest

from agents.interpretation.regulatory_lens.agent import neutral_when_labels_shown
from schemas.verdicts import AxisVerdict, LensVerdict


def _verdict(overall: str, shown: dict[str, int], with_axes: bool = True) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="regulatory",
        target_gene="GENE1",
        disease="disease one",
        overall_verdict=overall,  # type: ignore[arg-type]
        confidence=0.4,
        axes=[AxisVerdict(axis="approval_precedent", verdict=True)] if with_axes else [],
        shown_inputs=shown,
    )


def test_insufficient_with_label_claims_shown_is_neutral_and_flagged():
    out = neutral_when_labels_shown(_verdict("insufficient_evidence", {"label": 3}))
    assert out.overall_verdict == "neutral"  # not re-aligned to support by its one axis
    assert out.validation_flags[-1].rule_id == "regulatory.labels_shown"


@pytest.mark.parametrize(
    "verdict",
    [
        _verdict("insufficient_evidence", {}),  # no label data at all
        _verdict("insufficient_evidence", {"label": 0}),
        _verdict("insufficient_evidence", {"label": 2}, with_axes=False),  # canned
        _verdict("support", {"label": 2}),
    ],
)
def test_otherwise_the_verdict_is_left_alone(verdict):
    assert neutral_when_labels_shown(verdict) is verdict
