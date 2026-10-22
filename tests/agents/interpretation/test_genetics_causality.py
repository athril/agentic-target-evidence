# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Causality by rule (genetics_lens/causality.py): a curated record, or replicated human
genetics in independent sources.

The failures: a null causality beside "strong evidence of causality", and variants in a
partner gene, named next to the target, counted as the target's genetics.
"""

from __future__ import annotations

import uuid

import pytest

from agents.interpretation.genetics_lens.causality import (
    apply_causality,
    causality_axis,
    causality_reading,
    render_causality_block,
)
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.direction_of_effect import DirectionObservation


def _obs(source: str, kind: str = "genetics_literature", in_scope: bool = True):
    return DirectionObservation(
        perturbation="decrease",
        outcome="benefit",
        evidence=kind,
        in_scope=in_scope,
        weight=0.25,
        source=source,
        detail="d",
    )


def _reading(observations=(), gwas=(), curated=False, available=True, paper_of=None):
    return causality_reading(
        observations,
        gwas_studies=gwas,
        paper_of=paper_of or {},
        curated=curated,
        available=available,
    ).model_dump()


def test_sentences_from_one_paper_are_one_source() -> None:
    paper_of = {"c1": "paper-1", "c2": "paper-1"}
    reading = _reading([_obs("c1"), _obs("c2")], paper_of=paper_of)

    assert reading["sources"] == ["paper:paper-1"]
    assert causality_axis(reading).verdict is None


@pytest.mark.parametrize(
    ("observations", "gwas"),
    [
        ([_obs("c1"), _obs("c2")], ()),  # two papers
        ([_obs("c1")], ("gwas_catalog:STUDY1",)),  # a paper and a GWAS study
        ([_obs("Open Targets burden", kind="genetics_structured")], ("gwas_catalog:STUDY1",)),
    ],
)
def test_two_independent_sources_set_causality(observations, gwas) -> None:
    axis = causality_axis(_reading(observations, gwas))

    assert axis.verdict is True and axis.confidence == 0.6


def test_out_of_scope_observations_do_not_count() -> None:
    reading = _reading([_obs("c1", in_scope=False), _obs("c2", in_scope=False)])

    assert reading["sources"] == []


def test_a_curated_record_sets_causality_on_its_own() -> None:
    assert causality_axis(_reading(curated=True, available=False)).verdict is True


def test_without_target_statements_the_lens_decides() -> None:
    assert causality_axis(_reading([_obs("c1"), _obs("c2")], available=False)) is None


def _verdict(
    causality: bool | None, rationale: str = "Strong evidence of causality."
) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="genetics",
        target_gene="GENE-A",
        disease="disease one",
        overall_verdict="support",
        confidence=0.6,
        axes=[
            AxisVerdict(axis="causality", verdict=causality, confidence=0.6, rationale=rationale),
            AxisVerdict(axis="genetic_validity", verdict=True, confidence=0.6, rationale="v"),
        ],
    )


def test_an_unreplicated_causality_is_set_to_null_with_the_rules_rationale() -> None:
    out = apply_causality(_verdict(True), _reading([_obs("c1")]))

    axis = next(a for a in out.axes if a.axis == "causality")
    assert axis.verdict is None
    assert axis.rationale.startswith("Human genetic association with this disease is not")
    assert any(f.rule_id == "genetics.causality_by_rule" for f in out.validation_flags)


def test_an_agreeing_axis_is_kept_and_lifted_to_the_rules_confidence() -> None:
    v = _verdict(True)
    v = v.model_copy(update={"axes": [v.axes[0].model_copy(update={"confidence": 0.3}), v.axes[1]]})

    out = apply_causality(v, _reading([_obs("c1"), _obs("c2")]))

    axis = next(a for a in out.axes if a.axis == "causality")
    assert axis.confidence == 0.6 and axis.rationale == "Strong evidence of causality."
    assert not out.validation_flags


def test_the_prompt_states_the_fixed_value() -> None:
    assert "set by rule from the human genetics (fixed): null" in render_causality_block(
        _reading([_obs("c1")])
    )
    assert render_causality_block({}) == ""


def test_a_curated_somatic_driver_record_sets_causality_for_a_cancer() -> None:
    reading = causality_reading(
        [], gwas_studies=(), paper_of={}, curated=False, available=False, somatic=["intogen"]
    ).model_dump()

    axis = causality_axis(reading)

    assert axis.verdict is True and "intogen" in axis.rationale
