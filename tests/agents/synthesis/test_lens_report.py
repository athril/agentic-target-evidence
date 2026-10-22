# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the source-aware per-lens report writer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.synthesis.report import lens_report
from agents.synthesis.report.lens_report import _render, write_lens_report
from schemas.evidence import (
    CoreClaim,
    DataClass,
    Direction,
    Evidence,
    EvidenceType,
    Provenance,
)
from schemas.verdicts import AxisVerdict, LensVerdict


def _prov(trace_id: str) -> Provenance:
    return Provenance(
        agent_name="test",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id=trace_id,
    )


def _evidence(run_id, trace_id, *, evidence_type, source, source_link, title="") -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="PRMT5",
        disease="pancreatic cancer",
        evidence_type=evidence_type,
        scope="abstract",
        source=source,
        source_link=source_link,
        provenance=_prov(trace_id),
        classification=DataClass.NON_SENSITIVE,
        extra={"title": title} if title else {},
    )


def _claim(run_id, trace_id, *, evidence_type, source_evidence_id, text) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=source_evidence_id,
        run_id=run_id,
        gene="PRMT5",
        disease="pancreatic cancer",
        evidence_type=evidence_type,
        claim_text=text,
        direction=Direction.INHIBIT,
        confidence=0.7,
        provenance=_prov(trace_id),
        classification=DataClass.NON_SENSITIVE,
    )


def _verdict(run_id, trace_id, *, axes) -> LensVerdict:
    return LensVerdict(
        run_id=run_id,
        trace_id=trace_id,
        lens="clinical",
        target_gene="PRMT5",
        disease="pancreatic cancer",
        direction=Direction.INHIBIT,
        overall_verdict="support",
        confidence=0.8,
        axes=axes,
        rationale="Clinical precedent exists.",
        narrative="Several trials are underway.",
    )


@pytest.fixture()
def run_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def trace_id() -> str:
    return "trace-lens-report"


def test_render_links_original_sources(run_id, trace_id):
    trial = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source="NCT01234567",
        source_link="https://clinicaltrials.gov/study/NCT01234567",
        title="A Phase II study of a PRMT5 inhibitor",
    )
    claim = _claim(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source_evidence_id=trial.evidence_id,
        text="A PRMT5 inhibitor reached Phase II in pancreatic cancer.",
    )
    axis = AxisVerdict(
        axis="clinical_precedent",
        verdict=True,
        confidence=0.8,
        rationale="Active trial.",
        supporting_claim_ids=[str(claim.evidence_id)],
    )
    md = _render(_verdict(run_id, trace_id, axes=[axis]), [trial], [claim])

    # Original source is linked, not an opaque UUID.
    assert "https://clinicaltrials.gov/study/NCT01234567" in md
    assert "NCT01234567" in md
    # The claim resolves to source citation [1], and the axis cites it.
    assert "## Evidence Considered" in md
    assert "## Extracted Claims" in md
    assert "| [1] |" in md  # claim row references source 1
    # Axis "Sources" column resolves the claim id to citation [1].
    clinical_row = [ln for ln in md.splitlines() if ln.startswith("| Clinical Precedent")][0]
    assert "[1]" in clinical_row


def test_render_evidence_section_has_quality_year_author_columns(run_id, trace_id):
    """Literature rows render in their own Literature table with
    Quality/Year/First-Author columns, same schema as report.md and full_report.md."""
    article = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.ARTICLE,
        source="PMID:111",
        source_link="https://pubmed.ncbi.nlm.nih.gov/111/",
        title="TRPC6 in FSGS",
    )
    article.extra["pub_year"] = 2022
    article.extra["authors"] = ["Smith J", "Doe A"]
    claim = _claim(
        run_id,
        trace_id,
        evidence_type=EvidenceType.ARTICLE,
        source_evidence_id=article.evidence_id,
        text="TRPC6 variant linked to disease.",
    )
    claim = claim.__class__(**{**claim.model_dump(), "topics": ["clinical"]})
    quality_map = {str(article.evidence_id): {"sjr_score": 0.9}}
    md = _render(_verdict(run_id, trace_id, axes=[]), [article], [claim], None, quality_map)

    assert "### Literature (1)" in md
    assert "| # | Source | Detail | Quality | Year | First Author |" in md
    assert (
        "| 1 | [PMID:111](https://pubmed.ncbi.nlm.nih.gov/111/) | TRPC6 in FSGS | ★★★ | 2022 | Smith J |"
        in md
    )
    # Citation numbering is unaffected — still referenced as [1] in claims.
    assert "| [1] |" in md


