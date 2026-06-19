# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Reconciler service — deterministic cross-lens agreement analysis.

Takes the six LensVerdicts produced post-HITL and produces an AgreementMap:
  - consensus_verdict: majority vote with conservative tie-break (ties favour
    the most conservative verdict — six lenses makes 3-3 splits more likely)
  - agreeing / dissenting lenses
  - direct support-vs-oppose conflicts (named, not averaged)
  - shared_claim_conflicts: claim IDs cited in both support and oppose AxisVerdicts
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from schemas.verdicts import AgreementMap, LensVerdict

# Tie-break priority: lower index = more conservative (wins on tie)
_PRIORITY: dict[str, int] = {
    "insufficient_evidence": 0,
    "oppose": 1,
    "neutral": 2,
    "support": 3,
}


def reconcile(lens_verdicts: list[LensVerdict], run_id: UUID) -> AgreementMap:
    """Produce an AgreementMap from the full set of LensVerdicts.

    Deterministic — no LLM call. Safe to call with an empty list (returns
    an AgreementMap with consensus_verdict="insufficient_evidence").
    """
    if not lens_verdicts:
        return AgreementMap(run_id=run_id)

    # Deduplicate by lens name (newest verdict wins — _merge_by_lens already does this
    # in state, but guard here so the function is safe when called directly with raw lists).
    deduped = list({lv.lens: lv for lv in lens_verdicts}.values())

    verdicts_by_lens = {lv.lens: lv.overall_verdict for lv in deduped}
    counts: Counter[str] = Counter(verdicts_by_lens.values())

    # Majority vote; conservative tie-break (lower _PRIORITY index wins)
    max_count = max(counts.values())
    candidates = [v for v, c in counts.items() if c == max_count]
    consensus = min(candidates, key=lambda v: _PRIORITY.get(v, 99))

    agreeing = [lv.lens for lv in deduped if lv.overall_verdict == consensus]
    dissenting = [lv.lens for lv in deduped if lv.overall_verdict != consensus]

    # Named conflicts: every (support, oppose) lens pair
    support_lv = [lv for lv in deduped if lv.overall_verdict == "support"]
    oppose_lv = [lv for lv in deduped if lv.overall_verdict == "oppose"]
    conflicts = [
        {
            "lens_a": sa.lens,
            "lens_b": op.lens,
            "description": f"{sa.lens} supports while {op.lens} opposes",
        }
        for sa in support_lv
        for op in oppose_lv
    ]

    # Shared claim conflicts: claim IDs cited in both support and oppose AxisVerdicts
    support_ids: set[str] = set()
    for lv in support_lv:
        for ax in lv.axes:
            support_ids.update(ax.supporting_claim_ids)

    oppose_ids: set[str] = set()
    for lv in oppose_lv:
        for ax in lv.axes:
            oppose_ids.update(ax.supporting_claim_ids)

    shared = sorted(support_ids & oppose_ids)

    agreeing_confs = [lv.confidence for lv in deduped if lv.overall_verdict == consensus]
    consensus_confidence = (
        round(sum(agreeing_confs) / len(agreeing_confs), 4) if agreeing_confs else 0.0
    )

    return AgreementMap(
        run_id=run_id,
        verdicts_by_lens=verdicts_by_lens,
        consensus_verdict=consensus,
        consensus_confidence=consensus_confidence,
        agreeing_lenses=agreeing,
        dissenting_lenses=dissenting,
        conflicts=conflicts,
        shared_claim_conflicts=shared,
    )
