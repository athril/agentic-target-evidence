# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/decision/reconciler.py."""

from __future__ import annotations

import uuid

import pytest

from schemas.verdicts import AgreementMap, AxisVerdict, LensVerdict
from services.decision.reconciler import reconcile


def _make_verdict(
    lens: str,
    overall_verdict: str,
    confidence: float = 0.7,
    *,
    run_id: uuid.UUID | None = None,
    axes: list[AxisVerdict] | None = None,
) -> LensVerdict:
    return LensVerdict(
        run_id=run_id or uuid.uuid4(),
        trace_id="trace-test",
        lens=lens,
        target_gene="BRCA1",
        disease="breast cancer",
        overall_verdict=overall_verdict,
        confidence=confidence,
        axes=axes or [],
        rationale="Test rationale.",
    )


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_reconcile_empty_returns_insufficient():
    run_id = uuid.uuid4()
    am = reconcile([], run_id=run_id)
    assert am.consensus_verdict == "insufficient_evidence"
    assert am.verdicts_by_lens == {}
    assert am.agreeing_lenses == []
    assert am.conflicts == []


# ---------------------------------------------------------------------------
# Majority vote
# ---------------------------------------------------------------------------


def test_reconcile_majority_support():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "support", run_id=run_id),
        _make_verdict("clinical", "neutral", run_id=run_id),
        _make_verdict("commercial", "insufficient_evidence", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "support"
    assert set(am.agreeing_lenses) == {"genetics", "biology", "safety"}
    assert set(am.dissenting_lenses) == {"clinical", "commercial"}


def test_reconcile_majority_oppose():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "oppose", run_id=run_id),
        _make_verdict("biology", "oppose", run_id=run_id),
        _make_verdict("safety", "oppose", run_id=run_id),
        _make_verdict("clinical", "neutral", run_id=run_id),
        _make_verdict("commercial", "support", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "oppose"
    assert len(am.agreeing_lenses) == 3


# ---------------------------------------------------------------------------
# Conservative tie-break
# ---------------------------------------------------------------------------


def test_reconcile_tie_favours_insufficient_evidence():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "insufficient_evidence", run_id=run_id),
        _make_verdict("clinical", "insufficient_evidence", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
    ]
    # support=2, insufficient_evidence=2, neutral=1 → tie on 2; conservative winner
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "insufficient_evidence"


