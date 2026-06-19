# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/decision/suitability.py (WS5: Mendelian causality score floor)."""

from __future__ import annotations

from pathlib import Path

from services.decision import suitability
from services.decision.suitability import apply_mendelian_score_floor, get_mendelian_score_floor


class TestGetMendelianScoreFloor:
    def test_reads_value_from_config_file(self, tmp_path: Path):
        cfg = tmp_path / "scoring.yaml"
        cfg.write_text("mendelian_causality_score_floor: 65\n")
        assert get_mendelian_score_floor(cfg) == 65

    def test_defaults_when_file_missing(self, tmp_path: Path):
        cfg = tmp_path / "does_not_exist.yaml"
        assert get_mendelian_score_floor(cfg) == suitability._DEFAULT_FLOOR

    def test_repo_config_file_is_loadable(self):
        repo_config = Path("config/scoring.yaml")
        floor = get_mendelian_score_floor(repo_config)
        assert isinstance(floor, int)
        assert floor == 70


class TestApplyMendelianScoreFloor:
    def test_noop_when_not_mendelian_grade(self):
        results = [{"target": "X", "score": 10, "rationale": "weak"}]
        out = apply_mendelian_score_floor(results, mendelian_grade=False, floor=70)
        assert out == results

    def test_raises_low_score_to_floor(self):
        results = [{"target": "TRPC6", "score": 30, "rationale": "Weak clinical signal."}]
        out = apply_mendelian_score_floor(results, mendelian_grade=True, floor=70)
        assert out[0]["score"] == 70
        assert "Mendelian causality floor" in out[0]["rationale"]
        assert "Weak clinical signal." in out[0]["rationale"]

    def test_does_not_lower_score_already_above_floor(self):
        results = [{"target": "TRPC6", "score": 92, "rationale": "Strong."}]
        out = apply_mendelian_score_floor(results, mendelian_grade=True, floor=70)
        assert out[0]["score"] == 92
        assert out[0]["rationale"] == "Strong."

    def test_score_exactly_at_floor_left_unchanged(self):
        results = [{"target": "TRPC6", "score": 70, "rationale": "Borderline."}]
        out = apply_mendelian_score_floor(results, mendelian_grade=True, floor=70)
        assert out[0]["score"] == 70
        assert out[0]["rationale"] == "Borderline."

    def test_default_floor_reads_from_repo_config_when_unspecified(self):
        results = [{"target": "TRPC6", "score": 30, "rationale": "Weak."}]
        out = apply_mendelian_score_floor(results, mendelian_grade=True)
        assert out[0]["score"] == 70
