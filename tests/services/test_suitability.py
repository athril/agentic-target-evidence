# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/decision/suitability.py — the deterministic suitability score.

The reference profiles are lens verdicts an expert assessment expects for well-known
target × disease situations (a clinically validated target, a genetically validated one
awaiting clinical proof, an unvalidated gene, one with severe liabilities). The score must
order them and band them the way a reviewer would, independently of any LLM output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.decision.suitability import ScoringConfig, compute_suitability, load_scoring_config


def _v(**lenses: tuple[str, float]) -> list[dict]:
    return [{"lens": k, "overall_verdict": v, "confidence": c} for k, (v, c) in lenses.items()]


# Validated: human genetics, outcome trials, approved drugs; commercial headwinds.
_VALIDATED = _v(
    genetics=("support", 0.9),
    biology=("support", 0.9),
    clinical=("support", 0.95),
    safety=("support", 0.75),
    commercial=("oppose", 0.6),
    regulatory=("support", 0.85),
)
# Mendelian-grade genetics, plausible biology, no clinical proof yet, organ liabilities.
_GENETIC_ONLY = _v(
    genetics=("support", 0.9),
    biology=("support", 0.7),
    clinical=("neutral", 0.5),
    safety=("neutral", 0.6),
    commercial=("neutral", 0.5),
    regulatory=("neutral", 0.4),
)
# Uncharacterised gene: weak association, little else.
_UNVALIDATED = _v(
    genetics=("neutral", 0.4),
    biology=("neutral", 0.4),
    clinical=("insufficient_evidence", 0.0),
    safety=("insufficient_evidence", 0.0),
    commercial=("support", 0.5),
    regulatory=("insufficient_evidence", 0.0),
)
# Genetics supports, but safety opposes on severe liabilities.
_UNSAFE = _v(
    genetics=("support", 0.9),
    biology=("support", 0.7),
    clinical=("neutral", 0.5),
    safety=("oppose", 0.8),
    commercial=("neutral", 0.5),
    regulatory=("neutral", 0.4),
)
_ORGAN = {"severity": "medium"}
_SEVERE = {"severity": "high"}


def test_reference_profiles_are_ordered_and_banded():
    validated = compute_suitability(_VALIDATED, liabilities=[_ORGAN], human_exposure=True)
    genetic = compute_suitability(_GENETIC_ONLY, liabilities=[_ORGAN] * 3, mendelian_grade=True)
    unvalidated = compute_suitability(_UNVALIDATED)
    unsafe = compute_suitability(
        _UNSAFE, liabilities=[_SEVERE, _SEVERE, _ORGAN], mendelian_grade=True
    )
    assert validated.score > genetic.score > unsafe.score
    assert genetic.score > unvalidated.score
    assert validated.band == "PROCEED"
    assert genetic.band == "PROCEED WITH CAUTION"
    assert unvalidated.band in ("INVESTIGATE FURTHER", "DEPRIORITISE")
    assert unsafe.band in ("INVESTIGATE FURTHER", "DEPRIORITISE")


def test_missing_lenses_pull_toward_uncertain():
    one = compute_suitability(_v(genetics=("support", 1.0)))
    assert one.score == 62  # 50 + 50 × 0.25
    assert one.lenses_with_verdict == 1
    assert any("1/6 lenses" in n for n in one.notes)


def test_liabilities_subtract_and_human_exposure_halves_them():
    base = compute_suitability(_VALIDATED).evidence_score
    hit = compute_suitability(_VALIDATED, liabilities=[_SEVERE, _ORGAN])
    assert hit.liability_penalty == 5.5
    softened = compute_suitability(_VALIDATED, liabilities=[_SEVERE, _ORGAN], human_exposure=True)
    assert abs(softened.liability_penalty - 2.75) <= 0.05
    assert softened.score > hit.score
    assert round(base - hit.liability_penalty) == hit.score


def test_liability_penalty_is_capped():
    s = compute_suitability(_VALIDATED, liabilities=[_SEVERE] * 20)
    assert s.liability_penalty == load_scoring_config().liability_penalty_cap


def test_mendelian_floor_applies_only_without_safety_opposition_or_open_severe_liability():
    weak = _v(genetics=("support", 0.6))
    assert compute_suitability(weak, mendelian_grade=True).score == 70
    assert compute_suitability(weak, mendelian_grade=False).score < 70
    blocked = compute_suitability(weak, mendelian_grade=True, liabilities=[_SEVERE])
    assert not blocked.mendelian_floor_applied and blocked.score < 70
    exposed = compute_suitability(
        weak, mendelian_grade=True, liabilities=[_SEVERE], human_exposure=True
    )
    assert exposed.mendelian_floor_applied


def test_contributions_sum_to_the_evidence_score():
    s = compute_suitability(_VALIDATED)
    assert abs(50 + sum(c.points for c in s.contributions) - s.evidence_score) < 0.3


