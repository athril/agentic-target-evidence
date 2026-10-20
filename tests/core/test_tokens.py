# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-token estimates from measured characters per token (core.tokens).

Regression lock: one ratio pooled over screening prose (~3.7 chars/token) and lens JSON
(~3.1) undercounted lens prompts by 10–13%, and a lens prompt filled the window.
"""

from __future__ import annotations

import json

from core.tokens import DEFAULT_RATIO, TokenEstimator


def _observe(est: TokenEstimator, task: str, *ratios: float, model: str = "m") -> None:
    for r in ratios:
        est.observe(model, int(r * 1000), 1000, task)


def test_the_default_ratio_applies_before_any_measurement(tmp_path):
    est = TokenEstimator(tmp_path / "r.json")
    assert est.ratio("m", "lens") == DEFAULT_RATIO
    assert est.estimate("x" * 3000, "m", "lens") == 1000


def test_each_task_has_its_own_ratio_at_its_lower_quartile(tmp_path):
    est = TokenEstimator(tmp_path / "r.json")
    _observe(est, "screening", 3.6, 3.8, 4.0, 3.7, 3.9)
    _observe(est, "biology_lens", 3.0, 3.1, 3.2, 3.3, 3.4)
    assert est.ratio("m", "screening") == 3.7
    assert est.ratio("m", "biology_lens") == 3.1


def test_an_unmeasured_task_borrows_the_densest_measured_task(tmp_path):
    """Undercounting is the costly error, so the lowest ratio stands in."""
    est = TokenEstimator(tmp_path / "r.json")
    _observe(est, "screening", 3.8, 3.8, 3.8)
    _observe(est, "genetics_lens", 3.1, 3.1, 3.1)
    _observe(est, "rare", 5.0)  # too few samples to stand for itself
    assert est.ratio("m", "biology_lens") == 3.1
    assert est.ratio("m", "rare") == 3.1
    assert est.ratio(None, "biology_lens") == 3.1  # the last measured model


def test_the_window_keeps_the_last_samples(tmp_path):
    est = TokenEstimator(tmp_path / "r.json")
    _observe(est, "t", *([2.0] * 10))
    _observe(est, "t", *([4.0] * 60))
    assert est.ratio("m", "t") == 4.0


def test_implausible_or_tiny_samples_are_ignored(tmp_path):
    """A reused prompt prefix makes the server count only new tokens: a huge ratio."""
    est = TokenEstimator(tmp_path / "r.json")
    est.observe("m", 30_000, 100, "t")  # 300 chars per token: a cache hit
    est.observe("m", 500, 100, "t")  # too short to measure
    est.observe("m", 9000, 0, "t")
    assert est.ratio("m", "t") == DEFAULT_RATIO


def test_ratios_persist_per_task_and_pooled_entries_are_dropped(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"m": [3.9, 3.9, 3.9]}))  # the pooled format
    est = TokenEstimator(path)
    assert est.ratio("m", "t") == DEFAULT_RATIO
    _observe(est, "t", 3.5, 3.5, 3.5)
    assert json.loads(path.read_text()) == {"m|t": [3.5, 3.5, 3.5]}
    reloaded = TokenEstimator(path)
    assert reloaded.ratio("m", "t") == 3.5 and reloaded.ratio(None, "other") == 3.5


def test_an_unreadable_file_falls_back_to_the_default(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("not json")
    assert TokenEstimator(path).ratio("m") == DEFAULT_RATIO
