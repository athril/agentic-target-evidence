# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The genetics lens's pre-computed mechanism block is framed by whose mechanism it is."""

from __future__ import annotations

from agents.interpretation.genetics_lens.agent import mechanism_note

_LOF = {
    "mechanism": "lof",
    "direction": "activate",
    "confidence": 0.8,
    "rationale": "truncating variants dominate",
}


def test_the_indications_own_mechanism_is_its_direction():
    note = mechanism_note(_LOF, in_scope=True, disease="disease one", related=[], is_oncology=False)
    assert "Therapeutic implication: ACTIVATE" in note
    assert "Use this direction" in note


def test_a_related_disorders_mechanism_is_not_the_indications_direction():
    note = mechanism_note(
        _LOF, in_scope=False, disease="disease one", related=["disorder two"], is_oncology=False
    )
    assert "NOT this indication" in note and "disorder two" in note
    assert "Use this direction" not in note
    assert "Therapeutic implication" not in note
    assert "cancer" not in note


def test_a_cancer_indication_is_told_the_germline_mechanism_is_not_the_tumours():
    note = mechanism_note(_LOF, in_scope=False, disease="tumour one", related=[], is_oncology=True)
    assert "germline disorder's mechanism is not the tumour's" in note
