# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The quality gates of a profile: clinical relevance beyond direct targets, constraint read by
its own release, safety carried across exposures, and claims kept apart from retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude/skills/gene-profile/scripts"
sys.path.insert(0, str(_SCRIPTS))

import check_profile  # noqa: E402
import profile_claims as claims  # noqa: E402
import profile_drugs as drugs  # noqa: E402
import profile_gates as gates  # noqa: E402
import profile_retrieval as retrieval  # noqa: E402
import profile_schema as ds  # noqa: E402
import profile_tables as tables  # noqa: E402
import profile_writer as writer  # noqa: E402

SELECTING = (
    "XYZ1 is a kinase inhibitor indicated in combination with abiraterone for adult patients "
    "with XYZ1-deficient metastatic prostate cancer as detected by an FDA-approved test."
)
ACTS_ELSEWHERE = {
    "drug_name": "OTHERTINIB",
    "brand_names": ["OTHERA"],
    "indications_and_usage": SELECTING,
    "mechanism_of_action": "12.1 Mechanism of Action Othertinib inhibits KIN1. "
    "Activation of KIN1 results from loss of XYZ1.",
}


# ── therapies ───────────────────────────────────────────────────────────────────────


def test_a_label_selecting_patients_by_the_gene_is_biomarker_selected_not_lost() -> None:
    selected, pathway = drugs.relate_labels([ACTS_ELSEWHERE], ["XYZ1"], [])
    assert [r["name"] for r in selected] == ["OTHERTINIB"]
    assert "XYZ1-deficient" in selected[0]["basis"]
    assert pathway == []


def test_a_label_naming_the_gene_only_in_its_mechanism_is_pathway_modulation() -> None:
    label = {**ACTS_ELSEWHERE, "indications_and_usage": "indicated for prostate cancer"}
    selected, pathway = drugs.relate_labels([label], ["XYZ1"], [])
    assert selected == []
    assert [r["name"] for r in pathway] == ["OTHERTINIB"]


def test_a_drug_on_the_gene_stays_in_the_drug_list() -> None:
    assert drugs.relate_labels([ACTS_ELSEWHERE], ["XYZ1"], ["othertinib"]) == ([], [])


def test_an_indication_naming_the_gene_to_say_it_is_untested_does_not_select() -> None:
    text = "Effects in women with inherited XYZ1 mutations have not been established."
    assert drugs.selection_clause(text, ["XYZ1"]) == ""


@pytest.mark.parametrize(
    "text",
    [
        "for tumours with one or more ABC/DEF/XYZ1 -alterations as detected by a test",
        "for adults with deleterious germline XYZ1 variants",
        "for XYZ1 wild-type colorectal cancer",
    ],
)
def test_selection_reads_the_ways_labels_state_status(text: str) -> None:
    assert drugs.selection_clause(text, ["XYZ1"])


def test_a_label_may_select_by_the_family_name() -> None:
    assert drugs.selection_clause("for ABC-mutated ovarian cancer", ["ABC1"])
    assert not drugs.selection_clause("for mutated ovarian cancer", ["ABC1"])
    assert not drugs.selection_clause("for TP-mutated cancer", ["TP53"])


def test_the_labels_own_heading_is_not_part_of_the_clause() -> None:
    clause = drugs.selection_clause("1 INDICATIONS AND USAGE for XYZ1-deficient cancer", ["XYZ1"])
    assert clause == "for XYZ1-deficient cancer"


def test_label_section_references_are_dropped_from_the_clause() -> None:
    assert drugs.selection_clause("for XYZ1-deficient cancer ( 1.2 )", ["XYZ1"]) == (
        "for XYZ1-deficient cancer"
    )


def test_every_selecting_indication_is_kept() -> None:
    text = (
        "• with fulvestrant for breast cancer with XYZ1 -alterations ( 1.1 ) "
        "• with abiraterone for XYZ1-deficient prostate cancer ( 1.2 )"
    )
    clause = drugs.selection_clause(text, ["XYZ1"])
    assert "breast" in clause and "prostate" in clause


def test_a_trial_counts_only_when_it_names_a_relevant_drug() -> None:
    assert drugs.trial_names_drug({"interventions": ["Othertinib 5 mg", "Placebo"]}, ["OTHERTINIB"])
    assert not drugs.trial_names_drug({"interventions": ["Trastuzumab"]}, ["OTHERTINIB"])