def test_render_evidence_section_without_quality_map_still_renders(run_id, trace_id):
    """quality_map is optional — omitted rows render "—" for Quality, not an error.
    Non-literature evidence renders in its own Empirical table."""
    trial = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source="NCT01234567",
        source_link="https://clinicaltrials.gov/study/NCT01234567",
    )
    md = _render(_verdict(run_id, trace_id, axes=[]), [trial], [])
    assert "### Empirical (1)" in md
    assert "| # | Source | Type | Detail | Quality |" in md
    assert (
        "| 1 | [NCT01234567](https://clinicaltrials.gov/study/NCT01234567) | Clinical Trials | — | — |"
        in md
    )


def test_render_filters_to_lens_evidence_types(run_id, trace_id):
    """A clinical lens must not surface non-trial evidence."""
    trial = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source="NCT09999999",
        source_link="https://clinicaltrials.gov/study/NCT09999999",
    )
    patent = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.PATENT,
        source="US1234567B2",
        source_link="https://patents.google.com/patent/US1234567B2",
    )
    md = _render(_verdict(run_id, trace_id, axes=[]), [trial, patent], [])
    assert "NCT09999999" in md
    assert "US1234567B2" not in md  # patent belongs to the commercial lens


def test_render_handles_no_evidence(run_id, trace_id):
    md = _render(_verdict(run_id, trace_id, axes=[]), [], [])
    assert "No source evidence" in md
    assert "Evidence considered:** 0 source(s)" in md


def test_patent_url_fallback(run_id, trace_id):
    patent = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.PATENT,
        source="US7654321B2",
        source_link="",  # no link supplied — must synthesise a Google Patents URL
    )
    verdict = LensVerdict(
        run_id=run_id,
        trace_id=trace_id,
        lens="commercial",
        target_gene="PRMT5",
        disease="pancreatic cancer",
        overall_verdict="oppose",
        confidence=0.7,
        axes=[],
        rationale="Crowded IP.",
    )
    md = _render(verdict, [patent], [])
    assert "https://patents.google.com/patent/US7654321B2" in md


def test_write_lens_report_writes_file(run_id, trace_id, tmp_path, monkeypatch):
    monkeypatch.setattr(lens_report, "_REPORT_ROOT", tmp_path / "report")
    trial = _evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source="NCT05555555",
        source_link="https://clinicaltrials.gov/study/NCT05555555",
    )
    path = write_lens_report(
        _verdict(run_id, trace_id, axes=[]),
        "EFO_0003860",
        evidence_rows=[trial],
        claims=[],
    )
    assert path is not None
    written = Path(path)
    assert written.exists()
    assert "NCT05555555" in written.read_text()


def test_write_lens_report_backward_compatible_no_evidence(run_id, trace_id, tmp_path, monkeypatch):
    """Called with only the verdict (legacy signature), it must still write a file."""
    monkeypatch.setattr(lens_report, "_REPORT_ROOT", tmp_path / "report")
    path = write_lens_report(_verdict(run_id, trace_id, axes=[]), "EFO_0003860")
    assert path is not None
    assert Path(path).exists()


# ---------------------------------------------------------------------------
# 0-claims banner
# ---------------------------------------------------------------------------


def _genetics_evidence(run_id, trace_id, *, source="opentargets") -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="TRPC6",
        disease="focal segmental glomerulosclerosis",
        evidence_type=EvidenceType.GENETICS,
        scope="abstract",
        source=source,
        source_link="https://platform.opentargets.org",
        provenance=_prov(trace_id),
        classification=DataClass.NON_SENSITIVE,
        extra={"genetic_score": 0.956, "overall_score": 0.85},
    )


def _genetics_verdict(run_id, trace_id) -> LensVerdict:
    return LensVerdict(
        run_id=run_id,
        trace_id=trace_id,
        lens="genetics",
        target_gene="TRPC6",
        disease="focal segmental glomerulosclerosis",
        overall_verdict="support",
        confidence=0.85,
        axes=[],
        rationale="Strong OT genetic association.",
    )


