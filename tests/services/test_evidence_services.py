# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/evidence — entity_resolution, clustering, quality/sufficiency,
and claim_extraction. graph_builder tests live in tests/services/knowledge_graph/."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness.context import RunContext
from schemas.evidence import (
    CoreClaim,
    DataClass,
    Direction,
    Evidence,
    EvidenceType,
    Provenance,
)
from services.evidence.claim_clustering import cluster_claims
from services.evidence.claim_extraction import (
    _build_core_claim,
    _parse_extraction,
    _structured_text,
    _trial_confidence,
    extract_claims,
    structured_claims,
)
from services.evidence.entity_resolution import resolve_entities
from services.evidence.quality_scorer import score_quality, score_quality_batch
from services.evidence.sufficiency_scorer import SufficiencyReport, score_sufficiency
from tests.conftest import *  # noqa: F401,F403 (pytest fixtures)

_RUN_ID = uuid.uuid4()
_TRACE = "svc-test"


# ── helpers ──────────────────────────────────────────────────────────────────


def _prov() -> Provenance:
    from datetime import datetime

    return Provenance(
        agent_name="test",
        tool_name="t",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id=_TRACE,
    )


def _claim(
    *,
    gene: str = "BRCA1",
    et: EvidenceType = EvidenceType.ARTICLE,
    direction: Direction = Direction.UNSPECIFIED,
    confidence: float | None = None,
    avail: date | None = None,
) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene=gene,
        gene_id="ENSG000",
        disease="breast cancer",
        disease_id="EFO_0000305",
        evidence_type=et,
        claim_text="Test claim.",
        direction=direction,
        confidence=confidence,
        availability_date=avail,
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
    )


def _evidence(source: str = "PMID:1", et: EvidenceType = EvidenceType.ARTICLE) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="BRCA1",
        gene_id="ENSG000",
        disease="breast cancer",
        disease_id="EFO_0000305",
        evidence_type=et,
        scope="abstract",
        source=source,
        source_link="https://pubmed.ncbi.nlm.nih.gov/1/",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
        extra={"title": "Test", "abstract": "BRCA1 inhibition reduces cancer growth."},
    )


# ── entity_resolution ────────────────────────────────────────────────────────


def test_resolve_entities_fills_missing_ids():
    claim = _claim()
    resolved = resolve_entities(
        [claim],
        canonical_gene_id="ENSG_CANON",
        canonical_disease_id="EFO_CANON",
    )
    # gene_id was already "ENSG000" (non-empty) — should not be overwritten
    assert resolved[0].gene_id == "ENSG000"


def test_resolve_entities_fills_empty_gene_id():
    claim = CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="BRCA1",
        gene_id="",  # empty
        disease="breast cancer",
        disease_id="",
        evidence_type=EvidenceType.ARTICLE,
        claim_text="Test.",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
    )
    resolved = resolve_entities(
        [claim],
        canonical_gene_id="ENSG_FILL",
        canonical_disease_id="EFO_FILL",
    )
    assert resolved[0].gene_id == "ENSG_FILL"
    assert resolved[0].disease_id == "EFO_FILL"


def test_resolve_entities_passthrough_when_no_canonical():
    claim = _claim()
    original_id = claim.gene_id
    resolved = resolve_entities([claim])
    assert resolved[0].gene_id == original_id


# ── claim_clustering ─────────────────────────────────────────────────────────


def test_cluster_claims_groups_by_gene_disease_type():
    claims = [
        _claim(et=EvidenceType.ARTICLE),
        _claim(et=EvidenceType.ARTICLE),
        _claim(et=EvidenceType.PATENT),
    ]
    clusters = cluster_claims(claims)
    assert len(clusters) == 2  # ARTICLE + PATENT
    article_key = ("ENSG000", "EFO_0000305", "article")
    patent_key = ("ENSG000", "EFO_0000305", "patent")
    assert article_key in clusters
    assert patent_key in clusters
    assert len(clusters[article_key]) == 2
    assert len(clusters[patent_key]) == 1


def test_cluster_claims_empty():
    assert cluster_claims([]) == {}


# ── quality_scorer ────────────────────────────────────────────────────────────


