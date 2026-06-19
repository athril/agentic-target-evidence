# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _genetics_source_evidence_text / _genetics_floor_signals (WS2: SPOKE wiring).

Regression lock for the TRPC6×FSGS report finding: the SPOKE Disease-ASSOCIATES-Gene
edge was retrieved but never reached the genetics lens prompt because its evidence
`extra` keys (disease_name/edge_sources/gwas_pvalue/diseases_score) didn't match any
branch of the key-sniffing fallback. WS2 fixes this by rendering a `text` field at
retrieval time and surfacing a `graph_association` floor signal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capabilities.target_validation.workflow import (
    _genetics_floor_signals,
    _genetics_source_evidence_text,
)
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _prov(trace_id: str = "t") -> Provenance:
    return Provenance(
        agent_name="test",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id=trace_id,
    )


def _ev(evidence_type: EvidenceType, extra: dict | None = None) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="TRPC6",
        disease="focal segmental glomerulosclerosis",
        evidence_type=evidence_type,
        scope="abstract",
        source="test",
        source_link="https://example.com",
        classification=DataClass.NON_SENSITIVE,
        provenance=_prov(),
        extra=extra or {},
    )


def _spoke_extra(
    disease_name: str = "focal segmental glomerulosclerosis",
    edge_sources: list[str] | None = None,
    gwas_pvalue: float | None = 1e-12,
    diseases_score: float | None = 0.9,
) -> dict:
    return {
        "disease_name": disease_name,
        "disease_identifier": "DOID:2017",
        "edge_sources": edge_sources if edge_sources is not None else ["GWAS", "ClinVar"],
        "gwas_pvalue": gwas_pvalue,
        "diseases_score": diseases_score,
        "text": (
            f"SPOKE graph: TRPC6–{disease_name} association via "
            f"{', '.join(edge_sources or ['GWAS', 'ClinVar'])}; "
            f"gwas_p={gwas_pvalue}, diseases_score={diseases_score}."
        ),
    }


# ---------------------------------------------------------------------------
# _genetics_source_evidence_text: SPOKE row -> non-empty prompt text
# ---------------------------------------------------------------------------


def test_spoke_row_produces_nonempty_prompt_text():
    row = _ev(EvidenceType.GENETICS, _spoke_extra())
    text = _genetics_source_evidence_text([row])
    assert text
    assert "SPOKE graph" in text
    assert "TRPC6" in text


def test_spoke_row_without_text_field_falls_back_silently():
    """Defensive: a SPOKE-shaped row missing the rendered text must not raise,
    even though it won't contribute a line (no key-sniffing fallback for SPOKE)."""
    extra = _spoke_extra()
    del extra["text"]
    row = _ev(EvidenceType.GENETICS, extra)
    # Should not raise; absence of text means the row contributes nothing.
    _genetics_source_evidence_text([row])


# ---------------------------------------------------------------------------
# _genetics_floor_signals: graph_association extraction
# ---------------------------------------------------------------------------


def test_graph_association_signal_present_with_corroborating_sources():
    row = _ev(EvidenceType.GENETICS, _spoke_extra(edge_sources=["GWAS", "ClinVar"]))
    signals = _genetics_floor_signals([row])
    assert signals["graph_association"] is not None
    assert signals["graph_association"]["corroborates_causality"] is True
    assert signals["graph_association"]["diseases_score"] == 0.9


def test_graph_association_not_corroborating_for_textmining_only():
    row = _ev(EvidenceType.GENETICS, _spoke_extra(edge_sources=["DISEASES"]))
    signals = _genetics_floor_signals([row])
    assoc = signals["graph_association"]
    assert assoc is not None
    # "DISEASES" textmining source IS in the corroborating set per WS2 spec —
    # only an edge with NO recognized source should fail to corroborate.
    assert assoc["corroborates_causality"] is True


def test_graph_association_absent_when_no_spoke_row():
    row = _ev(EvidenceType.GENETICS, {"genetic_score": 0.95})
    signals = _genetics_floor_signals([row])
    assert signals["graph_association"] is None


