# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Utilities for parsing LLM JSON responses."""

from __future__ import annotations

import json
import re
from typing import Any


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


def loads_recovering(raw: str, *, max_repairs: int = 3) -> Any:
    """``json.loads`` that recovers from a prematurely-closed root object.

    Local models occasionally close the root object early and then continue
    emitting more keys as if they were siblings, e.g.::

        {"overall_verdict": "support", "narrative": "..."},
          "axes": [ ... ]
        }

    The *content* is valid; only a stray ``}`` is misplaced. Strict ``json.loads``
    parses the first object and then raises ``JSONDecodeError: Extra data`` on the
    leftover ``, "axes": ...``, which would otherwise discard an entirely usable
    verdict. This helper detects that case, splices out the premature closing brace
    immediately before the extra data, and re-parses — repeating up to
    ``max_repairs`` times for objects closed early more than once.

    Uses ``strict=False`` so literal newlines inside multi-paragraph string fields
    (e.g. the biology lens ``narrative``) are also tolerated. Any decode error other
    than a recoverable premature close is re-raised unchanged.
    """
    text = raw
    for _ in range(max_repairs + 1):
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError as e:
            if "Extra data" not in e.msg:
                raise
            # The premature closing brace is the last non-space char before the
            # leftover data; splice only that brace out and retry.
            i = e.pos - 1
            while i >= 0 and text[i].isspace():
                i -= 1
            if i < 0 or text[i] != "}":
                raise
            text = text[:i] + text[i + 1 :]
    return json.loads(text, strict=False)
