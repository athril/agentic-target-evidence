# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic commercial-lens rules (agents/interpretation/commercial_lens/agent.py)."""

from __future__ import annotations

import uuid

from agents.interpretation.commercial_lens.agent import _ip_unjudged
from schemas.verdicts import AxisVerdict, LensVerdict


def _v(overall: str, market: bool | None = True) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="commercial",
        target_gene="G",
        disease="d",
        overall_verdict=overall,
        confidence=0.7,
        axes=[
            AxisVerdict(
                axis="ip_landscape", verdict=False, confidence=0.75, rationale="16 patents"
            ),
            AxisVerdict(
                axis="competitive_opportunity", verdict=False, confidence=0.7, rationale="crowded"
            ),
            AxisVerdict(axis="market_size", verdict=market, confidence=0.6, rationale="large"),
        ],
    )


def test_patent_titles_do_not_judge_the_ip_landscape():
    out = _ip_unjudged(_v("oppose"), [])
    ip = next(a for a in out.axes if a.axis == "ip_landscape")
    assert ip.verdict is None and "freedom to operate" in ip.rationale
    assert any(f.rule_id == "ip_landscape_unjudged" for f in out.validation_flags)