def test_a_diagnostic_needs_both_the_gene_and_the_drug_in_its_order() -> None:
    device = {"device": "Assay", "pma_number": "P1", "decision_date": "2026-06-12"}
    found = drugs.diagnostics_for(
        [
            {**device, "statement": "detect XYZ1 protein to select othertinib therapy"},
            {**device, "pma_number": "P2", "statement": "detect XYZ1 protein"},
            {**device, "pma_number": "P3", "statement": "detect ABC to select othertinib"},
        ],
        ["XYZ1"],
        ["OTHERTINIB"],
    )
    assert [d["pma_number"] for d in found] == ["P1"]


def test_late_phase_trials_lead_investigation_rows_whatever_their_size() -> None:
    rows = drugs.investigation_rows(
        [
            {"nct_id": "NCT1", "phases": ["PHASE2"], "enrollment": 5000},
            {"nct_id": "NCT2", "phases": ["PHASE3"], "enrollment": 300,
             "interventions": ["Drug", "Placebo", "Biospecimen Collection"]},
        ]
    )  # fmt: skip
    assert [r["nct_id"] for r in rows] == ["NCT2", "NCT1"]
    assert rows[0]["drugs"] == ["Drug"]


def test_relevance_table_shows_each_relation_with_dates_and_diagnostic() -> None:
    selected = drugs.attach_approval(
        {"name": "OTHERTINIB", "brands": [], "basis": SELECTING},
        {"approval_date": "2023-11-16", "efficacy_supplements": ["2026-09-16", "2026-06-12"],
         "application_number": "NDA1", "brand_names": ["OTHERA"]},
    )  # fmt: skip
    text = drugs.relevance_md(
        {},
        {
            "biomarker": [selected],
            "diagnostics": [
                {
                    "device": "Assay",
                    "pma_number": "P1",
                    "decision_date": "2026-06-12",
                    "drugs": ["OTHERTINIB"],
                }
            ],
        },  # fmt: skip
        "[@therapy_relevance]",
        lambda _n: "",
    )
    assert "Biomarker-selected therapy" in text
    assert "efficacy supplements 2026-09-16, 2026-06-12" in text
    assert "Assay (P1, 2026-06-12)" in text


def test_a_gene_linked_to_no_therapy_has_no_table() -> None:
    assert drugs.relevance_md({}, {}, "", lambda _n: "") == ""


def test_trial_is_named_by_acronym_id_or_paper() -> None:
    trial = {"acronym": "SHORT-1", "nct_id": "NCT1", "papers": ["9"]}
    assert tables.named_trial(trial, "in SHORT-1 the hazard")
    assert tables.named_trial(trial, "[@pmid:9]")
    assert not tables.named_trial(trial, "nothing")


def test_outcome_table_keeps_the_registry_name_and_linked_papers() -> None:
    data = {
        "trials": [
            {
                "acronym": "SHORT-1", "nct_id": "NCT1", "phases": ["PHASE3"], "enrollment": 12000,
                "status": "COMPLETED", "interventions": ["Drug", "Placebo"],
                "primary_outcomes": ["Death"], "papers": ["9"], "papers_linked": 4,
            }
        ]
    }  # fmt: skip
    text, keys = tables.outcome_trials_md(data, {"nct:NCT1", "pmid:9"})
    assert "SHORT-1" in text and "12,000" in text and "+3 more" in text
    assert "Placebo" not in text.split("\n")[2]
    assert keys == ["nct:NCT1", "pmid:9"]


# ── constraint ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("loeuf", "word"),
    [(0.30, "strong"), (0.5972, "no categorical call"), (0.65, "moderate"), (0.8, "no strong")],
)
def test_loeuf_reads_against_gnomads_v4_cutoffs(loeuf: float, word: str) -> None:
    assert word in tables.loeuf_reading(loeuf)


def test_a_value_between_the_published_cutoffs_is_given_its_interval() -> None:
    data = {"loeuf": 0.597, "oe_lof": 0.407, "oe_lof_lower": 0.284, "obs_lof": 19, "exp_lof": 46.7}
    text = tables.constraint_md(data, {"gnomad_constraint"}, gnomad_v4=True)
    assert "0.41 (0.28–0.60)" in text
    assert "most constrained 25% of genes" in text
    assert "0.45 for v4.1.1" in text


def test_a_gene_past_the_median_is_not_described_as_constrained() -> None:
    assert tables.loeuf_percentile(1.14) == "less constrained than most genes"
    assert tables.loeuf_percentile(0.3) == "within the most constrained 10% of genes"


def test_a_failed_frequency_lookup_is_not_checked_not_absent() -> None:
    import profile_variants as variants

    assert variants.frequency_text([{"found": False, "failed": True}])[0].startswith("Not checked")
    assert variants.frequency_text([{"found": False}])[0] == "Not in gnomAD"


