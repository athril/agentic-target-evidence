# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Properties every guard family must satisfy, independent of any one sentence.

1. **Compliance is never punished.** Every family's recommended phrasing — the wording
   its prompt framing and its own annotation steer the LLM toward — passes every
   family. Historical false positives (Mendelian "absence is expected", "Orphanet
   prevalence is unknown", "underserved at the target level", "non-disease-relevant
   tissues") were all guards flagging the phrasing another part of the system asked for.
2. **Guards never react to annotations.** A note quotes the phrasing it warns about;
   another guard (or the same one on a second pass) must not flag the note.
3. **Annotation is append-only and idempotent.**
"""

from __future__ import annotations

import pytest

from services.evidence.clinical_trial_interpret import TrialFact, normalize_phase
from services.evidence.constraint_interpret import (
    MENDELIAN_GWAS_ABSENCE_PHRASE,
    interpret_constraint,
)
from services.evidence.guard_core import annotate, strip_annotations
from services.evidence.guard_rules import GUARD_RULES, GuardFacts, run_family
from services.evidence.readings import build_dependency_reading

# TRPC6 × FSGS arms every family (LoF-tolerant, Mendelian-grade, contested indication,
# curated disease tissue, near-zero dependency, one Phase 2 + one Phase 3 trial).
_FACTS = GuardFacts(
    gene="TRPC6",
    disease="FSGS",
    constraint=interpret_constraint("TRPC6", loeuf=0.759, pli=0.0, mis_z=1.70, moeuf=0.928),
    mendelian_grade=True,
    indication_approved_drug_count=1,
    indication_active_trial_count=38,
    top_tissues=["Lung", "Esophagus_Muscularis", "Thyroid"],
    disease_relevant_tissues=["Kidney_Cortex"],
    dependency=build_dependency_reading(
        None, {"num_fitness_lines": 2, "total_lines": 512, "bf_scaled_mean": 3.485}
    ),
    trial_facts=[
        TrialFact(
            nct_id="NCT05213624",
            phase="PHASE2",
            status="COMPLETED",
            phase_numbers=normalize_phase("PHASE2"),
        ),
        TrialFact(
            nct_id="NCT07220083",
            phase="PHASE3",
            status="RECRUITING",
            phase_numbers=normalize_phase("PHASE3"),
        ),
    ],
)

_RECOMMENDED = sorted(
    {(family, p) for family, rule in GUARD_RULES.items() for p in rule.recommended_phrasings}
)

# Sentences each family must flag under _FACTS — used to produce real annotations.
_VIOLATING = {
    "constraint": "TRPC6 shows haploinsufficiency and is missense intolerant.",
    "mendelian_floor": "The lack of GWAS support weakens causality.",
    "commercial": "The competitive field is underserved; the market size is unknown.",
    "clinical_trial": "BI 764198 is in two Phase 3 trials (NCT05213624 and NCT07220083).",
    "tissue_relevance": "Lung is the disease-relevant tissue for FSGS.",
    "dependency_relevance": "DepMap dependency supports TRPC6 as a target.",
    "quantity": "TRPC6 is a dependency in several cancer cell lines.",
}


def test_every_family_is_armed_by_the_reference_facts() -> None:
    for family, rule in GUARD_RULES.items():
        assert rule.is_armed(_FACTS), family


def test_every_family_declares_recommended_phrasings() -> None:
    for family, rule in GUARD_RULES.items():
        assert rule.recommended_phrasings, f"{family} declares no recommended phrasing"


def test_genetics_prompt_phrase_is_a_recommended_phrasing() -> None:
    assert MENDELIAN_GWAS_ABSENCE_PHRASE in GUARD_RULES["mendelian_floor"].recommended_phrasings


@pytest.mark.parametrize("family", sorted(GUARD_RULES))
@pytest.mark.parametrize(("owner", "phrase"), _RECOMMENDED)
def test_no_family_flags_any_recommended_phrasing(family: str, owner: str, phrase: str) -> None:
    violations = run_family(family, phrase, _FACTS)
    assert not violations, (
        f"{family} flags {owner}'s recommended phrasing {phrase!r}: "
        f"{[v.rule_id for v in violations]}"
    )


@pytest.mark.parametrize("family", sorted(_VIOLATING))
def test_violating_reference_sentence_fires(family: str) -> None:
    assert run_family(family, _VIOLATING[family], _FACTS), family


@pytest.mark.parametrize("source", sorted(_VIOLATING))
@pytest.mark.parametrize("family", sorted(GUARD_RULES))
def test_no_family_flags_another_familys_annotation(source: str, family: str) -> None:
    notes = [v.note for v in run_family(source, _VIOLATING[source], _FACTS)]
    annotated = annotate(
        "TRPC6 is relatively LoF-tolerant (NOT haploinsufficient).",
        run_family(source, _VIOLATING[source], _FACTS),
    )
    assert (
        strip_annotations(annotated) == "TRPC6 is relatively LoF-tolerant (NOT haploinsufficient)."
    )
    for note in notes:
        assert not run_family(family, note, _FACTS), f"{family} flagged a {source} note"


@pytest.mark.parametrize("family", sorted(_VIOLATING))
def test_annotation_is_idempotent(family: str) -> None:
    text = _VIOLATING[family]
    once = annotate(text, run_family(family, text, _FACTS))
    twice = annotate(once, run_family(family, once, _FACTS))
    assert once != text
    assert twice == once
    assert once.startswith(text), "annotation must append, never rewrite"
