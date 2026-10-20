# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-token estimates from the model's measured characters per token (core.tokens)."""

from __future__ import annotations

import json

from core.tokens import DEFAULT_RATIO, TokenEstimator


def test_the_default_ratio_applies_before_any_measurement(tmp_path):
    est = TokenEstimator(tmp_path / "r.json")
    assert est.ratio("m") == DEFAULT_RATIO
    assert est.estimate("x" * 3000, "m") == 1000


def test_the_ratio_is_the_median_of_the_models_last_samples(tmp_path):
    est = TokenEstimator(tmp_path / "r.json")
    for chars, toks in [(9000, 3000), (8000, 2000), (5000, 2000)]:  # 3.0, 4.0, 2.5
        est.observe("m", chars, toks)
    assert est.ratio("m") == 3.0
    for _ in range(60):
        est.observe("m", 4000, 1000)  # 4.0, fills the window
    assert est.ratio("m") == 4.0


def test_ratios_are_kept_per_model_and_an_unmeasured_model_borrows_the_last(tmp_path):
    est = TokenEstimator(tmp_path / "r.json")
    est.observe("a", 9000, 3000)
    est.observe("b", 8000, 2000)
    assert est.ratio("a") == 3.0 and est.ratio("b") == 4.0
    assert est.ratio("unknown") == 4.0 and est.ratio() == 4.0


def test_implausible_or_tiny_samples_are_ignored(tmp_path):
    """A reused prompt prefix makes the server count only new tokens: a huge ratio."""
    est = TokenEstimator(tmp_path / "r.json")
    est.observe("m", 30_000, 100)  # 300 chars per token: a cache hit
    est.observe("m", 500, 100)  # too short to measure
    est.observe("m", 9000, 0)
    assert est.ratio("m") == DEFAULT_RATIO


def test_ratios_persist_and_reload(tmp_path):
    path = tmp_path / "r.json"
    TokenEstimator(path).observe("m", 7000, 2000)
    assert json.loads(path.read_text()) == {"m": [3.5]}
    reloaded = TokenEstimator(path)
    assert reloaded.ratio("m") == 3.5 and reloaded.ratio() == 3.5


def test_an_unreadable_file_falls_back_to_the_default(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("not json")
    assert TokenEstimator(path).ratio("m") == DEFAULT_RATIO
