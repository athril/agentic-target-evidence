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
import profile_writer as writer  # noqa: E402

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


# ------------------------------------------------------------------ evidence relations

_HUMAN = {"design": "observational", "species": ["human"], "last_author": "A"}
_TRIAL = {"design": "randomized controlled trial", "species": ["human"], "last_author": "B"}
_STRENGTHS = {"1": _HUMAN, "2": _TRIAL}
_SOURCES = {"pmid:1": {}, "pmid:2": {}}


def _claim(**over: Any) -> dict[str, Any]:
    return {"claim": "Lower activity raises a risk", "supports": ["pmid:1"], **over}


def test_a_different_kind_of_evidence_against_a_claim_is_flagged() -> None:
    claim = _claim(contradicts=["pmid:2"])
    assert writer.cross_kind_contradictions(claim, _STRENGTHS) == ["pmid:2"]


def test_the_same_kind_of_evidence_against_a_claim_is_a_real_conflict() -> None:
    claim = _claim(supports=["pmid:2"], contradicts=["pmid:2"])
    assert writer.cross_kind_contradictions(claim, _STRENGTHS) == []


def test_a_not_comparable_source_is_shown_with_its_reason_and_not_as_against() -> None:
    claim = _claim(not_comparable=[{"key": "pmid:2", "why": "short exposure"}])
    table, keys = writer.how_we_know_md([claim], _STRENGTHS, {"pmid:1", "pmid:2"})
    assert "≠ not directly comparable: short exposure [@pmid:2]" in table
    assert "✗" not in table
    assert "pmid:2" in keys


def test_a_not_comparable_source_needs_its_reason_and_a_known_key() -> None:
    bare = _claim(not_comparable=[{"key": "pmid:2"}, {"key": "pmid:9", "why": "x"}])
    problems = writer.how_problems([bare], _SOURCES)
    assert any("needs a 'why'" in p for p in problems)
    assert any("pmid:9" in p for p in problems)


def test_a_not_comparable_source_does_not_count_as_support() -> None:
    claim = _claim(not_comparable=[{"key": "pmid:2", "why": "short exposure"}])
    cells = writer.claim_cells(claim, _STRENGTHS)
    assert cells["pharmacology"]["support"] == []
    assert cells["pharmacology"]["papers"] == 0


# ------------------------------------------------------------------ mechanism routes


def _step(route: str = "") -> dict[str, Any]:
    return {"actor": "A", "action": "acts on", "target": "B", "compartment": "Cytoplasm",
            "keys": ["pmid:1"], **({"route": route} if route else {})}  # fmt: skip


def test_steps_without_routes_are_one_chain() -> None:
    assert writer.step_edges([_step(), _step(), _step()]) == [(0, 1), (1, 2)]


def test_routes_branch_from_the_shared_step_and_rejoin_at_the_next() -> None:
    steps = [_step(), _step("r1"), _step("r1"), _step("r2"), _step()]
    assert writer.step_edges(steps) == [(0, 1), (1, 2), (0, 3), (2, 4), (3, 4)]


def test_a_mechanism_may_start_with_its_routes() -> None:
    assert writer.step_edges([_step("r1"), _step("r2"), _step()]) == [(0, 2), (1, 2)]


def test_parallel_routes_may_use_more_steps_than_one_chain() -> None:
    routed = [_step("r1")] * 5 + [_step("r2")] * 5
    assert writer.mechanism_problems(routed, {"pmid:1": {}}, set()) == []
    assert any(
        "steps" in p for p in writer.mechanism_problems([_step()] * 9, {"pmid:1": {}}, set())
    )


def test_a_lone_route_is_a_problem() -> None:
    steps = [_step("r1"), _step(), _step(), _step()]
    assert any(
        "only one route" in p for p in writer.mechanism_problems(steps, {"pmid:1": {}}, set())
    )


def test_the_figure_draws_branching_routes(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from profile_figures import mechanism

    steps = [{**_step(), "strength": "solid"}, {**_step("r1"), "strength": "dashed"},
             {**_step("r2"), "strength": "dashed"}, {**_step(), "strength": "solid"}]  # fmt: skip
    assert mechanism(tmp_path / "m.svg", steps) is not None
    assert (tmp_path / "m.svg").stat().st_size > 0
