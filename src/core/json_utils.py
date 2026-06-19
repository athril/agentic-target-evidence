# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Utilities for parsing LLM JSON responses."""

from __future__ import annotations

import re


def strip_json_fence(raw: str) -> str:
    """Strip markdown code fences from an LLM JSON response.

    Models often echo the ``` ```json ``` ``` fence from the skill prompt.
    Strips the fence so json.loads can parse the content cleanly.
    """
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    raw = re.sub(r"^```[^\n]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()
