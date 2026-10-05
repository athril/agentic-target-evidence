# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Target-IP vs indication-only patent counting for the commercial lens."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from capabilities.target_validation.workflow import _patent_counts
from mcp_servers.uspto.tools import title_mentions
from services.evidence.constraint_interpret import interpret_patent_landscape


@pytest.mark.parametrize(
    ("title", "term", "expected"),
    [
        ("INHIBITORS OF TRPC6", "TRPC6", True),
        ("STABILIZED FORMULATIONS CONTAINING ANTI-PCSK9 ANTIBODIES", "PCSK9", True),
        (
            "Method of treating focal segmental glomerulosclerosis",
            "focal segmental glomerulosclerosis",
            True,
        ),
        ("PCSK90 variants", "PCSK9", False),
        ("METHOD OF TREATING FOCAL SEGMENTAL GLOMERULOSCLEROSIS", "TRPC6", False),
        ("anything", "", False),
    ],
)
def test_title_mentions(title: str, term: str, expected: bool) -> None:
    assert title_mentions(title, term) is expected


def test_patent_counts_split_target_and_indication_only() -> None:
    """TRPC6 × FSGS live shape: the OR query returned 12 TRPC6 titles + 5 FSGS-only titles."""
    rows = [SimpleNamespace(extra={"title": "INHIBITORS OF TRPC6"})] * 12 + [
        SimpleNamespace(extra={"title": "METHOD OF TREATING FOCAL SEGMENTAL GLOMERULOSCLEROSIS"})
    ] * 5
    assert _patent_counts(rows, "TRPC6") == (12, 5)  # type: ignore[arg-type]
    assert _patent_counts([], "TRPC6") == (0, 0)


def test_patent_landscape_framing_separates_indication_ip() -> None:
    text = interpret_patent_landscape(0, 5)
    assert "No patents naming the target" in text
    assert "5 further patent(s) name only the indication" in text
    assert "FTO" in text
    text = interpret_patent_landscape(12, 5)
    assert text.startswith("12 patent(s) naming the target")
    assert "do NOT describe the IP landscape as 'free of patents'" in text
    assert "only the indication" not in interpret_patent_landscape(3)
