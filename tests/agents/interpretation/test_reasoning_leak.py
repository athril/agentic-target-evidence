# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Leaked model deliberation is cut from the parsed verdict's prose fields."""

from __future__ import annotations

import json
import uuid

from agents.interpretation._lens_base import (
    REASONING_LEAK_RULE,
    _parse_verdict,
    strip_leaked_reasoning,
)

# The start of CFTR regulatory's label_safety rationale (2026-10-26 panel review).
_LEAKED = (
    "While some approved CFTR modulators (e.g., Trikafta, Alyftrek) carry black-box warnings "
    "for drug-induced liver injury, these are class-level safety signals observed in the label "
    "data. However, per the instructions, since no single 'clean' in-class drug exists, we must "
    "interpret this carefully. Wait, let's re-read: 'verdict=false means label liabilities are "
    "present'. I will go with 'oppose'."
)


def test_text_from_the_first_reasoning_sentence_on_is_cut():
    out, leaked = strip_leaked_reasoning(_LEAKED)
    assert leaked
    assert out.startswith("While some approved CFTR modulators")
    assert out.endswith("observed in the label data.")


def test_ordinary_prose_is_untouched():
    text = "The label's instructions for use require liver monitoring. No boxed warning applies."
    assert strip_leaked_reasoning(text) == (text, False)


def test_parsed_verdict_records_the_cut_fields():
    raw = json.dumps(
        {
            "overall_verdict": "support",
            "confidence": 0.9,
            "rationale": "Approved precedent exists.",
            "narrative": "",
            "axes": [{"axis": "label_safety", "verdict": None, "rationale": _LEAKED}],
        }
    )
    v = _parse_verdict(raw, "regulatory", "CFTR", "cystic fibrosis", "activate", uuid.uuid4(), "t")
    assert "per the instructions" not in v.axes[0].rationale
    assert v.rationale == "Approved precedent exists."
    flag = next(f for f in v.validation_flags if f.rule_id == REASONING_LEAK_RULE)
    assert "label_safety" in flag.message
