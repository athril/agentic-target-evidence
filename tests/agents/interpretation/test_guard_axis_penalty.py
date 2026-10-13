# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A guard that flags an omitted or invented fact lowers the axes resting on it.

The phenotype-omission guard used to annotate only: a safety lens that never named the
lung and kidney knockout phenotypes kept "support 93%". Families with ``penalised_axes``
now cut those axes' confidence (as a failed fact check does) and the lens is
re-aggregated, so the number follows the open flag.
"""

from __future__ import annotations

import uuid

from agents.interpretation._lens_base import GUARD_AXIS_PENALTY, apply_guards
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.guard_rules import GUARD_RULES, GuardFacts

_LONG_SAFE_TEXT = (
    "Inhibition of the target looks tolerable given human loss-of-function carriers. " * 12
)


def _verdict() -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="safety",
        target_gene="GENE-A",
        disease="DISEASE-A",
        overall_verdict="support",
        confidence=0.9,
        narrative=_LONG_SAFE_TEXT,
        axes=[
            AxisVerdict(axis="toxicity", verdict=True, confidence=0.9, evidence_sources=3),
            AxisVerdict(
                axis="tissue_specificity", verdict=True, confidence=0.8, evidence_sources=2
            ),
        ],
    )


def test_omission_flag_cuts_toxicity_and_the_lens_confidence():
    facts = GuardFacts(mouse_phenotypes=["abnormal type II pneumocyte morphology"])
    out = apply_guards(_verdict(), facts, ("phenotype_omission",), lens="safety")
    tox = next(a for a in out.axes if a.axis == "toxicity")
    tis = next(a for a in out.axes if a.axis == "tissue_specificity")
    assert tox.confidence == round(0.9 * GUARD_AXIS_PENALTY, 3)
    assert tis.confidence == 0.8  # not the family's axis
    assert out.confidence < 0.9


def test_no_penalty_when_the_text_names_the_system():
    v = _verdict().model_copy(
        update={
            "narrative": _LONG_SAFE_TEXT + " Knockout lung phenotypes are a candidate liability."
        }
    )
    facts = GuardFacts(mouse_phenotypes=["abnormal type II pneumocyte morphology"])
    out = apply_guards(v, facts, ("phenotype_omission",), lens="safety")
    assert next(a for a in out.axes if a.axis == "toxicity").confidence == 0.9


def test_penalty_is_not_applied_twice_by_a_second_pass():
    facts = GuardFacts(mouse_phenotypes=["abnormal type II pneumocyte morphology"])
    once = apply_guards(_verdict(), facts, ("phenotype_omission",), lens="safety")
    twice = apply_guards(once, facts, (), lens="safety")
    assert twice.axes[0].confidence == once.axes[0].confidence


def test_penalised_axes_name_real_lens_axes():
    known = {
        "toxicity",
        "tissue_specificity",
        "mechanism_of_action",
        "clinical_validation",
        "clinical_precedent",
        "approval_precedent",
    }
    for rule in GUARD_RULES.values():
        assert set(rule.penalised_axes) <= known
