# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl


class DataClass(StrEnum):
    SENSITIVE = "SENSITIVE"
    NON_SENSITIVE = "NON_SENSITIVE"


class Direction(StrEnum):
    """Therapeutic hypothesis direction — part of the run entity
    ``(gene, disease, direction)`` and stamped on every claim.

    ``unspecified`` is the default so rows that predate direction still load.
    """

    INHIBIT = "inhibit"
    ACTIVATE = "activate"
    DEGRADE = "degrade"
    MODULATE = "modulate"
    UNSPECIFIED = "unspecified"


class EvidenceType(StrEnum):
    ARTICLE = "article"
    BOOK = "book"
    PATENT = "patent"
    CLINICAL_TRIAL = "clinical_trial"
    GENETICS = "genetics"
    OMICS = "omics"
    ABSTRACT = "abstract"
    CONFERENCE = "conference"
    CONSTRAINT = "constraint"  # gnomAD LoF/missense constraint
    FUNCTIONAL_GENOMICS = "functional_genomics"  # CRISPR/RNAi dependency (DepMap)
    EXPRESSION = "expression"  # tissue expression / localization (GTEx/HPA)
    DRUGGABILITY = "druggability"  # protein class + chemistry + curated interactions (UniProt/ChEMBL/DGIdb)
    REGULATORY = "regulatory"  # FDA drug labels + FAERS adverse event signal
    REGULATORY_ELEMENT = "regulatory_element"  # cis-regulatory assay coverage at locus (ENCODE)


class LensTopic(StrEnum):
    """Interpretation lenses a free-text literature claim is relevant to.

    Used to route an otherwise-undifferentiated literature claim (``ARTICLE`` /
    ``ABSTRACT`` / ``BOOK`` / ``CONFERENCE``) to the lenses that should reason over
    it. Multi-valued: one claim may serve several lenses (e.g. a knockout-lethality
    claim is both ``BIOLOGY`` and ``SAFETY``). Only these four lenses consume
    literature; commercial/regulatory reason over structured sources instead.
    """

    GENETICS = "genetics"
    BIOLOGY = "biology"
    SAFETY = "safety"
    CLINICAL = "clinical"


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str
    tool_name: str | None = None
    timestamp: datetime
    model_used: str | None = None
    trace_id: str


class CoreClaim(BaseModel):
    """Shared base for every atomic claim / evidence row (the claim substrate).

    The flat ``Evidence`` is the first concrete ``CoreClaim`` — one row per
    retrieved document. Claim extraction produces many ``CoreClaim`` rows per
    document, each carrying an atomic ``claim_text``. All shared entity, provenance,
    direction, and temporal fields live here; source/retrieval fields live on
    ``Evidence`` (and, later, typed extensions own type-specific payload).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0"] = "1.0"
    evidence_id: UUID
    # Provenance pointer to the document this claim was extracted from. None for
    # document-level Evidence rows (which *are* the source); lets reports resolve
    # a claim back to its PMID / NCT / patent / gene source link.
    source_evidence_id: UUID | None = None
    run_id: UUID
    # "target_gene" accepted as a validation alias for backward compat.
    gene: str = Field(validation_alias=AliasChoices("gene", "target_gene"))
    gene_id: str = ""  # Ensembl ID (e.g. ENSG00000012048)
    disease: str
    disease_id: str = ""  # EFO/MONDO ID (e.g. EFO_0000305)
    direction: Direction = Direction.UNSPECIFIED
    population: str | None = None
    evidence_type: EvidenceType
    claim_text: str = ""  # atomic claim statement; "" for document-level rows
    confidence: float | None = None  # extractor/model confidence in the claim
    # Lens-routing hint for free-text literature claims only. Empty for structured
    # claims (which route by ``evidence_type``) and for document-level Evidence rows.
    # Biology stays the literature catch-all; these tags fan a claim out to
    # genetics/safety/clinical as well.
    topics: list[LensTopic] = []
    availability_date: date | None = None  # source publication date — drives the temporal cut
    provenance: Provenance
    classification: DataClass

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoreClaim:
        """Validate a plain dict and return a frozen instance.

        Round-trips through JSON so that types such as UUID and HttpUrl are
        coerced exactly the same way as over-the-wire deserialisation.
        """
        return cls.model_validate_json(cls.model_validate(data).model_dump_json())


class Evidence(CoreClaim):
    """A retrieved-source claim: the document-level ``CoreClaim``.

    Kept as the single constructable evidence model so every acquisition agent and
    persistence path that builds ``Evidence(...)`` keeps working unchanged. Claim
    extraction adds atomic, typed claims as additional ``CoreClaim`` rows alongside.
    """

    scope: Literal["abstract", "full_text"]
    source: str  # PMID / NCT ID / patent number / etc.
    source_link: HttpUrl | str
    query_used: str | None = None
    artifact_uri: str | None = None  # file:// or s3:// pointer to derived artifact
    extra: dict[str, Any] = {}


def source_fingerprint(
    gene: str,
    disease: str,
    direction: str,
    evidence_type: str,
    source: str,
) -> str:
    """64-char hex SHA-256; stable cache key for per-evidence screening verdicts."""
    key = f"screening|{gene}|{disease}|{direction}|{evidence_type}|{source}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]


def lens_fingerprint(gene: str, disease: str, direction: str, lens_name: str) -> str:
    """64-char hex SHA-256; stable cache key for per-lens verdicts."""
    key = f"lens|{gene}|{disease}|{direction}|{lens_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]


def experiment_fingerprint(gene: str, disease: str, direction: str) -> str:
    """64-char hex SHA-256; stable cache key for experiment results."""
    key = f"experiment|{gene}|{disease}|{direction}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]


def source_quality_fingerprint(gene: str, disease: str, direction: str) -> str:
    """64-char hex SHA-256; stable cache key for the per-run source-quality map."""
    key = f"source_quality|{gene}|{disease}|{direction}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]
