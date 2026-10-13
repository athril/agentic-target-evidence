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


_CLOSERS = {"[": "]", "{": "}"}
# A backslash that does not start a valid JSON escape (\" \\ \/ \b \f \n \r \t \uXXXX):
# models write LaTeX ("\(10^{-39}\)") or Windows paths inside string values.
_INVALID_ESCAPE = re.compile(r'(?<!\\)((?:\\\\)*)\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def remove_trailing_commas(text: str) -> str:
    """Drop commas that directly precede a closing ``}`` or ``]`` outside strings.

    Valid JSON never has one there, so removing it cannot change a valid document;
    small models often leave one after the last item of an object or array.
    """
    out: list[str] = []
    in_string = escaped = False
    pending: list[str] = []  # a comma plus the whitespace after it, held until we know
    for ch in text:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if pending:
            if ch.isspace():
                pending.append(ch)
                continue
            if ch in "}]":
                out.extend(pending[1:])  # keep the whitespace, drop the comma
            else:
                out.extend(pending)
            pending = []
        if ch == ",":
            pending = [ch]
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    out.extend(pending)
    return "".join(out)


def escape_invalid_backslashes(text: str) -> str:
    """Double every backslash that does not begin a valid JSON escape, so the text
    parses with the backslash kept literally; valid escapes are left untouched."""
    return _INVALID_ESCAPE.sub(lambda m: m.group(1) + "\\\\", text)


def close_unbalanced(text: str) -> str | None:
    """``text`` with its still-open arrays/objects closed, or None if that is unsafe.

    Local models sometimes stop without the final bracket(s) — ``[{"a": 1}`` — after
    every string and value is complete. Closing them is safe exactly then: brackets
    are tracked outside strings, and a text that ends inside a string (truncated
    mid-value), has a mismatched closer or has nothing open is left alone.
    """
    stack: list[str] = []
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in _CLOSERS:
            stack.append(_CLOSERS[ch])
        elif ch in "]}" and (not stack or stack.pop() != ch):
            return None
    if in_string or not stack:
        return None
    body = text.rstrip()
    if body.endswith(","):
        body = body[:-1]
    return body + "".join(reversed(stack))


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

    It also recovers the opposite slip — a root left open at the end of the output
    (``[{...}`` with the final ``]`` missing) — via ``close_unbalanced``, a trailing comma
    before a closing bracket (``remove_trailing_commas``) and a backslash that is not a
    valid JSON escape (``escape_invalid_backslashes``, e.g. LaTeX in a narrative).

    Uses ``strict=False`` so literal newlines inside multi-paragraph string fields
    (e.g. the biology lens ``narrative``) are also tolerated. Any decode error other
    than a recoverable premature close or unclosed end is re-raised unchanged.
    """
    text = raw
    closed = escaped = decommaed = False
    for _ in range(max_repairs + 3):
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError as e:
            if "Invalid \\escape" in e.msg and not escaped:
                text, escaped = escape_invalid_backslashes(text), True
                continue
            if not decommaed:
                decommaed = True
                stripped = remove_trailing_commas(text)
                if stripped != text:
                    text = stripped
                    continue
            if "Extra data" not in e.msg:
                # Failed at the very end of the input: brackets left open.
                repaired = None if closed else close_unbalanced(text)
                if repaired is None or e.pos < len(text.rstrip()):
                    raise
                text, closed = repaired, True
                continue
            # The premature closing brace is the last non-space char before the
            # leftover data; splice only that brace out and retry.
            i = e.pos - 1
            while i >= 0 and text[i].isspace():
                i -= 1
            if i < 0 or text[i] != "}":
                raise
            text = text[:i] + text[i + 1 :]
    return json.loads(text, strict=False)


def loads_concatenated(raw: str) -> list[Any]:
    """Every JSON value in ``raw`` when a model emits several in a row — one object per
    line (JSON Lines) or back to back — instead of the single value asked for.

    Each value is decoded with the same repairs as ``loads_recovering``; text between
    values (whitespace, commas, stray prose) is skipped. Returns [] when nothing decodes.
    """
    decoder = json.JSONDecoder(strict=False)
    text = escape_invalid_backslashes(remove_trailing_commas(raw))
    values: list[Any] = []
    i = 0
    while i < len(text):
        start = min((j for j in (text.find("{", i), text.find("[", i)) if j >= 0), default=-1)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            i = start + 1
            continue
        values.append(value)
        i = end
    return values