def test_score_quality_sets_confidence():
    claim = _claim(confidence=None, avail=date(2025, 1, 1))
    scored = score_quality(claim)
    assert scored.confidence is not None
    assert 0.0 <= scored.confidence <= 1.0


def test_score_quality_blends_extractor_confidence():
    claim = _claim(confidence=0.9, avail=date(2025, 1, 1))
    scored = score_quality(claim)
    # blended = (heuristic + 0.9) / 2 — must be between heuristic and 0.9
    assert scored.confidence is not None
    assert 0.0 < scored.confidence <= 1.0


def test_score_quality_old_evidence_penalised():
    recent = _claim(confidence=None, avail=date(2025, 1, 1))
    old = _claim(confidence=None, avail=date(2010, 1, 1))
    assert score_quality(recent).confidence > score_quality(old).confidence


def test_score_quality_batch_preserves_count():
    claims = [_claim() for _ in range(5)]
    scored = score_quality_batch(claims)
    assert len(scored) == 5
    assert all(c.confidence is not None for c in scored)


# ── sufficiency_scorer ───────────────────────────────────────────────────────


def test_score_sufficiency_reports_sufficient_when_threshold_met():
    claims = [_claim(et=EvidenceType.ARTICLE) for _ in range(3)]
    report = score_sufficiency(claims)
    assert isinstance(report, SufficiencyReport)
    assert report.category_counts.get("article", 0) == 3
    assert "article" in report.sufficient_categories


def test_score_sufficiency_empty_claims():
    report = score_sufficiency([])
    assert report.category_counts == {}


def test_score_sufficiency_excludes_low_confidence():
    claims = [_claim(et=EvidenceType.GENETICS, confidence=0.1)]  # below threshold 0.4
    report = score_sufficiency(claims)
    # low confidence claim is excluded → genetics category is insufficient
    assert "genetics" in report.insufficient_categories


# ── claim_extraction (unit helpers) ──────────────────────────────────────────


def test_parse_extraction_valid_json():
    raw = json.dumps(
        [
            {
                "evidence_id": "abc",
                "claims": [
                    {
                        "claim_text": "BRCA1 inhibition reduces tumour growth.",
                        "direction": "inhibit",
                        "confidence": 0.9,
                    }
                ],
            }
        ]
    )
    result = _parse_extraction(raw, [])
    assert result[0]["evidence_id"] == "abc"
    assert result[0]["claims"][0]["direction"] == "inhibit"


def test_parse_extraction_bad_json_fallback(run_id, trace_id):
    ev = _evidence()
    result = _parse_extraction("not valid json", [ev])
    assert len(result) == 1
    assert result[0]["claims"][0]["claim_text"] == ""


def test_build_core_claim_creates_valid_claim():
    ev = _evidence()
    claim_dict = {
        "claim_text": "BRCA1 loss increases risk.",
        "direction": "unspecified",
        "confidence": 0.85,
    }
    claim = _build_core_claim(claim_dict, ev, _RUN_ID, _TRACE)
    assert claim is not None
    assert claim.claim_text == "BRCA1 loss increases risk."
    assert claim.confidence == pytest.approx(0.85)
    assert claim.direction == Direction.UNSPECIFIED
    # The claim keeps a provenance link back to the document it was extracted from,
    # so reports can resolve it to the original source.
    assert claim.source_evidence_id == ev.evidence_id


def test_build_core_claim_empty_text_returns_none():
    ev = _evidence()
    claim = _build_core_claim(
        {"claim_text": "   ", "direction": "inhibit", "confidence": 0.9}, ev, _RUN_ID, _TRACE
    )
    assert claim is None


def test_build_core_claim_propagates_availability_date():
    ev = _evidence()
    ev = ev.model_copy(update={"availability_date": date(2024, 6, 1)})
    claim = _build_core_claim(
        {"claim_text": "Claim.", "direction": "inhibit", "confidence": 0.8}, ev, _RUN_ID, _TRACE
    )
    assert claim is not None
    assert claim.availability_date == date(2024, 6, 1)


