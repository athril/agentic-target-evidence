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

import check_profile as checker  # noqa: E402
import gather  # noqa: E402
import profile_claims as claims  # noqa: E402
import profile_drugs as drugs  # noqa: E402
import profile_topics as topics  # noqa: E402
import profile_writer as writer  # noqa: E402

# ------------------------------------------------------------------ the drug list


def _known(name: str, **over: Any) -> dict[str, Any]:
    return {"drug_name": name, "drug_type": "Antibody", "max_phase": 3.0, "is_approved": False,
            "stop_reports": [], **over}  # fmt: skip


def _label(name: str, moa: str = "binds GENE1", brands: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"drug_name": name, "mechanism_of_action": moa, "brand_names": list(brands)}


def _approval(number: str, day: str = "2020-01-02", **over: Any) -> dict[str, Any]:
    return {"application_number": number, "approval_date": day, "sponsor": "ACME",
            "brand_names": [], "ingredients": [], "route": "ORAL", **over}  # fmt: skip


def _rows(known: Any, labels: Any, approvals: dict[str, Any], failed: Any = ()) -> Any:
    entries = drugs.candidates(known, labels, ["GENE1"])
    return drugs.rows(entries, {k: approvals.get(k) for k in entries}, failed)


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


def test_the_regulator_outranks_the_aggregator() -> None:
    rows = _rows([_known("DRUGA")], [], {"DRUGA": _approval("BLA1", "2025-12-12")})
    assert rows[0]["stage"] == "US approved 2025-12-12"
    assert rows[0]["us_approved"] is True


def test_a_labelled_drug_the_aggregator_lacks_gets_a_row_with_its_stage() -> None:
    rows = _rows(
        [],
        [_label("BRANDX")],
        {"BRANDX": _approval("NDA9", ingredients=["DRUGB DECANOATE"], brand_names=["BRANDX"])},
    )
    assert [r["name"] for r in rows] == ["DRUGB"]
    assert rows[0]["brands"] == ["BRANDX"]
    assert rows[0]["stage"].startswith("US approved")


def test_names_on_one_application_are_one_drug() -> None:
    rows = _rows(
        [_known("DRUGA", max_phase=3.0)],
        [_label("DRUGA-LIGA", brands=("BRANDA",))],
        {"DRUGA": _approval("BLA1")},
    )
    assert len(rows) == 1
    assert rows[0]["brands"] == ["BRANDA"]


def test_an_off_target_label_is_left_out() -> None:
    assert _rows([], [_label("OTHER", moa="binds GENE2")], {}) == []


def test_a_symbol_inside_a_longer_word_is_not_on_target() -> None:
    assert not drugs.on_target(_label("X", moa="binds GENE10"), ["GENE1"])


def test_approved_without_a_us_application_is_not_called_us_approved() -> None:
    rows = _rows([_known("DRUGA", is_approved=True)], [], {})
    assert rows[0]["stage"] == drugs.STAGE_OTHER
    assert rows[0]["approved"] is True
    assert rows[0]["us_approved"] is False


def test_a_failed_lookup_is_not_read_as_no_us_application() -> None:
    rows = _rows([_known("DRUGA", is_approved=True)], [], {}, failed={"DRUGA"})
    assert rows[0]["stage"] == drugs.STAGE_APPROVED_UNCHECKED


def test_unapproved_drugs_keep_their_phase_and_stop_reason() -> None:
    stop = {"status": "TERMINATED", "why": "Sponsor decision"}
    done = {"status": "COMPLETED", "why": ""}
    rows = _rows([_known("DRUGA", stop_reports=[stop, done])], [], {})
    assert rows[0]["stage"] == "Phase 3"
    assert rows[0]["stops"] == ["Sponsor decision"]


def test_a_discontinued_approval_says_so() -> None:
    rows = _rows([_known("DRUGA")], [], {"DRUGA": _approval("NDA1", discontinued=True)})
    assert rows[0]["stage"] == "US approved 2020-01-02; discontinued"


def test_most_advanced_drugs_come_first() -> None:
    rows = _rows(
        [_known("EARLY", max_phase=1.0), _known("ELSEWHERE", is_approved=True), _known("USDRUG")],
        [],
        {"USDRUG": _approval("NDA1")},
    )
    assert [r["name"] for r in rows] == ["USDRUG", "ELSEWHERE", "EARLY"]


