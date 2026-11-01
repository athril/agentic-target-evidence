# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What a gene profile may state: drug stages, comparisons between kinds of evidence, routes,
checks against the literature and the audit of each sentence against its source.

Offline: every record is a fixture. Gene and drug names are placeholders."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gather  # noqa: E402
import profile_drugs as drugs  # noqa: E402

# ------------------------------------------------------------------ the drug list


def _known(name: str, **over: Any) -> dict[str, Any]:
    return {"drug_name": name, "drug_type": "Antibody", "max_phase": 3.0, "is_approved": False,
            **over}  # fmt: skip


def _label(name: str, moa: str = "binds GENE1", brands: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"drug_name": name, "mechanism_of_action": moa, "brand_names": list(brands)}


def _rows(known: Any, labels: Any = ()) -> Any:
    return drugs.rows(drugs.candidates(known, labels, ["GENE1"]))


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("DRUGA SODIUM", "DRUGA"),
        ("DRUGA DECANOATE", "DRUGA"),
        ("druga mesylate", "DRUGA"),
        ("DRUGA-LIGA", "DRUGA"),
        ("INSULIN VARIANT", "INSULIN VARIANT"),
        ("ADO-DRUGA", "ADO-DRUGA"),
    ],
)
def test_sources_share_a_drug_name(name: str, key: str) -> None:
    assert drugs.drug_key(name) == key


def test_an_approved_drug_is_approved_whatever_its_phase() -> None:
    rows = _rows([_known("DRUGA", is_approved=True, max_phase=4.0)])
    assert rows[0]["stage"] == drugs.STAGE_APPROVED
    assert rows[0]["approved"] is True


def test_an_unapproved_drug_keeps_its_highest_phase() -> None:
    rows = _rows([_known("DRUGA", max_phase=2.0), _known("DRUGA SODIUM", max_phase=3.0)])
    assert len(rows) == 1
    assert rows[0]["stage"] == "Phase 3"


def test_a_drug_without_a_phase_says_so() -> None:
    assert _rows([_known("DRUGA", max_phase=None)])[0]["stage"] == drugs.STAGE_NONE


def test_a_label_adds_brand_names_to_the_drug_it_names() -> None:
    rows = _rows([_known("DRUGA")], [_label("DRUGA-LIGA", brands=("BRANDA",))])
    assert len(rows) == 1
    assert rows[0]["brands"] == ["BRANDA"]


def test_a_label_alone_adds_no_drug() -> None:
    assert _rows([], [_label("DRUGB")]) == []


def test_an_off_target_label_adds_no_brand() -> None:
    rows = _rows([_known("DRUGA")], [_label("DRUGA", moa="binds GENE2", brands=("BRANDA",))])
    assert rows[0]["brands"] == []


def test_a_symbol_inside_a_longer_word_is_not_on_target() -> None:
    assert not drugs.on_target(_label("X", moa="binds GENE10"), ["GENE1"])


def test_most_advanced_drugs_come_first() -> None:
    rows = _rows(
        [_known("EARLY", max_phase=1.0), _known("LATE"), _known("ONMARKET", is_approved=True)]
    )
    assert [r["name"] for r in rows] == ["ONMARKET", "LATE", "EARLY"]


def test_the_table_lists_every_drug_with_the_stage_rule() -> None:
    rows = _rows([_known("DRUGA", is_approved=True)])
    table = drugs.landscape_md({"drugs": rows}, "[@drug_landscape]")
    assert "| Druga | Antibody | Approved | [@drug_landscape] |" in table
    assert "Open Targets" in table


def test_no_drug_renders_nothing_for_the_empty_state() -> None:
    assert drugs.landscape_md({"drugs": []}, "") == ""


def test_a_drug_is_named_by_name_or_brand() -> None:
    row = {"name": "DRUGA", "brands": ["BRANDA"]}
    assert drugs.named(row, "brandA lowered it")
    assert not drugs.named(row, "drugab did not")


def _ctx(**data: Any) -> Any:
    ctx = gather.Ctx.__new__(gather.Ctx)
    ctx.data = data
    return ctx


def test_downstream_lookups_use_the_drug_list_with_product_names() -> None:
    listed = {"drugs": [{"name": "DRUGA", "brands": ["BRANDA"], "approved": True}]}
    ctx = _ctx(drug_landscape=listed, ot_known_drugs={"drugs": []})
    assert gather.approved_drugs(ctx) == [("DRUGA", ["BRANDA"])]


def test_downstream_lookups_fall_back_to_open_targets() -> None:
    ctx = _ctx(ot_known_drugs={"drugs": [_known("DRUGA SODIUM", is_approved=True)]})
    assert gather.approved_drugs(ctx) == [("DRUGA", [])]