def test_render_banner_when_zero_claims_but_source_evidence_exists(run_id, trace_id):
    """When 0 claims but N source evidence rows exist, show the diagnostic banner."""
    ev = _genetics_evidence(run_id, trace_id)
    md = _render(_genetics_verdict(run_id, trace_id), [ev], [])

    assert "Structured-claim extraction returned 0" in md
    assert "source record(s) were reasoned over directly" in md
    assert "extraction.dropped_structured" in md
    # Must NOT show the default "No atomic claims" message
    assert "No atomic claims were extracted" not in md


def test_render_normal_claims_section_when_claims_present(run_id, trace_id):
    """When claims exist, show the normal extracted-claims table, not the banner."""
    ev = _genetics_evidence(run_id, trace_id)
    claim = _claim(
        run_id,
        trace_id,
        evidence_type=EvidenceType.GENETICS,
        source_evidence_id=ev.evidence_id,
        text="TRPC6 shows high OT genetic association with FSGS.",
    )
    claim = claim.__class__(
        **{**claim.model_dump(), "evidence_id": uuid.uuid4(), "source_evidence_id": ev.evidence_id}
    )
    md = _render(_genetics_verdict(run_id, trace_id), [ev], [claim])

    assert "Structured-claim extraction returned 0" not in md
    assert "TRPC6 shows high OT genetic association" in md


def test_render_no_banner_when_both_zero(run_id, trace_id):
    """When 0 claims AND 0 evidence, show the normal empty-evidence message, not the banner."""
    md = _render(_genetics_verdict(run_id, trace_id), [], [])

    assert "Structured-claim extraction returned 0" not in md
    assert "No atomic claims were extracted" in md


def test_guard_checks_section_lists_every_outcome(run_id, trace_id):
    """Not-evaluated checks are shown, so a missing input never reads as a pass."""
    from schemas.verdicts import GuardCheck

    v = _verdict(run_id, trace_id, axes=[]).model_copy(
        update={
            "guard_checks": [
                GuardCheck(family="clinical_trial", status="passed"),
                GuardCheck(
                    family="quantity", status="flagged", detail="quantity.phase2_trial_count"
                ),
                GuardCheck(
                    family="dependency_relevance",
                    status="not_evaluated",
                    detail="no DepMap or Project Score dependency data",
                ),
            ]
        }
    )
    md = _render(v, [], [])
    assert "## Automated Checks" in md
    assert "| Trial phase / status | passed | — |" in md
    assert "| Quantities vs. data bands | **flagged** | quantity.phase2_trial_count |" in md
    assert "not evaluated | no DepMap or Project Score dependency data" in md


def test_guard_checks_section_absent_without_checks(run_id, trace_id):
    assert "## Automated Checks" not in _render(_verdict(run_id, trace_id, axes=[]), [], [])


def test_render_version_on_top_and_run_info_at_bottom(run_id, trace_id):
    from agents.synthesis.report.provenance import Provenance, short_version

    v = _verdict(run_id, trace_id, axes=[])
    prov = Provenance(
        policy="hybrid",
        stage_models={
            "screening": "ollama/q",
            "clinical_lens": "anthropic/sonnet",
            "critic": "anthropic/opus",
        },
    )
    md = _render(v, [], [], provenance=prov)
    header = md.split("---", 1)[0]
    assert f"**Version:** {short_version()}" in header
    assert "Generated" not in header
    footer = md.rsplit("## Run Information", 1)[1]
    assert "- **Generated:**" in footer
    assert "- **Routing policy:** `hybrid`" in footer
    assert "| Evidence screening | `ollama/q` |" in footer
    assert "| Clinical lens | `anthropic/sonnet` |" in footer
    # Only the steps that fed this lens — not post-lens reasoning.
    assert "anthropic/opus" not in footer


def test_render_run_info_without_provenance(run_id, trace_id):
    md = _render(_verdict(run_id, trace_id, axes=[]), [], [])
    footer = md.rsplit("## Run Information", 1)[1]
    assert "- **Generated:**" in footer
    assert "Routing policy" not in footer