def test_the_table_lists_every_drug_with_the_stage_rule() -> None:
    rows = _rows([_known("DRUGA")], [], {"DRUGA": _approval("NDA1")})
    table = drugs.landscape_md({"drugs": rows}, "[@drug_landscape]")
    assert "| Druga |" in table
    assert "US approved 2020-01-02" in table
    assert "Drugs@FDA" in table


def test_a_stopped_trial_is_not_shown_against_an_approved_drug() -> None:
    stop = {"status": "TERMINATED", "why": "Slow enrolment"}
    rows = _rows([_known("DRUGA", stop_reports=[stop])], [], {"DRUGA": _approval("NDA1")})
    assert "Slow enrolment" not in drugs.landscape_md({"drugs": rows}, "")


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


# ------------------------------------------------------------------ topics of the abstracts


def _paper(abstract: str, cites: int = 1) -> dict[str, Any]:
    return {"abstract": abstract, "citation_count": cites}


def test_a_sentence_is_kept_only_when_it_names_the_gene() -> None:
    papers = {
        "1": _paper("The promoter of GENE1 holds a site. Mutating the promoter did nothing."),
        "2": _paper("The promoter of OTHER was mapped."),
    }
    hits = topics.topic_sentences(papers, ["GENE1"])["How its transcription is controlled"]
    assert hits == [("1", "The promoter of GENE1 holds a site.")]


def test_a_symbol_inside_a_longer_word_does_not_match() -> None:
    papers = {"1": _paper("The promoter of GENE10 was mapped.")}
    assert not topics.topic_sentences(papers, ["GENE1"])["How its transcription is controlled"]


def test_aliases_count_as_the_gene() -> None:
    papers = {"1": _paper("Cleavage of OLDNAME gives a fragment.")}
    hits = topics.topic_sentences(papers, ["GENE1", "OLDNAME"])
    assert hits["How the protein is processed or modified"]


def test_the_most_cited_papers_come_first_and_each_topic_is_capped() -> None:
    papers = {str(i): _paper(f"GENE1 is cleaved by enzyme {i}.", cites=i) for i in range(20)}
    papers["x"] = _paper("GENE1 is cleaved by an enzyme.", cites=None)  # type: ignore[arg-type]
    hits = topics.topic_sentences(papers, ["GENE1"])["How the protein is processed or modified"]
    assert [p for p, _s in hits[:2]] == ["19", "18"]
    assert len(hits) == topics._PER_TOPIC


def test_disagreement_has_its_own_topic() -> None:
    papers = {"1": _paper("Contrary to earlier work, GENE1 did not bind the receptor.")}
    assert topics.topic_sentences(papers, ["GENE1"])["Where findings disagree or stay open"]


def test_routine_contrast_words_are_not_disagreement() -> None:
    papers = {"1": _paper("GENE1 is common; however, its effect on a trait is small.")}
    assert not topics.topic_sentences(papers, ["GENE1"])["Where findings disagree or stay open"]


def test_post_transcriptional_control_is_not_transcription() -> None:
    papers = {"1": _paper("GENE1 lowers a receptor by a post-transcriptional mechanism.")}
    assert not topics.topic_sentences(papers, ["GENE1"])["How its transcription is controlled"]


def test_the_digest_says_sentences_are_leads_and_is_empty_without_matches() -> None:
    assert topics.topics_digest({"1": _paper("Nothing relevant here.")}, ["GENE1"]) == []
    lines = topics.topics_digest({"1": _paper("GENE1 is cleaved.")}, ["GENE1"])
    assert "- pmid:1 — GENE1 is cleaved." in lines
    assert any("before using it" in line for line in lines)


# ------------------------------------------------------------------ claim searches

_SYMBOLS = ["GENE1"]
_TERMS = ["enzyme", "active OR activity"]
_QUERY = claims.search_query(_SYMBOLS, _TERMS)
_SEARCHES = {_QUERY: {"query": _QUERY, "total": 2, "pmids": ["11", "12"]}}


def _check(**over: Any) -> dict[str, Any]:
    return {"claim": "A claim", "terms": _TERMS, "outcome": "holds", "read": ["pmid:11"], **over}