def test_build_core_claim_tags_literature_topics():
    from schemas.evidence import LensTopic

    ev = _evidence(et=EvidenceType.ARTICLE)
    claim = _build_core_claim(
        {
            "claim_text": "Knockout is embryonic-lethal.",
            "direction": "unspecified",
            "confidence": 0.7,
            "topics": ["biology", "safety", "biology"],  # dup deliberately
        },
        ev,
        _RUN_ID,
        _TRACE,
    )
    assert claim is not None
    assert claim.topics == [LensTopic.BIOLOGY, LensTopic.SAFETY]  # deduped, validated


def test_build_core_claim_unknown_topics_fall_back_to_biology():
    from schemas.evidence import LensTopic

    ev = _evidence(et=EvidenceType.ARTICLE)
    claim = _build_core_claim(
        {"claim_text": "X.", "confidence": 0.7, "topics": ["commercial", "bogus"]},
        ev,
        _RUN_ID,
        _TRACE,
    )
    assert claim is not None
    # unknown/non-literature tags dropped → literature claim defaults to biology
    assert claim.topics == [LensTopic.BIOLOGY]


def test_build_core_claim_untagged_literature_defaults_to_biology():
    from schemas.evidence import LensTopic

    ev = _evidence(et=EvidenceType.ARTICLE)
    claim = _build_core_claim(
        {"claim_text": "Generic mechanism statement.", "confidence": 0.6},
        ev,
        _RUN_ID,
        _TRACE,
    )
    assert claim is not None
    assert claim.topics == [LensTopic.BIOLOGY]


def test_build_core_claim_ignores_topics_on_structured_type():
    """Topics are literature-only; a structured type never carries them even if the
    LLM hallucinates a tag."""
    ev = _evidence(et=EvidenceType.GENETICS)
    claim = _build_core_claim(
        {"claim_text": "GWAS hit.", "confidence": 0.9, "topics": ["genetics"]},
        ev,
        _RUN_ID,
        _TRACE,
    )
    assert claim is not None
    assert claim.topics == []


# ── claim_extraction (integration: mocked LLM) ───────────────────────────────


def _make_ctx() -> RunContext:
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    return RunContext(run_id=_RUN_ID, trace_id=_TRACE, router=router), provider


async def test_extract_claims_empty_evidence_returns_empty():
    ctx, _ = _make_ctx()
    result = await extract_claims([], "BRCA1", "breast cancer", "unspecified", ctx)
    assert result == []


async def test_extract_claims_calls_llm_and_parses_output():
    from core.routing.providers.base import CompletionResult

    ev = _evidence()
    ev_id = str(ev.evidence_id)
    llm_output = json.dumps(
        [
            {
                "evidence_id": ev_id,
                "claims": [
                    {
                        "claim_text": "BRCA1 inhibition reduces tumour growth.",
                        "direction": "inhibit",
                        "confidence": 0.88,
                    },
                ],
            }
        ]
    )

    ctx, provider = _make_ctx()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content=llm_output,
            model_used="test",
            input_tokens=10,
            output_tokens=20,
            latency_ms=50.0,
        )
    )
    ctx.load_skill = MagicMock(return_value="skill text")

    result = await extract_claims([ev], "BRCA1", "breast cancer", "inhibit", ctx)

    assert len(result) == 1
    assert result[0].claim_text == "BRCA1 inhibition reduces tumour growth."
    assert result[0].direction == Direction.INHIBIT
    assert result[0].confidence == pytest.approx(0.88)


async def test_extract_claims_skips_empty_claim_text():
    from core.routing.providers.base import CompletionResult

    ev = _evidence()
    ev_id = str(ev.evidence_id)
    llm_output = json.dumps(
        [
            {
                "evidence_id": ev_id,
                "claims": [
                    {"claim_text": "", "direction": "inhibit", "confidence": 0.9},
                ],
            }
        ]
    )

    ctx, provider = _make_ctx()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content=llm_output,
            model_used="test",
            input_tokens=10,
            output_tokens=5,
            latency_ms=30.0,
        )
    )
    ctx.load_skill = MagicMock(return_value="skill")

    result = await extract_claims([ev], "BRCA1", "breast cancer", "inhibit", ctx)
    assert result == []


# ── _structured_text: breadth-summary and GoF coverage ───────────────────────