def test_earlier_runs_keep_their_bands() -> None:
    assert "0.45" not in tables.constraint_md({"loeuf": 0.5}, {"gnomad_constraint"})


@pytest.mark.parametrize(
    ("text", "loeuf", "flagged"),
    [
        ("There is no strong depletion of pLoF variants in gnomAD", 0.60, True),
        ("There is no strong depletion of pLoF variants in gnomAD", 0.80, False),
        ("The gene is strongly constrained against pLoF", 0.60, True),
        ("The gene is strongly constrained against pLoF", 0.30, False),
        ("Moderate pLoF depletion, LOEUF 0.60", 0.60, False),
        ("There is no strong depletion of T cells", 0.60, False),
    ],
)
def test_constraint_wording_must_match_the_value(text: str, loeuf: float, flagged: bool) -> None:
    assert bool(tables.constraint_wording_problem(text, loeuf)) is flagged


def test_measures_stay_apart_and_each_says_what_it_does_not_tell() -> None:
    text = tables.tolerance_md(
        {
            "gnomad_constraint": {"loeuf": 0.6},
            "clingen_dosage": {
                "curated": True,
                "haploinsufficiency_score": "3",
                "haploinsufficiency": "Sufficient evidence for dosage pathogenicity",
            },
            "clinvar": {"pathogenic": [{}] * 10, "likely_pathogenic": [{}] * 5},
            "depmap": {"dependency_fraction": 0.0068},
        },
        "tumour-suppressor-like",
        {"gnomad_constraint", "clingen_dosage", "clinvar", "depmap"},
    )
    assert "haploinsufficiency score 3" in text
    assert "15 pathogenic or likely pathogenic" in text
    assert "tumour-suppressor-like" in text
    assert "0.7% of DepMap lines" in text
    assert "no record measures this" in text


# ── writer files ────────────────────────────────────────────────────────────────────


def _strengths() -> dict[str, dict[str, Any]]:
    return {
        "1": {"design": "animal model", "species": ["mouse"], "last_author": "Ann"},
        "2": {"design": "cells / in vitro", "species": ["human"], "last_author": "Bo"},
        "3": {"design": "cells / in vitro", "species": ["human"], "last_author": "Cy"},
        "4": {"design": "animal model", "species": ["mouse"], "last_author": "Di"},
    }


@pytest.mark.parametrize(
    ("step", "fragment"),
    [
        ({"status": "established", "keys": ["pmid:1"]}, "two independent groups"),
        ({"status": "established", "keys": ["pmid:2", "pmid:3"]}, "two kinds of evidence"),
        ({"status": "established", "keys": ["pmid:1", "pmid:2"]}, "in vivo evidence"),
        ({"status": "strong", "keys": ["pmid:2"]}, "two independent groups"),
        ({"status": "context_dependent", "keys": ["pmid:2"]}, "'context'"),
        ({"status": "contested", "keys": ["pmid:2"]}, "'against'"),
        ({"keys": ["pmid:2"]}, "status must be"),
    ],
)
def test_the_records_cap_a_mechanisms_status(step: dict[str, Any], fragment: str) -> None:
    problems = writer.status_problems([step], _strengths())
    assert any(fragment in p for p in problems)


@pytest.mark.parametrize(
    "step",
    [
        {"status": "established", "keys": ["pmid:1", "pmid:4", "pmid:2"]},
        {"status": "strong", "keys": ["pmid:2", "pmid:3"]},
        {"status": "emerging", "keys": ["pmid:2"]},
        {"status": "context_dependent", "context": "HEK-293 cells", "keys": ["pmid:2"]},
        {"status": "contested", "against": ["pmid:3"], "keys": ["pmid:2"]},
    ],
)
def test_a_status_the_records_bear_passes(step: dict[str, Any]) -> None:
    assert writer.status_problems([step], _strengths()) == []


_LIFELONG = {"extent": "partial", "duration": "lifelong", "scope": "systemic"}
_DRUG = {"extent": "partial", "duration": "chronic", "scope": "systemic"}


def _risk(**kw: Any) -> dict[str, Any]:
    return {
        "risk": "Tumours", "evidence": "germline_loss", "keys": ["hpo"],
        "observed_exposure": _LIFELONG, "planned_exposure": _DRUG, "modality": "inhibitor",
        "assumption": "dose response extends to partial, reversible inhibition",
        "status": "extrapolated", **kw,
    }  # fmt: skip


def test_a_risk_carried_from_genetics_to_a_drug_must_name_its_assumption() -> None:
    assert writer.safety_problems([_risk()], {"hpo": 1}) == []
    problems = writer.safety_problems([_risk(assumption="")], {"hpo": 1})
    assert any("assumption" in p for p in problems)