def test_axis_sources_separate_added_from_cited_without_repeats(run_id, trace_id):
    trials = [
        _evidence(
            run_id,
            trace_id,
            evidence_type=EvidenceType.CLINICAL_TRIAL,
            source=f"NCT0000000{i}",
            source_link=f"https://clinicaltrials.gov/study/NCT0000000{i}",
        )
        for i in range(2)
    ]
    cited = _claim(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source_evidence_id=trials[0].evidence_id,
        text="Trial A, phase 2.",
    )
    same_source = _claim(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source_evidence_id=trials[0].evidence_id,
        text="Trial A enrolled 40.",
    )
    other = _claim(
        run_id,
        trace_id,
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        source_evidence_id=trials[1].evidence_id,
        text="Trial B, phase 3.",
    )
    added = [str(same_source.evidence_id), str(other.evidence_id)]
    axis = AxisVerdict(
        axis="clinical_precedent",
        verdict=True,
        confidence=0.8,
        rationale="Trials.",
        supporting_claim_ids=[str(cited.evidence_id), *added],
        added_claim_ids=added,
    )
    md = _render(_verdict(run_id, trace_id, axes=[axis]), trials, [cited, same_source, other])
    row = [ln for ln in md.splitlines() if ln.startswith("| Clinical Precedent")][0]
    sources = row.split("|")[-2]
    cited_part, added_part = sources.split("· added:")
    assert "[1]" in cited_part and "[1]" not in added_part  # source 1 shown once
    assert "[2]" in added_part


def _conflict_flag():
    from schemas.verdicts import ValidationFlag

    return ValidationFlag(
        lens="clinical", severity="medium", rule_id="verdict_axis_conflict", message="x"
    )


def _unfavourable_axis():
    return AxisVerdict(
        axis="clinical_precedent", verdict=False, confidence=0.6, rationale="No precedent."
    )


def test_header_shows_model_value_and_axis_conflict(run_id, trace_id):
    v = _verdict(run_id, trace_id, axes=[_unfavourable_axis()]).model_copy(
        update={"confidence": 0.0, "model_confidence": 0.85, "validation_flags": [_conflict_flag()]}
    )
    md = _render(v, [], [])
    assert "model stated 85%" in md
    assert "contradicts every scored axis" in md


def test_a_conflict_flag_left_by_the_models_verdict_is_not_shown_once_a_rule_aligned_it(
    run_id, trace_id
):
    """The flag was raised on the model's "oppose"; a rule then set "support" from the
    favourable axes, and the summary is the model's reading of its own verdict."""
    favourable = AxisVerdict(
        axis="clinical_precedent", verdict=True, confidence=0.6, rationale="Precedent."
    )
    v = _verdict(run_id, trace_id, axes=[favourable]).model_copy(
        update={"validation_flags": [_conflict_flag()], "model_verdict": "oppose"}
    )
    md = _render(v, [], [])
    assert "contradicts every scored axis" not in md
    assert "Set by rule:** the lens model's verdict was oppose" in md
    assert "## Summary (the model's reading)" in md


def test_automated_checks_list_corrections_and_other_flags(run_id, trace_id):
    from schemas.verdicts import ValidationFlag

    v = _verdict(run_id, trace_id, axes=[]).model_copy(
        update={
            "validation_flags": [
                ValidationFlag(
                    lens="clinical",
                    severity="low",
                    rule_id="prose_repaired",
                    claim_excerpt="Two Phase 3 trials exist.",
                    message="Rewritten to match the data: One Phase 3 trial exists.",
                ),
                ValidationFlag(
                    lens="clinical",
                    severity="medium",
                    rule_id="numeric_grounding",
                    claim_excerpt="x",
                    message="1 number(s) ...",
                ),
            ]
        }
    )
    md = _render(v, [], [])
    assert "## Automated Checks" in md
    assert "“Two Phase 3 trials exist.” → One Phase 3 trial exists." in md
    assert "`numeric_grounding` (medium)" in md


@pytest.mark.parametrize(
    ("overall", "axes", "expected"),
    [
        ("support", [True], "strong evidence, 77%"),
        ("neutral", [True, False, False], "— (mixed: 1 favourable, 2 unfavourable)"),
        ("neutral", [False, True, None], "— (mixed: 1 favourable, 1 unfavourable, 1 uncertain)"),
        ("insufficient_evidence", [], "— (insufficient evidence)"),
    ],
)
def test_confidence_text_shows_a_tally_for_non_directional_verdicts(overall, axes, expected):
    from agents.synthesis.report.lens_report import confidence_text

    assert confidence_text(overall, 0.77, axes) == expected


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.0, "weak evidence, 0%"),
        (0.3, "weak evidence, 30%"),
        (0.396, "moderate evidence, 40%"),
        (0.5, "moderate evidence, 50%"),
        (0.69, "moderate evidence, 69%"),
        (0.7, "strong evidence, 70%"),
        (0.95, "strong evidence, 95%"),
    ],
)
def test_confidence_text_bands_a_directional_verdict(confidence, expected):
    from agents.synthesis.report.lens_report import confidence_text

    assert confidence_text("oppose", confidence, [False]) == expected


