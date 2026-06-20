# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ReportAgent — dossier format (lens verdicts + agreement map)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.synthesis.report.agent import ReportAgent, _kept_evidence_section, render_report
from agents.synthesis.report.citations import cite, evidence_label
from tests.agents.conftest import make_task_msg

# ---------------------------------------------------------------------------
# Helpers shared across citation / grouped-kept tests
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    source: str
    source_link: str
    evidence_type: str
    extra: dict[str, Any] = field(default_factory=dict)
    gene: str = ""


def _keep() -> dict:
    return {"screening_verdict": {"verdict": "keep", "rationale": "relevant"}}


# ---------------------------------------------------------------------------
# evidence_label short-label tests
# ---------------------------------------------------------------------------


def test_label_gtex_hpa() -> None:
    row = _Row(source="gtex_hpa:ENSG00000137672", source_link="", evidence_type="expression")
    assert evidence_label(row) == "GTEx/HPA"


def test_label_hpa() -> None:
    row = _Row(source="hpa:ENSG00000137672", source_link="", evidence_type="expression")
    assert evidence_label(row) == "GTEx/HPA"


def test_label_gtex_v8_with_tissue() -> None:
    row = _Row(source="gtex_v8:Brain_Cortex", source_link="", evidence_type="expression")
    assert evidence_label(row) == "GTEx · Brain_Cortex"


def test_label_gnomad() -> None:
    row = _Row(source="gnomad:ENSG00000137672", source_link="", evidence_type="constraint")
    assert evidence_label(row) == "gnomAD"


def test_label_depmap() -> None:
    row = _Row(source="depmap:TRPC6", source_link="", evidence_type="functional_genomics")
    assert evidence_label(row) == "DepMap"


def test_label_uniprot() -> None:
    row = _Row(source="uniprot:Q9Y210", source_link="", evidence_type="druggability")
    assert evidence_label(row) == "UniProt"


def test_label_fda_label() -> None:
    row = _Row(source="fda:label:TRPC6", source_link="", evidence_type="regulatory")
    assert evidence_label(row) == "FDA label"


def test_label_fda_faers() -> None:
    row = _Row(source="fda:faers:TRPC6", source_link="", evidence_type="regulatory")
    assert evidence_label(row) == "FAERS"


def test_label_pmid_unchanged() -> None:
    row = _Row(
        source="PMID:12345678",
        source_link="https://pubmed.ncbi.nlm.nih.gov/12345678/",
        evidence_type="article",
    )
    assert evidence_label(row) == "PMID:12345678"


def test_label_nct_unchanged() -> None:
    row = _Row(
        source="NCT04567890",
        source_link="https://clinicaltrials.gov/study/NCT04567890",
        evidence_type="clinical_trial",
    )
    assert evidence_label(row) == "NCT04567890"


def test_cite_produces_markdown_link() -> None:
    row = _Row(
        source="gnomad:ENSG00000137672",
        source_link="https://gnomad.broadinstitute.org/gene/ENSG00000137672",
        evidence_type="constraint",
    )
    result = cite(row)
    assert result == "[gnomAD](https://gnomad.broadinstitute.org/gene/ENSG00000137672)"


# ---------------------------------------------------------------------------
# _kept_evidence_section grouped rendering tests
# ---------------------------------------------------------------------------


def test_kept_section_groups_by_type() -> None:
    rows = [
        _Row(
            "PMID:1", "https://pubmed.ncbi.nlm.nih.gov/1/", "article", {**_keep(), "pub_year": 2021}
        ),
        _Row(
            "PMID:2", "https://pubmed.ncbi.nlm.nih.gov/2/", "article", {**_keep(), "pub_year": 2020}
        ),
        _Row("gnomad:ENSG1", "https://gnomad.broadinstitute.org/gene/ENSG1", "constraint", _keep()),
        _Row("fda:label:GENE1", "https://fda.gov", "regulatory", _keep()),
    ]
    md = _kept_evidence_section(rows)
    assert "### Literature" in md
    assert "### Empirical" in md
    assert "#### Constraint" in md
    assert "#### Regulatory" in md


def test_kept_section_uses_short_labels() -> None:
    rows = [
        _Row(
            "gtex_hpa:ENSG00000137672",
            "https://gtexportal.org/home/gene/ENSG00000137672",
            "expression",
            _keep(),
        ),
        _Row(
            "gnomad:ENSG00000137672",
            "https://gnomad.broadinstitute.org/gene/ENSG00000137672",
            "constraint",
            _keep(),
        ),
    ]
    md = _kept_evidence_section(rows)
    assert "[GTEx/HPA](" in md
    assert "[gnomAD](" in md
    assert "gtex_hpa:ENSG00000137672" not in md


def test_kept_section_caps_literature_at_lit_cap() -> None:
    from agents.synthesis.report.agent import _LIT_CAP

    rows = [
        _Row(
            f"PMID:{i}",
            f"https://pubmed.ncbi.nlm.nih.gov/{i}/",
            "article",
            {**_keep(), "pub_year": 2020 + i},
        )
        for i in range(_LIT_CAP + 5)
    ]
    md = _kept_evidence_section(rows)
    assert "full_report.md" in md
    assert "and 5 more" in md