def test_a_carried_risk_is_never_recorded_as_observed() -> None:
    problems = writer.safety_problems([_risk(status="observed")], {"hpo": 1})
    assert any("extrapolated or untested" in p for p in problems)


def test_a_drugs_own_finding_needs_no_bridge() -> None:
    same = _risk(
        evidence="pharmacology", planned_exposure=_LIFELONG, assumption="", status="observed"
    )
    assert writer.safety_problems([same], {"hpo": 1}) == []


def test_exposure_axes_use_the_vocabulary() -> None:
    bad = _risk(planned_exposure={"extent": "some", "duration": "chronic", "scope": "systemic"})
    assert any("planned_exposure.extent" in p for p in writer.safety_problems([bad], {"hpo": 1}))


# ── claims ──────────────────────────────────────────────────────────────────────────


def _item(text: str = "PTEN lowered PIP3") -> dict[str, Any]:
    return {"id": "a", "text": text, "sources": [{"text": "PTEN lowered PIP3 levels in the cells"}]}


def test_a_few_shared_words_do_not_validate_a_claim() -> None:
    short = [{"id": "a", "verdict": "supported", "quote": "lowered PIP3"}]
    long = [{"id": "a", "verdict": "supported", "quote": "PTEN lowered PIP3 levels in"}]
    assert claims.audit_problems(short, [_item()])
    assert claims.audit_problems(long, [_item()]) == []


def test_verdicts_are_dated_once() -> None:
    entries = [{"id": "a"}, {"id": "b", "checked_on": "2026-01-01"}]
    assert claims.stamp_verdicts(entries, "2026-11-01")
    assert [e["checked_on"] for e in entries] == ["2026-11-01", "2026-01-01"]
    assert not claims.stamp_verdicts(entries, "2026-12-01")


def test_the_ledger_records_each_claim_from_the_other_files() -> None:
    blocks = [
        {"id": "a", "section": "biology", "text": "X binds Y", "keys": ["pmid:1", "pmid:2"],
         "auditable": True},
        {"id": "b", "section": "biology", "text": "Z", "keys": ["pmid:3"], "auditable": True},
        {"id": "c", "section": "genetics", "text": "LOEUF", "keys": ["gnomad_constraint"],
         "auditable": False},
    ]  # fmt: skip
    verdicts = [{"id": "a", "verdict": "supported", "quote": "q", "checked_on": "2026-11-01"}]
    checks = [{"outcome": "qualified", "read": ["pmid:2"], "note": "only in cells"}]
    entries = claims.ledger(blocks, verdicts, checks, _strengths(), lambda k: "cells")
    by_id = {e["id"]: e for e in entries}
    assert by_id["C-a"]["validation"] == "supported"
    assert by_id["C-a"]["last_verified"] == "2026-11-01"
    assert by_id["C-a"]["qualified_by"] == ["only in cells"]
    assert by_id["C-a"]["sources"][0]["organism"] == ["mouse"]
    assert by_id["C-b"]["validation"] == claims.NOT_AUDITED
    assert by_id["C-c"]["validation"] == claims.RECORD_ONLY
    assert claims.ledger_summary(entries) == {
        "supported": 1, claims.NOT_AUDITED: 1, claims.RECORD_ONLY: 1,
    }  # fmt: skip


def _review(**kw: Any) -> dict[str, Any]:
    return {
        "reader": "fresh reader",
        "checks": dict.fromkeys(claims.REVIEW_CHECKS, "clear"),
        "findings": [],
        **kw,
    }


def test_an_open_high_finding_blocks_release_and_a_recorded_limit_does_not() -> None:
    assert claims.review_problems(_review()) == []
    finding = {"check": "safety_reasoning", "severity": "high", "finding": "over-reach"}
    assert claims.review_problems(_review(findings=[{**finding, "resolution": "open"}]))
    assert not claims.review_problems(_review(findings=[{**finding, "resolution": "limitation"}]))


def test_a_review_answers_every_question() -> None:
    assert any("status_is_current" in p for p in claims.review_problems(_review(checks={})))


# ── retrieval and gates ─────────────────────────────────────────────────────────────


def _manifest(entries: dict[str, str], depth: str = "standard") -> ds.Manifest:
    return ds.Manifest(
        query="XYZ1",
        gene="XYZ1",
        depth=depth,
        generated_at="2026-11-01T00:00:00",
        entries=[
            ds.ManifestEntry(key=k, section="clinical", db_name=k, status=v, url="")
            for k, v in entries.items()
        ],
        template=4,
    )