def test_config_overrides_weights_and_bands(tmp_path: Path):
    cfg = tmp_path / "scoring.yaml"
    cfg.write_text(
        "lens_weights: {genetics: 1.0, biology: 0, clinical: 0, safety: 0, regulatory: 0, "
        "commercial: 0}\nbands:\n  - {min: 90, label: GO}\n  - {min: 0, label: NO-GO}\n"
        "mendelian_causality_score_floor: 65\n"
    )
    loaded = load_scoring_config(cfg)
    assert loaded.mendelian_floor == 65
    s = compute_suitability(_v(genetics=("support", 0.9)), config=loaded)
    assert s.score == 95 and s.band == "GO"


def test_repo_config_loads():
    cfg = load_scoring_config(Path("config/scoring.yaml"))
    assert isinstance(cfg, ScoringConfig)
    assert abs(sum(cfg.lens_weights.values()) - 1.0) < 1e-9
    assert cfg.mendelian_floor == 70


@pytest.mark.parametrize("verdict", ["support", "oppose"])
def test_score_stays_in_range(verdict):
    s = compute_suitability(_v(**{k: (verdict, 1.0) for k in load_scoring_config().lens_weights}))
    assert 0 <= s.score <= 100


def test_direction_gate_caps_a_contradicted_direction():
    """No drug in the indication to oppose it, but human genetics and labels say the
    other way helps: the lens average alone scored such a request in the 60s."""
    s = compute_suitability(_VALIDATED, direction_verdict="contradicts", direction_against=0.73)
    assert s.score == 30 and s.direction_gate == "cap 30"
    assert any("Direction-of-effect cap 30" in n for n in s.notes)


@pytest.mark.parametrize(
    "verdict,against", [("contradicts", 0.45), ("conflicting", 0.9), ("supports", 0.0), ("", 0.0)]
)
def test_direction_gate_needs_a_clear_contradiction(verdict, against):
    s = compute_suitability(_VALIDATED, direction_verdict=verdict, direction_against=against)
    assert s.direction_gate == "" and s.score > 30


def test_stalled_precedent_caps_below_proceed_with_caution():
    """Late-stage attrition with no approval allowed PROCEED WITH CAUTION (cap 74)."""
    s = compute_suitability(_VALIDATED, precedent_status="stalled")
    assert s.score == 59 and s.band == "INVESTIGATE FURTHER"


# --------------------------------------------------------- programme-outcome gates


def test_a_weak_programme_strength_does_not_cap_a_failed_precedent():
    """One futility-stopped phase 2 capped a supported target at 44 like a failed class."""
    s = compute_suitability(_VALIDATED, precedent_status="failed", programme_strength=0.18)
    assert s.programme_gate == "" and s.precedent_gate == ""
    assert s.score > 59


def test_a_moderate_programme_strength_caps_below_proceed_with_caution():
    s = compute_suitability(_VALIDATED, precedent_status="failed", programme_strength=0.33)
    assert s.score == 59 and s.band == "INVESTIGATE FURTHER"
    assert s.programme_gate == "cap 59" and s.precedent_gate == ""
    assert any("Programme-outcome cap 59" in n and "stalled or failed" in n for n in s.notes)


def test_a_strong_programme_strength_caps_below_investigate():
    s = compute_suitability(_VALIDATED, precedent_status="stalled", programme_strength=0.67)
    assert s.score == 44 and s.band == "DEPRIORITISE"
    assert s.programme_gate == "cap 44"
    assert any("mechanism failed in several programmes" in n for n in s.notes)


def test_programme_gate_replaces_the_stalled_cap():
    """Stalled with a weak strength: the flat 59 no longer applies."""
    flat = compute_suitability(_VALIDATED, precedent_status="stalled")
    gated = compute_suitability(_VALIDATED, precedent_status="stalled", programme_strength=0.1)
    assert flat.score == 59 and gated.score > 59


@pytest.mark.parametrize("strength", [None, 0.0])
def test_without_a_strength_the_flat_caps_apply(strength):
    """Older callers and rows without stop reports keep today's behaviour (None); a strength
    of exactly zero is a real reading, not a missing one."""
    s = compute_suitability(_VALIDATED, precedent_status="failed", programme_strength=strength)
    if strength is None:
        assert s.score == 44 and s.precedent_gate == "cap 44" and s.programme_gate == ""
    else:
        assert s.score > 44 and s.programme_gate == ""


@pytest.mark.parametrize("status", ["validated", "opposed"])
def test_programme_strength_leaves_validated_and_opposed_alone(status):
    plain = compute_suitability(_VALIDATED, precedent_status=status)
    gated = compute_suitability(_VALIDATED, precedent_status=status, programme_strength=0.9)
    assert (gated.score, gated.precedent_gate, gated.programme_gate) == (
        plain.score,
        plain.precedent_gate,
        "",
    )


