# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for core.json_utils — recovering usable JSON from local-model slips."""

from __future__ import annotations

import json

import pytest

from core.json_utils import close_unbalanced, loads_recovering, parse_array_prefix


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Root array never closed (the model stopped after the object).
        ('[{"a": 1, "ids": ["x", "y"]}', [{"a": 1, "ids": ["x", "y"]}]),
        # Several levels left open, trailing comma.
        ('{"a": {"b": [1, 2,', {"a": {"b": [1, 2]}}),
        # Brackets inside strings are not structure.
        ('[{"t": "a [b] {c"}', [{"t": "a [b] {c"}]),
        # Escaped quotes inside strings.
        ('[{"t": "say \\"hi\\""}', [{"t": 'say "hi"'}]),
    ],
)
def test_loads_recovering_closes_unclosed_brackets(raw: str, expected: object) -> None:
    assert loads_recovering(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        '[{"a": "truncated mid-str',  # ends inside a string: genuinely truncated
        '[{"a": 1]',  # mismatched closer
        '{"a": 1} garbage',  # not an unclosed-bracket problem
    ],
)
def test_loads_recovering_does_not_guess_beyond_unclosed_brackets(raw: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        loads_recovering(raw)


def test_close_unbalanced_leaves_balanced_text_alone() -> None:
    assert close_unbalanced('{"a": [1]}') is None


def test_loads_recovering_still_handles_premature_close() -> None:
    raw = '{"verdict": "support", "narrative": "x"},\n  "axes": []\n}'
    assert loads_recovering(raw) == {"verdict": "support", "narrative": "x", "axes": []}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # LaTeX inside a string value: "\(" is not a JSON escape
        (
            '{"narrative": "p-values from \\(10^{-39}\\) upward"}',
            {"narrative": "p-values from \\(10^{-39}\\) upward"},
        ),
        # a Windows-style path
        ('{"path": "C:\\data\\gwas"}', {"path": "C:\\data\\gwas"}),
        # valid escapes are untouched alongside an invalid one
        ('{"t": "a\\nb \\"q\\" \\u00e9 \\x"}', {"t": 'a\nb "q" é \\x'}),
    ],
)
def test_loads_recovering_keeps_invalid_backslashes_literally(raw: str, expected: object) -> None:
    assert loads_recovering(raw) == expected


def test_loads_recovering_drops_trailing_commas_outside_strings() -> None:
    raw = '{"axes": [{"axis": "a", "ids": ["x", "y"],\n    },\n  ], "note": "a, }"}'
    assert loads_recovering(raw) == {"axes": [{"axis": "a", "ids": ["x", "y"]}], "note": "a, }"}


def test_loads_concatenated_reads_json_lines() -> None:
    from core.json_utils import loads_concatenated

    raw = '{"S1": "first."}\n{"S2": "second, with \\\\(x\\\\)."}\nnoise {"S3": "third"}'
    assert loads_concatenated(raw) == [
        {"S1": "first."},
        {"S2": "second, with \\(x\\)."},
        {"S3": "third"},
    ]


_FIVE = [
    {"id": f"PMID:{i}", "verdict": "keep", "rationale": f"item {i}, with [brackets] and {{braces}}"}
    for i in range(5)
]


@pytest.mark.parametrize("fenced", [False, True])
def test_parse_array_prefix_returns_exactly_the_complete_objects_at_every_cut(
    fenced: bool,
) -> None:
    """Cut a reply at every character: the result is the objects whose closing brace
    made it in, never a partial one and never one fewer."""
    body = json.dumps(_FIVE, indent=1)
    full = f"```json\n{body}\n```" if fenced else body
    offset = len("```json\n") if fenced else 0
    # Index just past each object's closing brace, in the unfenced body.
    ends: list[int] = []
    depth, in_string, escaped = 0, False, False
    for i, ch in enumerate(body):
        if in_string:
            escaped = not escaped and ch == "\\"
            if ch == '"' and not escaped:
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                ends.append(i + 1)
    assert len(ends) == len(_FIVE)
    for cut in range(len(full) + 1):
        complete = sum(1 for e in ends if e + offset <= cut)
        assert parse_array_prefix(full[:cut]) == _FIVE[:complete], cut


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[]", []),
        ("", []),
        ("no json here", []),
        ("[garbage, {", []),
        ('Here you go: [{"a": 1}, 7, {"b": 2}', [{"a": 1}, {"b": 2}]),
    ],
)
def test_parse_array_prefix_edge_cases(raw: str, expected: list) -> None:
    assert parse_array_prefix(raw) == expected