def _problems(entries: Any, core: tuple[str, ...] = ("A claim",), cited: Any = ()) -> list[str]:
    return claims.check_problems(entries, core, _SEARCHES, _SYMBOLS, set(cited))


def test_the_query_joins_the_gene_and_each_term_group() -> None:
    assert claims.search_query(["GENE1", "OLD"], ["enzyme", "active OR activity"]) == (
        '("GENE1"[tiab] OR "OLD"[tiab]) AND ("enzyme"[tiab]) '
        'AND ("active"[tiab] OR "activity"[tiab])'
    )


def test_a_checked_claim_passes() -> None:
    assert _problems([_check()]) == []


def test_every_core_claim_needs_a_check() -> None:
    assert any("no check" in p for p in _problems([_check()], ("A claim", "Another")))


def test_a_check_on_claim_text_matches_whatever_the_spacing_and_case() -> None:
    assert _problems([_check(claim="a  CLAIM")]) == []


def test_terms_that_were_not_searched_fail() -> None:
    assert any("run claim_search" in p for p in _problems([_check(terms=["other"])]))


def test_the_outcome_has_a_vocabulary() -> None:
    assert any("outcome must be" in p for p in _problems([_check(outcome="fine")]))


def test_papers_must_come_from_the_search() -> None:
    assert any("not in this search" in p for p in _problems([_check(read=["pmid:99"])]))


def test_a_search_with_results_must_be_read() -> None:
    assert any("no paper read" in p for p in _problems([_check(read=[])]))
    assert any("read them" in p for p in _problems([_check(read=[], outcome="not_found")]))


def test_a_changed_claim_needs_a_note_and_its_paper_cited() -> None:
    entry = _check(outcome="qualified", read=["pmid:11"])
    assert any("in 'note'" in p for p in _problems([entry], cited={"pmid:11"}))
    noted = {**entry, "note": "weaker than stated"}
    assert any("cite in the draft" in p for p in _problems([noted]))
    assert _problems([noted], cited={"pmid:11"}) == []


# ------------------------------------------------------------------ the audit

_ITEM = {
    "id": "abc",
    "text": "A sentence",
    "sources": [{"key": "pmid:1", "text": "The  exact words here."}],
}


def _verdict(**over: Any) -> dict[str, Any]:
    return {"id": "abc", "verdict": "supported", "quote": "exact words", **over}


def test_a_block_id_changes_with_its_words_only() -> None:
    assert claims.block_id("A  sentence") == claims.block_id("a sentence")
    assert claims.block_id("A sentence") != claims.block_id("Another sentence")


def test_a_supported_block_with_a_real_quote_passes() -> None:
    assert claims.audit_problems([_verdict()], [_ITEM]) == []


def test_a_block_without_a_verdict_fails() -> None:
    assert "not audited" in claims.audit_problems([], [_ITEM])[0][1]


@pytest.mark.parametrize("verdict", ["overstated", "unsupported"])
def test_an_adverse_verdict_fails_with_its_note(verdict: str) -> None:
    problem = claims.audit_problems([_verdict(verdict=verdict, note="says more")], [_ITEM])
    assert verdict in problem[0][1]
    assert "says more" in problem[0][1]


def test_an_unknown_verdict_fails() -> None:
    assert "must be one of" in claims.audit_problems([_verdict(verdict="ok")], [_ITEM])[0][1]


def test_a_quote_outside_the_abstract_fails() -> None:
    assert "not in the cited" in claims.audit_problems([_verdict(quote="invented")], [_ITEM])[0][1]


def test_an_empty_quote_fails() -> None:
    assert claims.audit_problems([_verdict(quote="")], [_ITEM])


def test_only_factual_blocks_citing_a_held_abstract_are_audited() -> None:
    blocks = [
        checker.Block("item", "Held [@pmid:1].", 1, "biology"),
        checker.Block("item", "Record [@uniprot_extra].", 2, "biology"),
        checker.Block("item", "No abstract [@pmid:2].", 3, "biology"),
        checker.Block("para", "*Interpretation:* a view [@pmid:1].", 4, "biology"),
        checker.Block("item", "A gap [@pmid:1].", 5, "gaps"),
    ]
    items = checker._audit_items(blocks, {"1": "The abstract."})
    assert [i["text"] for i in items] == ["Held ."]
    assert items[0]["sources"] == [{"key": "pmid:1", "text": "The abstract."}]