def test_structured_text_genetics_breadth_summary_returns_summary():
    """GWAS/coloc breadth-summary Evidence records must produce non-empty text."""
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="TRPC6",
        gene_id="ENSG00000144935",
        disease="focal segmental glomerulosclerosis",
        disease_id="EFO_0004236",
        evidence_type=EvidenceType.GENETICS,
        scope="abstract",
        source="gwas_catalog:locus_breadth_summary",
        source_link="https://www.ebi.ac.uk/gwas/genes/TRPC6",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
        extra={
            "summary": "TRPC6 locus: 12 distinct GWAS traits found (0 matched FSGS).",
            "all_traits": ["blood pressure", "kidney function"],
            "kept_traits": [],
            "dropped_off_target": 12,
            "is_oncology": False,
        },
    )
    text = _structured_text(ev)
    assert text == "TRPC6 locus: 12 distinct GWAS traits found (0 matched FSGS)."


def test_structured_text_genetics_gwas_row_returns_hit_text():
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="TRPC6",
        gene_id="ENSG00000144935",
        disease="focal segmental glomerulosclerosis",
        disease_id="EFO_0004236",
        evidence_type=EvidenceType.GENETICS,
        scope="abstract",
        source="GCST001",
        source_link="internal://gwas/GCST001",
        provenance=_prov(),
        classification=DataClass.SENSITIVE,
        extra={
            "pvalue": 1e-10,
            "beta": 0.3,
            "odds_ratio": None,
            "study_id": "GCST001",
            "lof_score": 0.5,
            "is_lof_intolerant": False,
        },
    )
    text = _structured_text(ev)
    assert "GWAS hit" in text
    assert "TRPC6" in text


def test_structured_text_genetics_open_targets_row():
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="TRPC6",
        gene_id="ENSG00000144935",
        disease="focal segmental glomerulosclerosis",
        disease_id="EFO_0004236",
        evidence_type=EvidenceType.GENETICS,
        scope="abstract",
        source="opentargets",
        source_link="https://platform.opentargets.org",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
        extra={
            "overall_score": 0.956,
            "genetic_score": 0.9,
            "known_drugs_score": 0.4,
            "tractability_small_molecule": 0.7,
            "tractability_antibody": 0.3,
        },
    )
    text = _structured_text(ev)
    assert "Open Targets" in text
    assert "overall_score=0.956" in text


# ── structured_claims (deterministic claim generation) ───────────────────────


def _genetics_ev(extra: dict, et: EvidenceType = EvidenceType.GENETICS) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="TRPC6",
        gene_id="ENSG00000144935",
        disease="focal segmental glomerulosclerosis",
        disease_id="EFO_0004236",
        evidence_type=et,
        scope="abstract",
        source="test_source",
        source_link="https://example.com",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
        extra=extra,
    )


def test_structured_claims_ot_assoc_strong_yields_claim():
    """OT association with genetic_score >= 0.7 → strong claim with high confidence."""
    ev = _genetics_ev({"genetic_score": 0.956, "overall_score": 0.85})
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "0.956" in claims[0].claim_text
    assert "strong" in claims[0].claim_text
    assert claims[0].confidence == pytest.approx(0.956)
    assert claims[0].source_evidence_id == ev.evidence_id
    assert claims[0].provenance.tool_name == "structured_extraction"


def test_structured_claims_ot_assoc_moderate_yields_claim():
    """OT association with 0.5 <= genetic_score < 0.7 → moderate claim."""
    ev = _genetics_ev({"genetic_score": 0.62, "overall_score": 0.5})
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "moderate" in claims[0].claim_text
    assert claims[0].confidence == pytest.approx(0.62)


def test_structured_claims_ot_assoc_below_threshold_no_claim():
    """OT association with genetic_score < 0.5 → no claim generated."""
    ev = _genetics_ev({"genetic_score": 0.3, "overall_score": 0.2})
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert claims == []


