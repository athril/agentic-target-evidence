# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression gate: every guard family against the labelled corpus.

tests/fixtures/guard_corpus.yaml holds real sentences from historical runs plus
synthetic contrast cases, each labelled fire / no_fire from the facts. A guard change
that turns a labelled case into a false positive or a missed violation fails here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.evidence.clinical_trial_interpret import TrialFact, normalize_phase
from services.evidence.constraint_interpret import interpret_constraint
from services.evidence.guard_rules import GUARD_RULES, GuardFacts, run_family
from services.evidence.readings import build_dependency_reading

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "guard_corpus.yaml"
_CORPUS: dict[str, Any] = yaml.safe_load(_CORPUS_PATH.read_text(encoding="utf-8"))


def _facts(name: str) -> GuardFacts:
    raw = dict(_CORPUS["facts"][name])
    gene = raw.get("gene", "")
    constraint = raw.pop("constraint", None)
    depmap = raw.pop("depmap", None)
    project_score = raw.pop("project_score", None)
    trials = raw.pop("trials", None) or []
    return GuardFacts(
        **raw,
        constraint=interpret_constraint(gene, **constraint) if constraint else None,
        dependency=build_dependency_reading(depmap, project_score),
        trial_facts=[TrialFact(**t, phase_numbers=normalize_phase(t.get("phase"))) for t in trials],
    )


def _params() -> list[Any]:
    params = []
    for case in _CORPUS["cases"]:
        marks = [pytest.mark.xfail(strict=True, reason=case["xfail"])] if case.get("xfail") else []
        params.append(pytest.param(case, id=case["id"], marks=marks))
    return params


@pytest.mark.parametrize("case", _params())
def test_guard_corpus_case(case: dict[str, Any]) -> None:
    violations = run_family(case["family"], case["text"], _facts(case["facts"]))
    rule_ids = {v.rule_id for v in violations}
    if case["expect"] == "no_fire":
        assert not violations, f"false positive {sorted(rule_ids)} on: {case['text']}"
        return
    assert violations, f"missed violation ({case.get('rule', case['family'])}): {case['text']}"
    if case.get("rule"):
        assert case["rule"] in rule_ids
    if case.get("kind"):
        assert case["kind"] in {v.kind for v in violations if v.rule_id == case["rule"]}


def test_corpus_is_well_formed() -> None:
    ids = [c["id"] for c in _CORPUS["cases"]]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in _CORPUS["cases"]:
        assert c["family"] in GUARD_RULES, c["id"]
        assert c["facts"] in _CORPUS["facts"], c["id"]
        assert c["expect"] in {"fire", "no_fire"}, c["id"]
        assert c.get("source"), c["id"]


def test_corpus_covers_both_outcomes_for_every_family() -> None:
    """Each family needs at least one true positive and one compliant case — a corpus
    with only fire cases cannot catch false positives, and vice versa."""
    seen = Counter((c["family"], c["expect"]) for c in _CORPUS["cases"])
    for family in GUARD_RULES:
        assert seen[(family, "fire")] >= 1, f"{family}: no fire case"
        assert seen[(family, "no_fire")] >= 1, f"{family}: no no_fire case"
