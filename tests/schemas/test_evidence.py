# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from schemas.evidence import DataClass, Evidence, EvidenceType


def test_evidence_round_trips_json(sample_evidence: Evidence) -> None:
    restored = Evidence.model_validate_json(sample_evidence.model_dump_json())
    assert restored == sample_evidence


def test_evidence_rejects_extra_fields(sample_evidence: Evidence) -> None:
    data = sample_evidence.model_dump()
    data["unknown_field"] = "should_be_rejected"
    with pytest.raises(ValidationError):
        Evidence.model_validate(data)


def test_evidence_is_frozen(sample_evidence: Evidence) -> None:
    with pytest.raises(ValidationError):
        sample_evidence.gene = "TP53"  # type: ignore[misc]


def test_data_class_values_are_exhaustive() -> None:
    assert set(DataClass) == {DataClass.SENSITIVE, DataClass.NON_SENSITIVE}


def test_evidence_type_covers_all_architecture_types() -> None:
    expected = {
        "article",
        "book",
        "patent",
        "clinical_trial",
        "genetics",
        "omics",
        "abstract",
        "conference",
        "constraint",
        "functional_genomics",
        "expression",
        "druggability",
        "regulatory",
    }
    assert {e.value for e in EvidenceType} == expected


@pytest.mark.parametrize(
    "evidence_type",
    [
        EvidenceType.CONSTRAINT,
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.DRUGGABILITY,
    ],
)
def test_new_evidence_types_round_trip(
    evidence_type: EvidenceType, run_id: uuid.UUID, sample_provenance
) -> None:
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="PCSK9",
        gene_id="ENSG00000169174",
        disease="hypercholesterolemia",
        disease_id="EFO_0000305",
        evidence_type=evidence_type,
        scope="abstract",
        source=f"test:{evidence_type.value}",
        source_link="https://example.com",
        provenance=sample_provenance,
        classification=DataClass.NON_SENSITIVE,
    )
    restored = Evidence.model_validate_json(ev.model_dump_json())
    assert restored == ev
    assert restored.evidence_type == evidence_type


def test_schema_version_1_0_still_validates(run_id: uuid.UUID, sample_provenance) -> None:
    """Backward compatibility: payloads with schema_version=1.0 must still parse."""
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="BRCA1",
        disease="breast cancer",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source="PMID:1",
        source_link="https://pubmed.ncbi.nlm.nih.gov/1/",
        provenance=sample_provenance,
        classification=DataClass.NON_SENSITIVE,
        schema_version="1.0",
    )
    assert ev.schema_version == "1.0"
    restored = Evidence.model_validate_json(ev.model_dump_json())
    assert restored.schema_version == "1.0"


def test_legacy_target_gene_alias_accepted(run_id: uuid.UUID, sample_provenance) -> None:
    """Backward compat: rows serialised with 'target_gene' must deserialise to 'gene'."""
    data = {
        "schema_version": "1.0",
        "evidence_id": str(uuid.uuid4()),
        "run_id": str(run_id),
        "target_gene": "BRCA1",  # old field name
        "disease": "breast cancer",
        "evidence_type": "article",
        "scope": "abstract",
        "source": "PMID:1",
        "source_link": "https://pubmed.ncbi.nlm.nih.gov/1/",
        "provenance": {
            "agent_name": "test",
            "trace_id": "t1",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        "classification": "NON_SENSITIVE",
    }
    ev = Evidence.model_validate(data)
    assert ev.gene == "BRCA1"


def test_schema_version_defaults_to_1_0(sample_evidence: Evidence) -> None:
    assert sample_evidence.schema_version == "1.0"


def test_direction_defaults_to_unspecified(sample_evidence: Evidence) -> None:
    from schemas.evidence import Direction

    assert sample_evidence.direction == Direction.UNSPECIFIED


def test_availability_date_defaults_to_none(sample_evidence: Evidence) -> None:
    assert sample_evidence.availability_date is None


def test_disease_id_and_gene_id_present(sample_evidence: Evidence) -> None:
    assert sample_evidence.disease_id == "EFO_0000305"
    assert sample_evidence.gene_id == "ENSG00000012048"


def test_disease_id_defaults_to_empty_string(run_id: uuid.UUID, sample_provenance) -> None:
    """Legacy rows with no disease_id/gene_id still validate cleanly."""
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="TP53",
        disease="cancer",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source="PMID:2",
        source_link="https://pubmed.ncbi.nlm.nih.gov/2/",
        provenance=sample_provenance,
        classification=DataClass.NON_SENSITIVE,
    )
    assert ev.disease_id == ""
    assert ev.gene_id == ""


