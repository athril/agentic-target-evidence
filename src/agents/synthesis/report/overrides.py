# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens verdicts changed by a deterministic rule, for the report.

A post-LLM rule (precedent, direction of effect, common essentiality, the commercial
validated rule) can change a lens verdict while the model's narrative still argues the
old one — "genetics strongly support activation" under an Oppose heading. The report
states each change above the narrative, from the rule's own flag, instead of asking the
model to rewrite its prose.
"""

from __future__ import annotations

import re
from typing import Any

_VERDICT_CHANGE = re.compile(r"verdict \w+ → \w+")


def rule_overrides(validation_flags: list[dict[str, Any]] | None) -> list[str]:
    """Messages of the flags that record a rule changing the lens's overall verdict."""
    return [
        str(f.get("message") or "")
        for f in validation_flags or []
        if _VERDICT_CHANGE.search(str(f.get("message") or ""))
        or f.get("rule_id") == "neutral_axis_conflict"
    ]


def overrides_block(validation_flags: list[dict[str, Any]] | None) -> str:
    """Markdown lines placed above the narrative, or "" when no rule changed the verdict."""
    lines = rule_overrides(validation_flags)
    if not lines:
        return ""
    return (
        "\n".join(
            f"> ⚙️ **Verdict set by rule** — {m} The narrative below is the model's own "
            "reasoning, written before the rule applied."
            for m in lines
        )
        + "\n\n"
    )