def _raw(run: Path, key: str, status: str, data: Any) -> None:
    (run / "raw").mkdir(exist_ok=True)
    rec = ds.RawRecord(
        key=key, section="clinical", db_name=key, tool="t", args={}, status=status,
        retrieved_at="2026-11-01", url="", data=data,
    )  # fmt: skip
    (run / "raw" / f"{key}.json").write_text(rec.model_dump_json())


def _row(rows: list[dict[str, Any]], domain: str, dimension: str) -> dict[str, Any]:
    return next(r for r in rows if r["domain"] == domain and r["dimension"] == dimension)


def test_the_matrix_says_what_was_searched_and_how_much_was_kept(tmp_path: Path) -> None:
    _raw(tmp_path, "ct_gene", "ok", {"total": 414, "trials": [{}] * 50})
    m = _manifest({"ct_gene": "ok", "ct_biomarker": "error", "faers": "skipped"})
    rows = retrieval.matrix(m, tmp_path)
    gene = _row(rows, "Clinical trials", "Gene")
    assert gene["status"] == retrieval.SEARCHED
    assert gene["capped"] == ["ct_gene: 50 of 414"]
    assert _row(rows, "Clinical trials", "Biomarker status")["status"] == retrieval.FAILED
    assert _row(rows, "Clinical trials", "Pathway")["status"] == retrieval.NO_SOURCE
    assert _row(rows, "Pharmacovigilance", "Per-drug query")["status"] == retrieval.NOT_APPLICABLE
    assert "Clinical trials / Biomarker status" in retrieval.failed_dimensions(rows)


def _rep(errors: tuple[str, ...] = (), warnings: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        errors=[check_profile.Issue(c, 0, "m") for c in errors],
        warnings=[check_profile.Issue(c, 0, "m") for c in warnings],
    )


def _gate_run(tmp_path: Path, review: dict[str, Any] | None) -> ds.Manifest:
    _raw(tmp_path, "ct_gene", "ok", {"total": 1, "trials": [{}]})
    if review is not None:
        (tmp_path / claims.REVIEW_FILE).write_text(json.dumps(review))
    return _manifest({"ct_gene": "ok"})


def test_a_clean_reviewed_run_is_released(tmp_path: Path) -> None:
    result = gates.evaluate(_rep(), _gate_run(tmp_path, _review()), tmp_path)
    assert gates.released(result)
    assert "released" in gates.release_line(result)


def test_no_review_means_not_released(tmp_path: Path) -> None:
    result = gates.evaluate(_rep(), _gate_run(tmp_path, None), tmp_path)
    assert not gates.released(result)
    assert "not released" in gates.release_line(result)


def test_an_unresolved_audit_fails_its_gate_and_blocks_release(tmp_path: Path) -> None:
    rep = _rep(errors=("E_CLAIM_AUDIT",))
    result = gates.evaluate(rep, _gate_run(tmp_path, _review()), tmp_path)
    assert result["claims"]["status"] == gates.FAIL
    line = gates.release_line(result)
    assert "not released" in line and "Claim validation" in line


def test_warnings_pass_but_are_named(tmp_path: Path) -> None:
    rep = _rep(warnings=("W_NUMBER_NOT_IN_SOURCE",))
    result = gates.evaluate(rep, _gate_run(tmp_path, _review()), tmp_path)
    assert result["numbers"]["status"] == gates.REVIEW
    assert gates.released(result)
    assert "Quantitative" in gates.release_line(result)


def test_a_failed_search_fails_retrieval(tmp_path: Path) -> None:
    m = _manifest({"ct_biomarker": "error"})
    assert gates.evaluate(_rep(), m, tmp_path)["retrieval"]["status"] == gates.FAIL


def test_a_quick_profile_is_never_called_released(tmp_path: Path) -> None:
    result = gates.evaluate(_rep(), _manifest({}, depth="quick"), tmp_path)
    assert result["release"]["status"] == gates.NOT_RUN
    assert "quick profile" in gates.release_line(result)
    assert not gates.released(result)


def test_every_checker_code_belongs_to_a_gate_or_is_structural() -> None:
    import re

    source = (_SCRIPTS / "check_profile.py").read_text()
    used = set(re.findall(r'"([EW]_[A-Z_0-9]+)"', source))
    mapped = set().union(*gates.GATE_CODES.values())
    structural = {
        "E_MISSING_TABLE", "E_UNKNOWN_BLOCK", "E_MISSING_SECTION", "W_GENERATED_SECTION",
        "W_EMPTY_SECTION", "W_GUARDS_UNAVAILABLE",
    }  # fmt: skip
    assert used - mapped - structural == set()
