# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the full_report.md renderer (render_full_report).

Exercises the categorized, link-rich companion dossier built from persisted
``EvidenceRow`` objects. Uses lightweight stand-ins that expose the same
attributes the renderer reads: source, source_link, evidence_type, extra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agents.synthesis.report.agent import render_full_report


@dataclass
class _Row:
    """Minimal EvidenceRow stand-in (evidence_type is a plain string, as in the DB)."""

    source: str
    source_link: str
    evidence_type: str
    extra: dict[str, Any] = field(default_factory=dict)


def _keep(rationale: str = "relevant") -> dict:
    return {"screening_verdict": {"verdict": "keep", "rationale": rationale}}


def _drop() -> dict:
    return {"screening_verdict": {"verdict": "drop", "rationale": "off-topic"}}


def _rows() -> list[_Row]:
    return [
        _Row(
            source="PMID:12345",
            source_link="https://pubmed.ncbi.nlm.nih.gov/12345/",
            evidence_type="article",
            extra={
                **_keep("strong genetic link"),
                "title": "TP53 in cancer",
                "journal": "Nature",
                "pub_year": 2021,
            },
        ),
        _Row(
            source="PMID:99999",
            source_link="https://pubmed.ncbi.nlm.nih.gov/99999/",
            evidence_type="article",
            extra={**_drop(), "title": "Unrelated paper", "journal": "Cell", "pub_year": 2010},
        ),
        _Row(
            source="US10234567B2",
            source_link="https://patents.google.com/patent/US10234567B2/en",
            evidence_type="patent",
            extra={
                **_keep(),
                "title": "Method of treating",
                "assignee": "Acme Bio",
                "filing_date": "2018-05-01",
            },
        ),
        _Row(
            source="US0000000B2",
            source_link="",  # no link → renderer must construct a Google Patents URL
            evidence_type="patent",
            extra={**_keep(), "title": "No-link patent"},
        ),
        _Row(
            source="NCT04567890",
            source_link="https://clinicaltrials.gov/study/NCT04567890",
            evidence_type="clinical_trial",
            extra={
                **_keep(),
                "title": "Phase II study",
                "phase": "Phase 2",
                "status": "Recruiting",
                "sponsor": "NIH",
            },
        ),
        _Row(
            source="opentargets:ENSG00000141510:EFO_0000305",
            source_link="https://platform.opentargets.org/target/ENSG00000141510",
            evidence_type="genetics",
            extra={
                **_keep(),
                "overall_score": 0.82,
                "genetic_score": 0.7,
                "assoc_source_link": "https://platform.opentargets.org/evidence/ENSG00000141510/EFO_0000305",
                "tract_source_link": "https://platform.opentargets.org/target/ENSG00000141510?tab=tractability",
            },
        ),
        _Row(
            source="gnomad:ENSG00000141510",
            source_link="https://gnomad.broadinstitute.org/gene/ENSG00000141510",
            evidence_type="constraint",
            extra={**_keep(), "summary": "high LoF intolerance"},
        ),
    ]


def _render() -> str:
    return render_full_report(
        target_gene="TP53",
        disease="breast cancer",
        disease_id="EFO_0000305",
        gene_id="ENSG00000141510",
        evidence_rows=_rows(),
        generated_at=datetime(2026, 6, 15, tzinfo=UTC),
    )


def test_includes_external_links_per_type() -> None:
    md = _render()
    assert "https://pubmed.ncbi.nlm.nih.gov/12345/" in md
    assert "https://patents.google.com/patent/US10234567B2/en" in md
    assert "https://clinicaltrials.gov/study/NCT04567890" in md
    assert "https://platform.opentargets.org/evidence/ENSG00000141510/EFO_0000305" in md


def test_patent_link_fallback_constructed_when_missing() -> None:
    md = _render()
    # The patent with an empty source_link gets a constructed Google Patents URL.
    assert "https://patents.google.com/patent/US0000000B2" in md


def test_dropped_evidence_excluded() -> None:
    md = _render()
    assert "PMID:99999" not in md
    assert "Unrelated paper" not in md
    # Kept count reflects the seven rows minus the single dropped one.
    assert "**Kept evidence:** 6 sources" in md


def test_type_detail_and_service_links_rendered() -> None:
    md = _render()
    # Per-type detail columns surface.
    assert "Nature" in md  # journal
    assert "Acme Bio" in md  # patent assignee
    assert "Recruiting" in md  # trial status
    assert "0.82" in md  # OpenTargets overall score
    # Service Reports block surfaces the platform/service URLs.
    assert "Service Reports & External Resources" in md
    assert "https://gnomad.broadinstitute.org/gene/ENSG00000141510" in md
    # Cross-link back to the executive report.
    assert "[report.md](./report.md)" in md


def _rows_with_regulatory_and_druggability() -> list[_Row]:
    return _rows() + [
        _Row(
            source="fda:label:TRPC6",
            source_link="https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=0",
            evidence_type="regulatory",
            extra={
                **_keep("FDA label references gene"),
                "summary": "No approved drug modulating TRPC6.",
            },
        ),
        _Row(
            source="uniprot:Q9Y210",
            source_link="https://www.uniprot.org/uniprot/Q9Y210",
            evidence_type="druggability",
            extra={
                **_keep("known binding pocket"),
                "summary": "TRP channel — ion-channel drug class.",
            },
        ),
    ]


def _render_with_extra() -> str:
    return render_full_report(
        target_gene="TP53",
        disease="breast cancer",
        disease_id="EFO_0000305",
        gene_id="ENSG00000141510",
        evidence_rows=_rows_with_regulatory_and_druggability(),
        generated_at=datetime(2026, 6, 15, tzinfo=UTC),
    )


def test_regulatory_section_present() -> None:
    md = _render_with_extra()
    assert "## Regulatory" in md
    # Source rows render via the shared evidence_label() helper (friendly name,
    # not the raw source string) — same convention as report.md's tables.
    assert "FDA label" in md
    assert "No approved drug modulating TRPC6." in md


def test_druggability_section_present() -> None:
    md = _render_with_extra()
    assert "## Druggability" in md
    assert "UniProt" in md
    assert "TRP channel — ion-channel drug class." in md


def test_regulatory_and_druggability_kept_count_correct() -> None:
    md = _render_with_extra()
    # 6 from base _rows() + 2 new = 8 kept (1 dropped remains)
    assert "**Kept evidence:** 8 sources" in md