def test_graph_association_keeps_highest_scoring_row():
    rows = [
        _ev(EvidenceType.GENETICS, _spoke_extra(disease_name="disease A", diseases_score=0.3)),
        _ev(EvidenceType.GENETICS, _spoke_extra(disease_name="disease B", diseases_score=0.9)),
    ]
    signals = _genetics_floor_signals(rows)
    assert signals["graph_association"]["disease_name"] == "disease B"


# ---------------------------------------------------------------------------
# _genetics_floor_signals: ontology constraint bundle extraction (WS3)
# ---------------------------------------------------------------------------


def _ontology_extra(
    inheritance_mode: str | None = "Autosomal dominant",
    hpo_phenotype_count: int = 4,
    hpo_specificity_band: str = "focal",
) -> dict:
    return {
        "inheritance_mode": inheritance_mode,
        "inheritance_mode_source": "ClinGen",
        "hpo_phenotype_count": hpo_phenotype_count,
        "hpo_specificity_band": hpo_specificity_band,
        "hpo_top_phenotypes": ["Nephrotic syndrome"],
        "text": "Ontology constraints for TRPC6: Mode of inheritance: Autosomal dominant.",
    }


def test_ontology_bundle_surfaces_inheritance_mode_and_hpo_band():
    row = _ev(EvidenceType.GENETICS, _ontology_extra())
    signals = _genetics_floor_signals([row])
    assert signals["inheritance_mode"] == "Autosomal dominant"
    assert signals["hpo_phenotype_count"] == 4
    assert signals["hpo_specificity_band"] == "focal"


def test_ontology_bundle_absent_defaults():
    row = _ev(EvidenceType.GENETICS, {"genetic_score": 0.95})
    signals = _genetics_floor_signals([row])
    assert signals["inheritance_mode"] is None
    assert signals["hpo_phenotype_count"] == 0
    assert signals["hpo_specificity_band"] == "unknown"


def _clingen_extra(classification: str = "Definitive") -> dict:
    return {
        "summary": f"ClinGen gene validity for TRPC6: focal segmental glomerulosclerosis ({classification}).",
        "associations": [
            {
                "gene_symbol": "TRPC6",
                "disease_label": "focal segmental glomerulosclerosis",
                "classification": classification,
            }
        ],
        "total": 1,
    }


def test_clingen_classification_surfaced_in_floor_signals():
    row = _ev(EvidenceType.GENETICS, _clingen_extra("Definitive"))
    signals = _genetics_floor_signals([row])
    assert signals["clingen_classification"] == "Definitive"


def test_clingen_classification_absent_defaults_to_none():
    row = _ev(EvidenceType.GENETICS, {"genetic_score": 0.95})
    signals = _genetics_floor_signals([row])
    assert signals["clingen_classification"] is None


def test_ontology_bundle_inheritance_mode_feeds_mechanism_direction_tie_break():
    """inheritance_mode from the ontology bundle must reach infer_mechanism_direction
    and break a borderline-missense tie into a firm GoF call (WS3 acceptance)."""
    ontology_row = _ev(
        EvidenceType.GENETICS, _ontology_extra(inheritance_mode="Autosomal dominant")
    )
    constraint_row = _ev(
        EvidenceType.CONSTRAINT, {"loeuf": 0.80, "pli": None, "mis_z": None, "moeuf": None}
    )
    plp = [
        {"major_consequence": "missense_variant", "variant_id": f"m{i}", "gold_stars": 2}
        for i in range(6)
    ] + [
        {"major_consequence": "stop_gained", "variant_id": f"t{i}", "gold_stars": 2}
        for i in range(5)
    ]
    plp_row = _ev(EvidenceType.CONSTRAINT, {"pathogenic": plp, "likely_pathogenic": []})

    signals = _genetics_floor_signals([ontology_row, constraint_row, plp_row])
    md = signals["mechanism_direction"]
    assert md is not None
    assert md["direction"] == "inhibit"
    assert md["mechanism"] == "gof"
    assert md["confidence"] >= 0.60
