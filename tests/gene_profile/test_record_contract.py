# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and renderers agree with the records' real shape.

The label-warnings table read a key no connector writes, and its test passed because the fixture
used the same key. ``tests/fixtures/record_shapes.json`` holds the key paths recorded runs were
seen to hold (``record_replay.py`` writes it); fixtures built with ``record_fixture`` may only use
those keys, and code that reads a key outside them fails ``assert_reads_known``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import profile_tables as tables  # noqa: E402

from tests.gene_profile.record_replay import (  # noqa: E402
    Tracker,
    _id_like,
    _segments,
    assert_reads_known,
    load_registry,
    record_fixture,
    satisfied,
)


def test_the_registry_holds_structure_not_data() -> None:
    """Identifiers, drug names and loci are folded into ``*``, so the registry carries no values."""
    registry = load_registry()
    assert registry, "replay recorded runs to write tests/fixtures/record_shapes.json"
    values = {
        (source, seg)
        for source, paths in registry.items()
        for path in paths
        for seg in _segments(path)
        if seg not in ("[]", "*") and _id_like(seg)
    }
    assert not values


def test_a_wildcard_stands_for_one_key() -> None:
    patterns = {"papers.*.year", "drugs[].name"}
    assert satisfied("papers.12345.year", patterns)
    assert satisfied("drugs[].name", patterns)
    assert not satisfied("papers.12345.title", patterns)
    assert not satisfied("drugs[].stage", patterns)


def test_a_fixture_cannot_invent_a_key() -> None:
    """The fixture that hid the empty warnings column would now be rejected."""
    with pytest.raises(ValueError, match="warnings_and_cautions"):
        record_fixture(
            "openfda_labels", [{"drug_name": "DRUGA", "warnings_and_cautions": "Monitor."}]
        )


def test_a_read_of_a_key_no_record_holds_fails() -> None:
    tracker = Tracker()
    labels = record_fixture("openfda_labels", [{"drug_name": "DRUGA"}], tracker)
    labels[0].get("warnings_and_cautions")
    with pytest.raises(AssertionError, match=r"\[\]\.warnings_and_cautions"):
        assert_reads_known(tracker)


def test_the_label_warnings_table_reads_only_keys_labels_hold() -> None:
    tracker = Tracker()
    labels = record_fixture(
        "openfda_labels",
        [
            {
                "drug_name": "DRUGA MESYLATE",
                "brand_names": ["BRANDA"],
                "boxed_warning": "WARNING: HEPATOTOXICITY",
                "warnings": "New primary malignancies can occur.",
                "contraindications": "None.",
            }
        ],
        tracker,
    )
    landscape = record_fixture(
        "drug_landscape", {"drugs": [{"name": "DRUGA", "brands": []}]}, tracker
    )
    text = tables.label_warnings_md(labels, landscape, {"openfda_labels"})
    assert "New primary malignancies" in text
    assert_reads_known(tracker)
