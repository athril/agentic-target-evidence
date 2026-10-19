# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Druggability and drug modalities from Open Targets (biology_lens/tractability.py)."""

from __future__ import annotations

import uuid

import pytest

from agents.interpretation.biology_lens.tractability import (
    apply_druggability,
    druggability_axis,
    render_modality_block,
)
from schemas.verdicts import AxisVerdict, LensVerdict

_ANTIBODY = {"drug_name": "DRUG-A", "drug_type": "Antibody", "is_approved": True, "max_phase": 4}
_EARLY = {
    "drug_name": "DRUG-B",
    "drug_type": "Small molecule",
    "is_approved": False,
    "max_phase": 0,
}


def test_the_lens_is_told_each_drugs_modality() -> None:
    block = render_modality_block([_ANTIBODY, _ANTIBODY, _EARLY])

    assert "antibody: drug-a" in block and "small molecule: drug-b" in block
    assert "State a drug's modality only as listed here." in block
    assert render_modality_block([]) == ""


@pytest.mark.parametrize(
    ("drugs", "tract", "value"),
    [
        ([_ANTIBODY], {"available": True}, True),  # a drug in the clinic
        ([_EARLY], {"available": True, "small_molecule": True}, True),  # tractable
        ([], {"available": True}, False),  # retrieved, nothing tractable, no drug
        ([_EARLY], {"available": True}, None),  # a preclinical drug: not nothing
        ([], {}, None),  # nothing retrieved
    ],
)
def test_druggability_by_rule(drugs, tract, value) -> None:
    axis = druggability_axis(drugs, tract)

    assert (axis.verdict if axis else None) is value


def _verdict(druggability: bool | None) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="biology",
        target_gene="GENE-A",
        disease="disease one",
        overall_verdict="neutral",
        confidence=0.4,
        axes=[
            AxisVerdict(axis="druggability", verdict=druggability, confidence=0.4, rationale="m"),
            AxisVerdict(axis="mechanism_of_action", verdict=None, confidence=0.0, rationale="x"),
        ],
    )


def test_a_contradicting_druggability_is_replaced_and_flagged() -> None:
    out = apply_druggability(_verdict(False), druggability_axis([_ANTIBODY], {}))

    axis = next(a for a in out.axes if a.axis == "druggability")
    assert axis.verdict is True and axis.confidence == 0.85
    assert any(f.rule_id == "biology.druggability_by_rule" for f in out.validation_flags)


def test_an_agreeing_druggability_keeps_its_rationale_and_is_not_flagged() -> None:
    out = apply_druggability(_verdict(True), druggability_axis([_ANTIBODY], {}))

    axis = next(a for a in out.axes if a.axis == "druggability")
    assert axis.rationale.startswith("m\n")
    assert not out.validation_flags


def test_no_rule_leaves_the_verdict() -> None:
    v = _verdict(True)
    assert apply_druggability(v, None) is v