def test_structured_claims_spoke_gwas_pvalue_yields_high_confidence_claim():
    """SPOKE association with a genome-wide-significant gwas_pvalue → confidence 0.9."""
    ev = _genetics_ev(
        {
            "disease_name": "breast cancer",
            "disease_identifier": "DOID:1612",
            "edge_sources": ["GWAS"],
            "gwas_pvalue": 8e-09,
            "diseases_score": None,
        }
    )
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "SPOKE" in claims[0].claim_text
    assert "breast cancer" in claims[0].claim_text
    assert claims[0].confidence == pytest.approx(0.9)
    assert claims[0].source_evidence_id == ev.evidence_id
    assert claims[0].provenance.tool_name == "structured_extraction"


def test_structured_claims_spoke_diseases_score_yields_scaled_confidence():
    """SPOKE association with only a DISEASES textmining score → confidence = score / 10, clamped."""
    ev = _genetics_ev(
        {
            "disease_name": "type 2 diabetes mellitus",
            "disease_identifier": "DOID:9352",
            "edge_sources": ["DISEASES"],
            "gwas_pvalue": None,
            "diseases_score": 6.291,
        }
    )
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "SPOKE" in claims[0].claim_text
    assert "type 2 diabetes mellitus" in claims[0].claim_text
    assert claims[0].confidence == pytest.approx(0.6291)


def test_structured_claims_spoke_below_significance_yields_low_confidence():
    """A sub-genome-wide-significant gwas_pvalue still yields a claim, at lower confidence."""
    ev = _genetics_ev(
        {
            "disease_name": "obesity",
            "disease_identifier": "DOID:9351",
            "edge_sources": ["GWAS"],
            "gwas_pvalue": 1e-4,
            "diseases_score": None,
        }
    )
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert claims[0].confidence == pytest.approx(0.6)


def test_structured_claims_breadth_summary_yields_claim():
    """GWAS/coloc breadth-summary row → claim from summary text."""
    ev = _genetics_ev({"summary": "TRPC6 locus: 0 matched FSGS."})
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "TRPC6 locus" in claims[0].claim_text


def test_structured_claims_gnomad_constraint_yields_claim():
    """gnomAD constraint record (has loeuf/pli) → deterministic constraint claim."""
    ev = _genetics_ev({"loeuf": 0.28, "pli": 0.99, "mis_z": 2.1}, et=EvidenceType.CONSTRAINT)
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "LOEUF=0.280" in claims[0].claim_text
    assert "pLI=0.990" in claims[0].claim_text
    assert claims[0].confidence == pytest.approx(0.9)


def test_structured_claims_gnomad_tolerant_lower_confidence():
    """LoF-tolerant gene (LOEUF > 0.8) → claim with lower confidence."""
    ev = _genetics_ev({"loeuf": 1.2, "pli": 0.01}, et=EvidenceType.CONSTRAINT)
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert claims[0].confidence == pytest.approx(0.75)


def test_structured_claims_clinvar_plp_yields_claim():
    """ClinVar bundle with ≥1 P/LP variant → deterministic claim."""
    ev = _genetics_ev(
        {
            "pathogenic": [
                {"hgvsp": "p.Arg895Cys", "gold_stars": 2, "major_consequence": "missense"},
                {"hgvsp": "p.Glu897Lys", "gold_stars": 1, "major_consequence": "missense"},
            ],
            "likely_pathogenic": [
                {"hgvsp": "p.Asn143Ser", "major_consequence": "missense"},
            ],
            "text": "gnomAD ClinVar: 3 P/LP variants.",
        },
        et=EvidenceType.CONSTRAINT,
    )
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert "3 Pathogenic/Likely-Pathogenic" in claims[0].claim_text
    assert claims[0].confidence == pytest.approx(0.85)


def test_structured_claims_clinvar_no_plp_no_claim():
    """ClinVar bundle with 0 P/LP variants → no claim."""
    ev = _genetics_ev(
        {"pathogenic": [], "likely_pathogenic": [], "text": "No P/LP variants."},
        et=EvidenceType.CONSTRAINT,
    )
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert claims == []


