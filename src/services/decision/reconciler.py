# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Reconciler service — deterministic cross-lens agreement analysis.

Takes the six LensVerdicts produced post-HITL and produces an AgreementMap:
  - consensus_verdict: the lens balance — the lens verdicts weighted by the same lens
    weights and confidences as the suitability score (config/scoring.yaml). An
    unweighted majority let three supporting lenses outvote a failed phase 3 programme
    and print "Support" above a DEPRIORITISE score. It is not the decision: the score,
    with its precedent and direction gates, is.
  - consensus_confidence: the share of the voting lens weight that agrees with it
  - agreeing / dissenting lenses
  - direct support-vs-oppose conflicts (named, not averaged)
  - shared_claim_conflicts: claim IDs cited in both support and oppose AxisVerdicts
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from schemas.verdicts import AgreementMap, LensVerdict
from services.decision.suitability import load_scoring_config

# |weighted signal| below this is a split, reported as neutral.
_BALANCE_MARGIN = 0.1
_SIGN = {"support": 1.0, "oppose": -1.0}


def _lens_weights() -> dict[str, float]:
    return load_scoring_config().lens_weights


def _weighted_balance(
    verdicts: list[LensVerdict], weights: dict[str, float]
) -> Literal["support", "oppose", "neutral", "insufficient_evidence"]:
    """Sign of Σ weight × sign × confidence over all lens weight (see module docstring)."""
    if all(lv.overall_verdict == "insufficient_evidence" for lv in verdicts):
        return "insufficient_evidence"
    total = sum(weights.values()) or 1.0
    signal = (
        sum(
            weights.get(lv.lens, 0.0) * _SIGN.get(lv.overall_verdict, 0.0) * lv.confidence
            for lv in verdicts
        )
        / total
    )
    if signal >= _BALANCE_MARGIN:
        return "support"
    if signal <= -_BALANCE_MARGIN:
        return "oppose"
    return "neutral"


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
    consensus = _weighted_balance(deduped, _lens_weights())

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

    weights = _lens_weights()
    voting = [lv for lv in deduped if lv.overall_verdict != "insufficient_evidence"]
    voting_weight = sum(weights.get(lv.lens, 0.0) for lv in voting)
    agreeing_weight = sum(
        weights.get(lv.lens, 0.0) for lv in voting if lv.overall_verdict == consensus
    )
    consensus_confidence = round(agreeing_weight / voting_weight, 4) if voting_weight else 0.0

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
