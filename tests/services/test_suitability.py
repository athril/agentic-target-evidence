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