@pytest.mark.parametrize("status", ["late_stage", "in_clinic", "untested", ""])
def test_programme_strength_does_not_gate_statuses_that_had_no_cap(status):
    """A running phase 3 next to an earlier failure was never capped; it still is not."""
    s = compute_suitability(_VALIDATED, precedent_status=status, programme_strength=0.9)
    assert s.programme_gate == "" and s.score == compute_suitability(_VALIDATED).score


def test_the_direction_gate_still_applies_after_the_programme_gate():
    s = compute_suitability(
        _VALIDATED,
        precedent_status="failed",
        programme_strength=0.18,
        direction_verdict="contradicts",
        direction_against=0.87,
    )
    assert s.score == 30 and s.direction_gate == "cap 30" and s.programme_gate == ""


def test_programme_gates_come_from_config():
    from services.evidence.programme_outcomes import ProgrammeGate

    cfg = ScoringConfig(programme_gates=[ProgrammeGate(min_strength=0.5, max=20, reason="test")])
    s = compute_suitability(
        _VALIDATED, precedent_status="failed", programme_strength=0.5, config=cfg
    )
    assert s.score == 20


def test_repo_config_carries_the_programme_gates():
    cfg = load_scoring_config(Path("config/scoring.yaml"))
    assert [(g.min_strength, g.max) for g in cfg.programme_gates] == [(0.6, 44), (0.3, 59)]


# ---- liabilities the safety lens left open ----------------------------------------


def test_liabilities_the_safety_lens_resolved_subtract_nothing():
    s = compute_suitability(
        _VALIDATED,
        liabilities=[_SEVERE, _ORGAN],
        liability_statuses=["resolved_by_human_data", "resolved_by_human_data"],
    )
    assert s.liability_penalty == 0.0
    assert "0 of 2 candidate liabilities counted" in s.notes[0]


def test_unresolved_and_confirmed_liabilities_still_count():
    s = compute_suitability(
        _VALIDATED,
        liabilities=[_SEVERE, _ORGAN, _SEVERE],
        liability_statuses=["unresolved", "resolved_by_human_data", "confirmed_in_humans"],
    )
    assert s.liability_penalty == 8.0  # two high, the medium one settled


def test_a_validated_precedent_subtracts_nothing():
    """A same-direction drug approved in the indication: its label is the accepted risk."""
    s = compute_suitability(
        _VALIDATED,
        liabilities=[_SEVERE, _ORGAN],
        liability_statuses=["unresolved", "confirmed_in_humans"],
        precedent_status="validated",
    )
    assert s.liability_penalty == 0.0


@pytest.mark.parametrize("statuses", [None, ["unresolved"]])  # none, or a different list
def test_without_matching_statuses_the_penalty_is_unchanged(statuses):
    today = compute_suitability(_VALIDATED, liabilities=[_SEVERE, _ORGAN])
    fallback = compute_suitability(
        _VALIDATED, liabilities=[_SEVERE, _ORGAN], liability_statuses=statuses
    )
    assert fallback.liability_penalty == today.liability_penalty == 5.5
    assert fallback.score == today.score


@pytest.mark.parametrize("n", [1, 3, 10, 30])
def test_more_records_of_an_accepted_risk_never_lower_the_score(n):
    """Well-studied targets accumulate label records; settled ones must not add up."""
    base = compute_suitability(_VALIDATED, liabilities=[], liability_statuses=[])
    more = compute_suitability(
        _VALIDATED,
        liabilities=[_SEVERE] * n,
        liability_statuses=["resolved_by_human_data"] * n,
    )
    assert more.score == base.score


def test_a_resolved_high_liability_does_not_block_the_mendelian_floor():
    weak = _v(genetics=("support", 0.2))
    blocked = compute_suitability(weak, mendelian_grade=True, liabilities=[_SEVERE])
    settled = compute_suitability(
        weak,
        mendelian_grade=True,
        liabilities=[_SEVERE],
        liability_statuses=["resolved_by_human_data"],
    )
    assert not blocked.mendelian_floor_applied
    assert settled.mendelian_floor_applied


def test_the_disease_worsening_liability_is_never_subtracted():
    """It is efficacy evidence the genetics and clinical lenses and the direction gate
    already count: the direction of effect enters the score once."""
    from services.evidence.liabilities import DIRECTION_HARM_SYSTEM

    worsening = {"system": DIRECTION_HARM_SYSTEM, "severity": "high"}
    without = compute_suitability(_VALIDATED, liabilities=[_SEVERE])
    single_call = compute_suitability(_VALIDATED, liabilities=[worsening, _SEVERE])
    split = compute_suitability(
        _VALIDATED,
        liabilities=[worsening, _SEVERE, _ORGAN],
        liability_statuses=["unresolved", "unresolved", "resolved_by_human_data"],
    )

    assert single_call.liability_penalty == without.liability_penalty == 4.0
    assert split.liability_penalty == 4.0  # statuses stay aligned with what is left
    assert "1 of 2 candidate liabilities counted" in split.notes[0]