def test_kept_section_shows_all_structured_rows() -> None:
    rows = [
        _Row("uniprot:Q9Y210", "https://www.uniprot.org/uniprot/Q9Y210", "druggability", _keep()),
        _Row("fda:label:X", "https://fda.gov", "regulatory", _keep()),
        _Row("depmap:GENE", "https://depmap.org", "functional_genomics", _keep()),
    ]
    md = _kept_evidence_section(rows)
    assert "### Empirical" in md
    assert "#### Druggability" in md
    assert "#### Regulatory" in md
    assert "#### Functional Genomics" in md


def test_kept_section_empty_returns_message() -> None:
    md = _kept_evidence_section([])
    assert "full_report.md" in md


# ---------------------------------------------------------------------------
# render_report passes kept_db_rows through
# ---------------------------------------------------------------------------


def test_render_report_with_kept_db_rows_shows_grouped_section() -> None:
    rows = [
        _Row(
            "PMID:99",
            "https://pubmed.ncbi.nlm.nih.gov/99/",
            "article",
            {**_keep(), "pub_year": 2023},
        ),
        _Row("gnomad:ENSG1", "https://gnomad.broadinstitute.org/gene/ENSG1", "constraint", _keep()),
    ]
    evidence_summary = [
        {"source": "PMID:99", "evidence_type": "article", "verdict": "keep"},
        {"source": "gnomad:ENSG1", "evidence_type": "constraint", "verdict": "keep"},
    ]
    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=None,
        experiment_results=[],
        critiques=[],
        review_gaps=[],
        evidence_summary=evidence_summary,
        generated_at=datetime(2026, 6, 16, tzinfo=UTC),
        kept_db_rows=rows,
    )
    assert "### Literature" in content
    assert "### Constraint" in content
    assert "[gnomAD](" in content


_LENS_VERDICTS = [
    {
        "schema_version": "1.0",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "trace_id": "trace-test",
        "lens": "genetics",
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "overall_verdict": "support",
        "confidence": 0.9,
        "axes": [
            {
                "axis": "causality",
                "verdict": True,
                "confidence": 0.9,
                "rationale": "GWAS.",
                "supporting_claim_ids": [],
            }
        ],
        "rationale": "Strong genetic causality evidence.",
    },
    {
        "schema_version": "1.0",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "trace_id": "trace-test",
        "lens": "biology",
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "overall_verdict": "support",
        "confidence": 0.75,
        "axes": [
            {
                "axis": "druggability",
                "verdict": True,
                "confidence": 0.75,
                "rationale": "Binding pocket.",
                "supporting_claim_ids": [],
            }
        ],
        "rationale": "Good druggability profile.",
    },
    {
        "schema_version": "1.0",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "trace_id": "trace-test",
        "lens": "safety",
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "overall_verdict": "oppose",
        "confidence": 0.6,
        "axes": [
            {
                "axis": "toxicity",
                "verdict": False,
                "confidence": 0.6,
                "rationale": "Cardiac expression.",
                "supporting_claim_ids": [],
            }
        ],
        "rationale": "Potential cardiac toxicity concern.",
    },
]

_AGREEMENT_MAP = {
    "schema_version": "1.0",
    "run_id": "00000000-0000-0000-0000-000000000001",
    "verdicts_by_lens": {"genetics": "support", "biology": "support", "safety": "oppose"},
    "consensus_verdict": "support",
    "consensus_confidence": 0.825,
    "agreeing_lenses": ["genetics", "biology"],
    "dissenting_lenses": ["safety"],
    "conflicts": [
        {
            "lens_a": "genetics",
            "lens_b": "safety",
            "description": "genetics supports while safety opposes",
        },
        {
            "lens_a": "biology",
            "lens_b": "safety",
            "description": "biology supports while safety opposes",
        },
    ],
    "shared_claim_conflicts": [],
}

_EXPERIMENT_RESULTS = [
    {
        "target": "BRCA1",
        "score": 78,
        "rationale": "Strong causality and tractability.",
        "supporting_evidence_ids": [],
    },
]

_REVIEW_GAPS = [
    {"stage": "literature", "missing_aspects": ["No RCT data."], "completeness_score": 60},
]


# ---------------------------------------------------------------------------
# render_report unit tests
# ---------------------------------------------------------------------------


def test_render_report_contains_gene_name():
    from datetime import datetime

    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=_LENS_VERDICTS,
        agreement_map=_AGREEMENT_MAP,
        experiment_results=_EXPERIMENT_RESULTS,
        critiques=[],
        review_gaps=_REVIEW_GAPS,
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "BRCA1" in content
    assert "breast cancer" in content


