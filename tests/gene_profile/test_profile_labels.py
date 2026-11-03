# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The labels of the gene's approved drugs: fetched by drug name, parsed once, read by every
table.

Label records are built from the recorded shapes of real FDA labels
(``tests/fixtures/fda_labels``) through the connector's own parser, so a table cannot be tested
against a label shape no label has.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gather  # noqa: E402
import profile_drugs as drugs  # noqa: E402
from profile_schema import RawRecord  # noqa: E402

from mcp_servers.openfda.labels import parse_label  # noqa: E402

_LABELS = _ROOT / "tests" / "fixtures" / "fda_labels"


def parsed(name: str) -> dict[str, Any]:
    """A label record as gather stores it."""
    raw = json.loads((_LABELS / f"{name}.json").read_text())
    return gather.to_jsonable(parse_label(raw))


# ── which drugs get a label ─────────────────────────────────────────────────────────


def test_every_approved_drug_of_the_table_is_fetched_once() -> None:
    landscape = {
        "drugs": [
            {"name": "SOTORASIB", "brands": ["LUMAKRAS"], "approved": True},
            {"name": "SOTORASIB", "brands": ["OTHERBRAND"], "approved": True},
            {"name": "SALIRASIB", "brands": [], "stage": "Phase 2", "approved": False},
        ]
    }
    assert drugs.linked_drugs(landscape) == [
        {"name": "SOTORASIB", "brands": ["LUMAKRAS", "OTHERBRAND"]}
    ]


def test_a_label_is_cited_by_its_drug() -> None:
    assert drugs.label_key("SOTORASIB") == "label:sotorasib"
    assert drugs.label_key("DABRAFENIB MESYLATE") == "label:dabrafenib"
    assert (
        drugs.label_key("ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR")
        == "label:elexacaftor-tezacaftor-and-ivacaftor"
    )


def test_a_row_reads_its_own_label_before_a_brand_listed_combination() -> None:
    labels = [
        {"drug_name": "LUMACAFTOR AND IVACAFTOR", "brand_names": ["ORKAMBI"]},
        {"drug_name": "IVACAFTOR", "brand_names": ["KALYDECO"]},
    ]
    row = {"name": "IVACAFTOR", "brands": ["ORKAMBI", "KALYDECO"]}
    assert drugs.label_for(labels, row)["drug_name"] == "IVACAFTOR"
    assert drugs.label_for(labels, {"name": "LUMACAFTOR", "brands": ["ORKAMBI"]})[
        "brand_names"
    ] == ["ORKAMBI"]
    assert drugs.label_for(labels, {"name": "NAPORAFENIB"}) == {}


# ── gathering ───────────────────────────────────────────────────────────────────────


def _ctx() -> gather.Ctx:
    ctx = gather.Ctx(query="KRAS", symbol="KRAS")
    ctx.data["drug_landscape"] = {
        "drugs": [
            {"name": "SOTORASIB", "brands": ["LUMAKRAS"], "approved": True},
            {"name": "PANITUMUMAB", "brands": ["VECTIBIX"], "approved": True},
            {"name": "NOLABELIB", "brands": [], "approved": True},
        ]
    }
    return ctx


def test_labels_are_fetched_by_name_and_a_missing_one_is_recorded(monkeypatch: Any) -> None:
    labels = {"SOTORASIB": "sotorasib", "PANITUMUMAB": "panitumumab"}

    async def fetch(name: str, brands: list[str]) -> Any:
        if name not in labels:
            return None
        return parse_label(json.loads((_LABELS / f"{labels[name]}.json").read_text()))

    monkeypatch.setattr(gather.openfda, "fetch_label", fetch)
    data = asyncio.run(gather.fetch_fda_labels(_ctx()))
    assert [lab["drug_name"] for lab in data["labels"]] == ["SOTORASIB", "PANITUMUMAB"]
    assert [(d["name"], d["label"]) for d in data["drugs"]] == [
        ("SOTORASIB", True),
        ("PANITUMUMAB", True),
        ("NOLABELIB", False),
    ]
    assert data["lookup_failed"] == {}


def test_a_failed_lookup_is_not_checked_and_all_failing_is_an_error(monkeypatch: Any) -> None:
    async def flaky(name: str, brands: list[str]) -> Any:
        if name == "PANITUMUMAB":
            raise RuntimeError("HTTP 429")
        return None

    monkeypatch.setattr(gather.openfda, "fetch_label", flaky)
    data = asyncio.run(gather.fetch_fda_labels(_ctx()))
    assert list(data["lookup_failed"]) == ["PANITUMUMAB"]

    async def down(name: str, brands: list[str]) -> Any:
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(gather.openfda, "fetch_label", down)
    with pytest.raises(RuntimeError, match="every label lookup failed"):
        asyncio.run(gather.fetch_fda_labels(_ctx()))


def test_no_linked_drug_skips_the_source() -> None:
    assert gather._need_linked_drug(gather.Ctx(query="MECP2"))


def test_each_label_is_a_citable_regulator_source() -> None:
    rec = RawRecord(
        key="fda_labels",
        section="clinical",
        db_name="openFDA drug labels",
        tool="openfda.fetch_label",
        status="ok",
        retrieved_at="2026-11-03T00:00:00+00:00",
        data={"drugs": [], "labels": [parsed("sotorasib")]},
    )
    [source] = gather.derived_sources(rec, "2026-11-03")
    assert source.key == "label:sotorasib"
    assert source.kind == "label" and source.tier == 1
    assert source.year == 2025 and "revised 2025-01-22" in source.title
    assert source.url.startswith("https://dailymed.nlm.nih.gov/")


def test_the_digest_gives_each_label_its_warnings() -> None:
    text = gather._labels_digest({"labels": [parsed("sotorasib")], "drugs": []})
    assert "[@label:sotorasib]" in text
    assert "5.1 Hepatotoxicity" in text