def test_known_drugs_header_counts_distinct_drugs_not_indication_rows():
    """Open Targets lists a drug once per indication: 3 drugs over 7 rows must read as
    3 drugs, 1 approved, 1 more in Phase 3 — never "7 rows — 4 approved"."""
    from agents.synthesis.report.lens_report import _ot_known_drugs_section

    def row(name: str, approved: bool, phase: float, disease: str) -> dict:
        return {
            "drug_name": name,
            "is_approved": approved,
            "max_phase": phase,
            "mechanism_of_action": "inhibitor",
            "disease_name": disease,
        }

    drugs = [
        row("DRUG-A", True, 4, "disease 1"),
        row("DRUG-A", True, 4, "disease 2"),
        row("DRUG-A", False, 3, "disease 3"),
        row("DRUG-A HYDROCHLORIDE", True, 4, "disease 4"),  # salt form of the same drug
        row("DRUG-B", False, 3, "disease 1"),
        row("DRUG-B", False, 2, "disease 5"),
        row("DRUG-C", False, 1, "disease 1"),
    ]
    section = _ot_known_drugs_section({"known_drugs": drugs, "known_drugs_count": 3})
    assert "3 distinct drug(s) — **1 approved**" in section
    assert "1 more reaching Phase 3" in section
    assert "7 drug-indication row(s)" in section


def test_banner_shows_the_direction_reading_not_the_requested_direction(run_id, trace_id):
    """The banner printed the requested direction as if genetics had derived it, and
    "degrade" as "ACTIVATE / RESTORE"."""
    v = _verdict(run_id, trace_id, axes=[]).model_copy(
        update={"lens": "genetics", "direction": Direction.DEGRADE}
    )
    md = _render(v, [], [])
    assert "ACTIVATE" not in md and "Genetics-derived" not in md
    summary = "The direction-of-effect evidence supports reducing the target's activity."
    md = _render(v.model_copy(update={"direction_of_effect": summary}), [], [])
    assert f"> **Direction of effect:** {summary}" in md


# --------------------------------------------------------------- split-lens decision trace


def _split_verdict() -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="safety",
        target_gene="GENE-A",
        disease="disease one",
        overall_verdict="support",
        question_results=[
            {
                "question_id": "liability_resolution",
                "facts": "Candidate liabilities (numbered):\n1. organ system a [medium; src]: e1",
                "answer": {
                    "liabilities": [
                        {
                            "index": 1,
                            "status": "resolved_by_human_data",
                            "claim_ids": ["c1"],
                            "note": "n",
                        }
                    ]
                },
                "shown_claim_ids": ["c1", "c2"],
                "dropped_claim_ids": ["x"],
                "attempts": 2,
                "notes": ["liability 2 not answered; unresolved"],
            },
            {
                "question_id": "human_harm",
                "facts": "",
                "answer": {"harm_reported": False, "events": []},
                "shown_claim_ids": [],
                "dropped_claim_ids": [],
                "attempts": 1,
                "notes": [],
            },
            {
                "question_id": "tissue",
                "facts": "",
                "answer": {
                    "disease_cell_type_expression": "reported",
                    "claim_ids": ["c1"],
                    "off_target_tissues_of_concern": ["Tissue_B"],
                },
                "shown_claim_ids": ["c1"],
                "dropped_claim_ids": [],
                "attempts": 1,
                "notes": [],
            },
        ],
    )


def test_decision_trace_lists_each_question_with_numbered_sources():
    from agents.synthesis.report.lens_report import _decision_trace_section

    text = _decision_trace_section(_split_verdict(), {"c1": 3}, {})
    assert "## Decision Trace" in text
    assert "| organ system a | resolved by human data | [3] | n |" in text
    assert "Harm reported in the requested direction: **no**" in text
    assert "**reported** (sources: [3])" in text and "Tissue_B" in text
    assert "2 claim(s) shown · 2 attempt(s) · 1 citation(s) dropped" in text
    assert "Corrected in code: liability 2 not answered; unresolved" in text


def test_decision_trace_is_absent_for_a_single_call_lens():
    from agents.synthesis.report.lens_report import _decision_trace_section

    single = _split_verdict().model_copy(update={"question_results": []})
    assert _decision_trace_section(single, {}, {}) == ""
