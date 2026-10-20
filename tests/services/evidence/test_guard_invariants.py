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

from services.evidence.clinical_trial_interpret import (
    ArmResult,
    TrialFact,
    normalize_phase,
    trial_count_phrases,
    trial_reported_values,
)
from services.evidence.constraint_interpret import (
    MENDELIAN_GWAS_ABSENCE_PHRASE,
    interpret_constraint,
)
from services.evidence.guard_core import annotate, strip_annotations
from services.evidence.guard_rules import GUARD_RULES, GuardFacts, run_family
from services.evidence.readings import build_dependency_reading

_TRIALS = [
    TrialFact(
        nct_id="NCT00000001",
        phase="PHASE2",
        status="COMPLETED",
        phase_numbers=normalize_phase("PHASE2"),
        interventions=("DRUG-A", "Placebo"),
        arm_results=(
            ArmResult(arm="DRUG-A 20 mg", p_value=0.0024, p_text="0.0024"),
            ArmResult(arm="DRUG-A 40 mg", p_value=0.7906, p_text="0.7906"),
            ArmResult(arm="DRUG-A 80 mg", p_value=0.1325, p_text="0.1325"),
        ),
    ),
    TrialFact(
        nct_id="NCT00000002",
        phase="PHASE3",
        status="RECRUITING",
        phase_numbers=normalize_phase("PHASE3"),
    ),
]

# A synthetic GENE-A × DISEASE-A fact sheet arms every family (LoF-tolerant, Mendelian-grade, contested indication,
# curated disease tissue, near-zero dependency, one Phase 2 + one Phase 3 trial).
_FACTS = GuardFacts(
    gene="GENE-A",
    disease="DISEASE-A",
    constraint=interpret_constraint("GENE-A", loeuf=0.759, pli=0.0, mis_z=1.70, moeuf=0.928),
    mendelian_grade=True,
    indication_approved_drug_count=1,
    indication_active_trial_count=38,
    top_tissues=["Lung", "Esophagus_Muscularis", "Thyroid"],
    disease_relevant_tissues=["Kidney_Cortex"],
    dependency=build_dependency_reading(
        None, {"num_fitness_lines": 2, "total_lines": 512, "bf_scaled_mean": 3.485}
    ),
    reported_outcomes=frozenset(),
    mouse_phenotypes=["increased vasoconstriction", "increased systemic arterial blood pressure"],
    tissue_ranks={"Lung": 1, "Esophagus_Muscularis": 2, "Thyroid": 3, "Kidney_Cortex": 29},
    drug_approval={"drug-a": False, "drug-b": True},
    cell_types=["target cell"],
    cell_types_reported=frozenset(),
    trial_facts=_TRIALS,
    trial_values=trial_reported_values(_TRIALS, []),
    genetic_favours_decrease=0.5,
    genetic_favours_increase=0.0,
)

_RECOMMENDED = sorted(
    {(family, p) for family, rule in GUARD_RULES.items() for p in rule.recommended_phrasings}
)

# Sentences each family must flag under _FACTS — used to produce real annotations.
_VIOLATING = {
    "constraint": "GENE-A shows haploinsufficiency and is missense intolerant.",
    "mendelian_floor": "The lack of GWAS support weakens causality.",
    "commercial": "The competitive field is underserved; the market size is unknown.",
    "clinical_trial": "DRUG-A is in two Phase 3 trials (NCT00000001 and NCT00000002).",
    "trial_outcome": "DRUG-A showed a significant reduction in proteinuria (NCT00000001).",
    "outcome_terms": "Stable disease was observed in the Phase 2 trial.",
    "tissue_relevance": "Lung is the disease-relevant tissue for DISEASE-A.",
    "dependency_relevance": "DepMap dependency supports GENE-A as a target.",
    "quantity": "GENE-A is a dependency in several cancer cell lines.",
    "phenotype_grounding": "GENE-A knockout mice are embryonic lethal.",
    "tissue_claim": "GENE-A is a kidney-specific protein.",
    "drug_approval": "DRUG-A is approved for DISEASE-A.",
    "trial_readout": "NCT00000002 showed a significant reduction in proteinuria.",
    "modality": "Inclisiran is a small molecule.",
    "cell_type_claim": "GENE-A is highly expressed in target cells of the kidney.",
    "trial_attribution": "NCT00000001 reduced proteinuria by 61% (hazard ratio 0.50).",
    "direction": "Genetic proxies show that lowering GENE-A activity increases DISEASE-A risk.",
    "phenotype_omission": (
        "GENE-A inhibition looks safe. " * 20
        + "Knockout mice show no relevant findings and expression supports the indication."
    ),
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
        "GENE-A is relatively LoF-tolerant (NOT haploinsufficient).",
        run_family(source, _VIOLATING[source], _FACTS),
    )
    assert (
        strip_annotations(annotated) == "GENE-A is relatively LoF-tolerant (NOT haploinsufficient)."
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


def test_trial_count_prompt_phrasing_passes_every_family() -> None:
    """The per-phase wording the clinical prompt asks the lens to quote is never flagged."""
    phrases = trial_count_phrases(_FACTS.trial_facts)
    assert phrases == [
        "one Phase 2 trial (NCT00000001, completed)",
        "one Phase 3 trial (NCT00000002, recruiting)",
    ]
    sentence = f"DRUG-A has {'; '.join(phrases)}."
    for family in GUARD_RULES:
        assert not run_family(family, sentence, _FACTS), family
