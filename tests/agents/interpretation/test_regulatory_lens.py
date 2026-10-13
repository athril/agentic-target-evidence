# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""In-class rules: an off-target label is neither precedent nor a target liability.

TRPC6 × FSGS: the only label shown was sparsentan (endothelin/angiotensin receptor
antagonist). Its FSGS approval was scored "Approval precedent: Yes, 85%" and its
hepatotoxicity boxed warning "Label safety: No" — both against TRPC6.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from agents.interpretation.regulatory_lens.agent import _apply_in_class_rules
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from schemas.verdicts import AxisVerdict, LensVerdict

_RUN = uuid.uuid4()
_OFF = (
    "SPARSENTAN's labelled mechanism of action does not name TRPC6; its label safety "
    "findings are drug effects, not TRPC6 effects"
)
_ON = "DRUGX's labelled mechanism of action names TRPC6"


def _claim(text: str) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="TRPC6",
        disease="FSGS",
        evidence_type=EvidenceType.REGULATORY,
        claim_text=text,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
    )


def _verdict(axes: dict[str, bool | None]) -> LensVerdict:
    return LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens="regulatory",
        target_gene="TRPC6",
        disease="FSGS",
        overall_verdict="neutral",
        axes=[AxisVerdict(axis=name, verdict=v, rationale="r") for name, v in axes.items()],
    )


_AXES = {"approval_precedent": True, "label_safety": False, "regulatory_de_risking": False}


def test_off_target_labels_are_not_precedent_or_liability():
    out = _apply_in_class_rules(_verdict(_AXES), [_claim(_OFF)])
    by_axis = {a.axis: a.verdict for a in out.axes}
    assert by_axis == {
        "approval_precedent": False,
        "label_safety": None,
        "regulatory_de_risking": None,
    }
    assert [f.rule_id for f in out.validation_flags] == ["off_target_label"]


def test_favourable_de_risking_is_kept():
    axes = {**_AXES, "regulatory_de_risking": True}
    out = _apply_in_class_rules(_verdict(axes), [_claim(_OFF)])
    assert {a.axis: a.verdict for a in out.axes}["regulatory_de_risking"] is True


@pytest.mark.parametrize(
    "texts", [(_OFF, _ON), (), ("unrelated claim",)], ids=["in-class", "no-claims", "no-labels"]
)
def test_unchanged_without_an_off_target_only_label_set(texts):
    verdict = _verdict(_AXES)
    assert _apply_in_class_rules(verdict, [_claim(t) for t in texts]) is verdict


def test_already_compliant_axes_add_no_flag():
    verdict = _verdict(
        {"approval_precedent": False, "label_safety": None, "regulatory_de_risking": None}
    )
    assert _apply_in_class_rules(verdict, [_claim(_OFF)]) is verdict