def test_evidence_requires_provenance(run_id: uuid.UUID) -> None:
    with pytest.raises(ValidationError):
        Evidence(  # type: ignore[call-arg]
            evidence_id=uuid.uuid4(),
            run_id=run_id,
            gene="BRCA1",
            disease="breast cancer",
            evidence_type=EvidenceType.ARTICLE,
            scope="abstract",
            source="PMID:1",
            source_link="https://pubmed.ncbi.nlm.nih.gov/1/",
            # provenance intentionally omitted
            classification=DataClass.NON_SENSITIVE,
        )


def test_from_dict_round_trips(sample_evidence: Evidence) -> None:
    restored = Evidence.from_dict(sample_evidence.model_dump())
    assert restored == sample_evidence


def test_from_dict_rejects_extra_fields(sample_evidence: Evidence) -> None:
    data = sample_evidence.model_dump()
    data["surprise"] = "should fail"
    with pytest.raises(ValidationError):
        Evidence.from_dict(data)


# ── Core-plus-typed (v0.5) round-trip ────────────────────────────────────────


def test_split_claim_yields_core_and_extension(sample_evidence: Evidence) -> None:
    from schemas.evidence import (
        CoreClaim,
        LiteratureClaim,
        split_claim,
    )

    core, ext = split_claim(sample_evidence)
    assert isinstance(core, CoreClaim)
    assert isinstance(ext, LiteratureClaim)  # ARTICLE → LiteratureClaim
    assert core.gene == sample_evidence.gene
    assert core.evidence_type == sample_evidence.evidence_type
    # the source/retrieval fields land in the extension, not the core
    assert ext.source == sample_evidence.source  # type: ignore[attr-defined]


# ── Fingerprint helpers ───────────────────────────────────────────────────────


def test_source_fingerprint_is_deterministic() -> None:
    from schemas.evidence import source_fingerprint

    fp1 = source_fingerprint("BRCA1", "breast cancer", "inhibit", "article", "PMID:12345")
    fp2 = source_fingerprint("BRCA1", "breast cancer", "inhibit", "article", "PMID:12345")
    assert fp1 == fp2


def test_lens_fingerprint_differs_per_lens_name() -> None:
    from schemas.evidence import lens_fingerprint

    fp_genetics = lens_fingerprint("BRCA1", "breast cancer", "inhibit", "genetics")
    fp_biology = lens_fingerprint("BRCA1", "breast cancer", "inhibit", "biology")
    assert fp_genetics != fp_biology


def test_fingerprints_have_64_char_length() -> None:
    from schemas.evidence import experiment_fingerprint, lens_fingerprint, source_fingerprint

    assert len(source_fingerprint("BRCA1", "breast cancer", "inhibit", "article", "PMID:1")) == 64
    assert len(lens_fingerprint("BRCA1", "breast cancer", "inhibit", "genetics")) == 64
    assert len(experiment_fingerprint("BRCA1", "breast cancer", "inhibit")) == 64


def test_source_fingerprint_differs_across_sources() -> None:
    from schemas.evidence import source_fingerprint

    fp1 = source_fingerprint("BRCA1", "breast cancer", "inhibit", "article", "PMID:111")
    fp2 = source_fingerprint("BRCA1", "breast cancer", "inhibit", "article", "PMID:999")
    assert fp1 != fp2


@pytest.mark.parametrize("evidence_type", list(EvidenceType))
def test_every_v02_row_splits_into_core_plus_extension(
    evidence_type: EvidenceType, run_id: uuid.UUID, sample_provenance
) -> None:
    """v0.5 invariant: every evidence type deserializes into core + a typed extension."""
    from schemas.evidence import EXTENSION_FOR, ClaimExtension, CoreClaim, split_claim

    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="PCSK9",
        disease="hypercholesterolemia",
        evidence_type=evidence_type,
        scope="abstract",
        source=f"src:{evidence_type.value}",
        source_link="https://example.com",
        provenance=sample_provenance,
        classification=DataClass.NON_SENSITIVE,
        extra={"k": "v"},
    )
    core, ext = split_claim(ev)
    assert isinstance(core, CoreClaim)
    expected_ext = EXTENSION_FOR.get(evidence_type, ClaimExtension)
    assert isinstance(ext, expected_ext)
