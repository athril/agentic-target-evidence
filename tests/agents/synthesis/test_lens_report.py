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
