# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for core.json_utils — recovering usable JSON from local-model slips."""

from __future__ import annotations

import json

import pytest

from core.json_utils import close_unbalanced, loads_recovering


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