async def test_extract_claims_structured_always_present_even_with_garbage_llm():
    """Structured types must yield claims even when the LLM returns garbage."""
    from core.routing.providers.base import CompletionResult

    ev_ot = _genetics_ev({"genetic_score": 0.956, "overall_score": 0.85})
    ev_gnomad = _genetics_ev({"loeuf": 0.28, "pli": 0.99}, et=EvidenceType.CONSTRAINT)

    ctx, provider = _make_ctx()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content="THIS IS NOT JSON",
            model_used="test",
            input_tokens=5,
            output_tokens=5,
            latency_ms=10.0,
        )
    )
    ctx.load_skill = MagicMock(return_value="skill")

    result = await extract_claims([ev_ot, ev_gnomad], "TRPC6", "FSGS", "inhibit", ctx)

    assert len(result) >= 2, "Must get ≥1 claim per structured evidence even with garbage LLM"
    tools = {c.provenance.tool_name for c in result}
    assert "structured_extraction" in tools


# ── structured_claims — CLINICAL_TRIAL deterministic branch ─────────────────


def _trial_ev(extra: dict, source: str = "NCT05213624") -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="TRPC6",
        gene_id="ENSG00000144935",
        disease="focal segmental glomerulosclerosis",
        disease_id="EFO_0004236",
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        scope="abstract",
        source=source,
        source_link="https://clinicaltrials.gov/ct2/show/" + source,
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
        extra=extra,
    )


def test_structured_claims_clinical_trial_yields_claim():
    """CLINICAL_TRIAL evidence with full extra → exactly one claim with correct shape."""
    ev = _trial_ev(
        {
            "title": "Phase 2 Study of XYZ in FSGS with TRPC6 Mutations",
            "phase": "Phase 2",
            "status": "RECRUITING",
            "interventions": ["XYZ inhibitor"],
            "conditions": ["Focal Segmental Glomerulosclerosis"],
            "enrollment": 60,
            "sponsor": "Sponsor Corp",
        }
    )
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_text  # non-empty
    assert "NCT05213624" in c.claim_text
    assert c.evidence_type == EvidenceType.CLINICAL_TRIAL
    assert c.confidence is not None and c.confidence >= 0.5
    assert c.source_evidence_id == ev.evidence_id
    assert c.direction == Direction.UNSPECIFIED
    assert c.provenance.tool_name == "structured_extraction"
    assert "(n=60)" in c.claim_text


def test_structured_claims_clinical_trial_minimal_extra():
    """Trial with sparse extra (no enrollment/interventions) → claim without raising."""
    ev = _trial_ev({})
    claims = structured_claims(ev, _RUN_ID, _TRACE)
    assert len(claims) == 1
    assert claims[0].claim_text
    assert claims[0].confidence >= 0.5


def test_trial_confidence_phase_ordering():
    """Phase 3 COMPLETED > Phase 1; all values ≥ 0.5."""
    p3_completed = _trial_confidence("Phase 3", "COMPLETED")
    p2_recruiting = _trial_confidence("Phase 2", "RECRUITING")
    p1_any = _trial_confidence("Phase 1", "ACTIVE")
    unknown = _trial_confidence("", "")

    assert p3_completed > p2_recruiting
    assert p2_recruiting > p1_any or p2_recruiting == p1_any  # P2 >= P1
    assert p3_completed > p1_any
    for val in (p3_completed, p2_recruiting, p1_any, unknown):
        assert val >= 0.5


async def test_extract_claims_clinical_trial_deterministic_with_empty_llm():
    """CLINICAL_TRIAL: deterministic claim present even when the LLM returns []."""
    from core.routing.providers.base import CompletionResult

    ev = _trial_ev(
        {
            "phase": "Phase 2",
            "status": "RECRUITING",
            "interventions": ["sparsentan"],
            "conditions": ["FSGS"],
            "enrollment": 50,
        }
    )

    ctx, provider = _make_ctx()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content="[]",
            model_used="test",
            input_tokens=5,
            output_tokens=2,
            latency_ms=10.0,
        )
    )
    ctx.load_skill = MagicMock(return_value="skill")

    result = await extract_claims([ev], "TRPC6", "FSGS", "inhibit", ctx)

    assert len(result) >= 1, (
        "Must get ≥1 deterministic claim for CLINICAL_TRIAL even when LLM returns []"
    )
    assert any(c.provenance.tool_name == "structured_extraction" for c in result)
    assert all(c.evidence_type == EvidenceType.CLINICAL_TRIAL for c in result)