def test_render_report_includes_all_sections():
    from datetime import datetime

    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=_LENS_VERDICTS,
        agreement_map=_AGREEMENT_MAP,
        experiment_results=_EXPERIMENT_RESULTS,
        critiques=[],
        review_gaps=_REVIEW_GAPS,
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    for section in [
        "Executive Summary",
        "Evidence Summary",
        "Discovery",
        "Agreement",
        "Suitability",
        "Gap Analysis",
        "Recommendations",
    ]:
        assert section in content, f"Missing section: {section}"


def test_render_report_shows_lens_names():
    from datetime import datetime

    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=_LENS_VERDICTS,
        agreement_map=None,
        experiment_results=[],
        critiques=[],
        review_gaps=[],
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "Genetics" in content
    assert "Biology" in content
    assert "Safety" in content


def test_render_report_shows_conflicts():
    from datetime import datetime

    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=_AGREEMENT_MAP,
        experiment_results=[],
        critiques=[],
        review_gaps=[],
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "safety" in content.lower()


def test_render_report_support_consensus_recommends_proceed():
    from datetime import datetime

    am = {**_AGREEMENT_MAP, "consensus_verdict": "support"}
    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=am,
        experiment_results=[],
        critiques=[],
        review_gaps=[],
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "PROCEED" in content


def test_render_report_oppose_consensus_recommends_deprioritise():
    from datetime import datetime

    am = {**_AGREEMENT_MAP, "consensus_verdict": "oppose"}
    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=am,
        experiment_results=[],
        critiques=[],
        review_gaps=[],
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "DEPRIORITISE" in content


def test_render_report_high_score_fallback_recommends_proceed():
    from datetime import datetime

    high_score_results = [
        {"target": "BRCA1", "score": 85, "rationale": "excellent", "supporting_evidence_ids": []}
    ]
    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=None,
        experiment_results=high_score_results,
        critiques=[],
        review_gaps=[],
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "PROCEED" in content


def test_render_report_low_score_recommends_deprioritise():
    from datetime import datetime

    low_score_results = [
        {"target": "BRCA1", "score": 20, "rationale": "weak", "supporting_evidence_ids": []}
    ]
    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=None,
        experiment_results=low_score_results,
        critiques=[],
        review_gaps=[],
        evidence_summary=[],
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert "DEPRIORITISE" in content


def test_render_report_evidence_table_truncated_at_100():
    from datetime import datetime

    evidence_summary = [
        {"source": f"PMID:{i}", "evidence_type": "article", "verdict": "keep"} for i in range(200)
    ]
    content = render_report(
        target_gene="BRCA1",
        disease="breast cancer",
        lens_verdicts=[],
        agreement_map=None,
        experiment_results=[],
        critiques=[],
        review_gaps=[],
        evidence_summary=evidence_summary,
        generated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert content.count("PMID:") <= 100


# ---------------------------------------------------------------------------
# Agent integration tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def report_ctx(run_id, trace_id):
    router = MagicMock()
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router)


async def test_report_agent_writes_file_and_returns_artifact_uri(
    run_id, trace_id, report_ctx, tmp_path
):
    msg = make_task_msg(
        "report",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload={
            "lens_verdicts": _LENS_VERDICTS,
            "agreement_map": _AGREEMENT_MAP,
            "experiment_results": _EXPERIMENT_RESULTS,
            "critiques": [],
            "review_gaps": _REVIEW_GAPS,
            "evidence_summary": [],
        },
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    with (
        patch("agents.synthesis.report.agent._REPORT_ROOT", tmp_path),
        patch("agents.synthesis.report.agent.get_session", return_value=mock_session),
    ):
        result = await ReportAgent().run(msg, report_ctx)

    assert result.intent == "result"
    artifact_uri = result.payload["artifact_uri"]
    assert artifact_uri.startswith("file://")

    report_file_path = artifact_uri.replace("file://", "")
    content = Path(report_file_path).read_text()
    assert "BRCA1" in content
    assert "breast cancer" in content


async def test_report_agent_persists_to_db(run_id, trace_id, report_ctx, tmp_path):
    msg = make_task_msg(
        "report",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload={},
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    added_rows = []
    mock_session.add = lambda row: added_rows.append(row)

    with (
        patch("agents.synthesis.report.agent._REPORT_ROOT", tmp_path),
        patch("agents.synthesis.report.agent.get_session", return_value=mock_session),
    ):
        await ReportAgent().run(msg, report_ctx)

    assert len(added_rows) == 1
    from core.persistence.models import Report

    assert isinstance(added_rows[0], Report)
    assert added_rows[0].run_id == run_id


async def test_report_agent_handles_empty_payload(run_id, trace_id, report_ctx, tmp_path):
    msg = make_task_msg(
        "report",
        {"target_gene": "TP53", "disease": "lung cancer"},
        run_id,
        trace_id,
        payload=None,
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    with (
        patch("agents.synthesis.report.agent._REPORT_ROOT", tmp_path),
        patch("agents.synthesis.report.agent.get_session", return_value=mock_session),
    ):
        result = await ReportAgent().run(msg, report_ctx)

    assert result.intent == "result"
    assert result.payload["artifact_uri"].startswith("file://")