def test_reconcile_tie_oppose_beats_support():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "oppose", run_id=run_id),
        _make_verdict("clinical", "oppose", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
    ]
    # support=2, oppose=2, neutral=1 → tie; oppose is more conservative than support
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "oppose"


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def test_reconcile_detects_support_oppose_conflict():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "oppose", run_id=run_id),
        _make_verdict("clinical", "neutral", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert len(am.conflicts) == 2  # genetics↔safety, biology↔safety
    conflict_pairs = {(c["lens_a"], c["lens_b"]) for c in am.conflicts}
    assert ("genetics", "safety") in conflict_pairs
    assert ("biology", "safety") in conflict_pairs


def test_reconcile_no_conflicts_when_all_support():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict(lens, "support", run_id=run_id)
        for lens in ("genetics", "biology", "safety", "clinical", "commercial")
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert am.conflicts == []
    assert am.shared_claim_conflicts == []


# ---------------------------------------------------------------------------
# Shared claim conflicts
# ---------------------------------------------------------------------------


def test_reconcile_detects_shared_claim_conflicts():
    run_id = uuid.uuid4()
    shared_id = "claim-abc-123"
    support_ax = AxisVerdict(
        axis="causality",
        verdict=True,
        confidence=0.9,
        rationale="ok",
        supporting_claim_ids=[shared_id, "claim-only-support"],
    )
    oppose_ax = AxisVerdict(
        axis="toxicity",
        verdict=False,
        confidence=0.8,
        rationale="bad",
        supporting_claim_ids=[shared_id, "claim-only-oppose"],
    )
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id, axes=[support_ax]),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "oppose", run_id=run_id, axes=[oppose_ax]),
        _make_verdict("clinical", "neutral", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert shared_id in am.shared_claim_conflicts
    assert "claim-only-support" not in am.shared_claim_conflicts
    assert "claim-only-oppose" not in am.shared_claim_conflicts


# ---------------------------------------------------------------------------
# Consensus confidence
# ---------------------------------------------------------------------------


def test_reconcile_consensus_confidence_is_mean_of_agreeing():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", confidence=0.8, run_id=run_id),
        _make_verdict("biology", "support", confidence=0.6, run_id=run_id),
        _make_verdict("safety", "neutral", confidence=0.5, run_id=run_id),
        _make_verdict("clinical", "oppose", confidence=0.9, run_id=run_id),
        _make_verdict("commercial", "neutral", confidence=0.4, run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    # neutral=2, support=2, oppose=1 → tie: oppose beats neutral? No: oppose(1) < neutral(2)
    # neutral wins majority with 2 votes; confidence = mean(0.5, 0.4) = 0.45
    assert am.consensus_verdict == "neutral"
    assert am.consensus_confidence == pytest.approx(0.45, abs=1e-4)


# ---------------------------------------------------------------------------
# AgreementMap round-trip
# ---------------------------------------------------------------------------


def test_agreement_map_round_trip():
    run_id = uuid.uuid4()
    am = AgreementMap(
        run_id=run_id,
        verdicts_by_lens={"genetics": "support", "biology": "support"},
        consensus_verdict="support",
        consensus_confidence=0.85,
        agreeing_lenses=["genetics", "biology"],
        dissenting_lenses=[],
        conflicts=[],
        shared_claim_conflicts=[],
    )
    dumped = am.model_dump(mode="json")
    restored = AgreementMap.from_dict(dumped)
    assert restored == am


# ---------------------------------------------------------------------------
# Six-lens reconciler (regulatory lens included)
# ---------------------------------------------------------------------------


def test_reconcile_six_lenses_majority_support():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "support", run_id=run_id),
        _make_verdict("clinical", "neutral", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
        _make_verdict("regulatory", "support", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "support"
    assert "regulatory" in am.verdicts_by_lens
    assert len(am.verdicts_by_lens) == 6


def test_reconcile_six_lenses_three_three_tie_conservative():
    """3-3 split between support and neutral → conservative tie-break picks neutral."""
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "support", run_id=run_id),
        _make_verdict("clinical", "neutral", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
        _make_verdict("regulatory", "neutral", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "neutral"
    assert set(am.agreeing_lenses) == {"clinical", "commercial", "regulatory"}
    assert set(am.dissenting_lenses) == {"genetics", "biology", "safety"}


def test_reconcile_six_lenses_support_oppose_tie_conservative():
    """3-3 split between support and oppose → conservative tie-break picks oppose."""
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "support", run_id=run_id),
        _make_verdict("clinical", "oppose", run_id=run_id),
        _make_verdict("commercial", "oppose", run_id=run_id),
        _make_verdict("regulatory", "oppose", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert am.consensus_verdict == "oppose"
    assert len(am.conflicts) == 9  # 3 support × 3 oppose = 9 pairs


def test_reconcile_regulatory_lens_in_agreement_map():
    run_id = uuid.uuid4()
    verdicts = [
        _make_verdict("genetics", "support", run_id=run_id),
        _make_verdict("biology", "support", run_id=run_id),
        _make_verdict("safety", "neutral", run_id=run_id),
        _make_verdict("clinical", "support", run_id=run_id),
        _make_verdict("commercial", "neutral", run_id=run_id),
        _make_verdict("regulatory", "support", run_id=run_id),
    ]
    am = reconcile(verdicts, run_id=run_id)
    assert "regulatory" in am.verdicts_by_lens
    assert am.verdicts_by_lens["regulatory"] == "support"
    assert "regulatory" in am.agreeing_lenses
