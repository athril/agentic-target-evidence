# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Open Targets retrieval service — deterministic MCP-backed fetch."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.opentargets.tools import (
    get_associations,
    get_known_drugs,
    get_mouse_phenotypes,
    get_safety,
    get_tractability,
    resolve_disease,
    resolve_gene,
    unique_drug_counts,
)
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/opentargets"


@dataclass
class OpenTargetsResult:
    evidences: list[Evidence]
    gene_id: str  # resolved Ensembl ID (propagate to state)
    disease_id: str  # resolved EFO/MONDO ID (propagate to state)


async def fetch_opentargets(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> OpenTargetsResult:
    """Fetch Open Targets association + tractability evidence.

    Returns a result object so the graph node can propagate the resolved
    gene_id / disease_id back to state for downstream agents.
    """
    async with span(_SERVICE, trace_id=trace_id, input_data=f"{gene}|{disease}"):
        if not gene_id:
            gene_id = await resolve_gene(gene)
        if not disease_id:
            disease_id = await resolve_disease(disease)

        assoc, tract, drugs, safety, mouse = await asyncio.gather(
            get_associations(gene_id, disease_id),
            get_tractability(gene_id),
            get_known_drugs(gene_id),
            get_safety(gene_id),
            get_mouse_phenotypes(gene_id),
        )

    extra = {
        "gene_id": gene_id,
        "disease_id": disease_id,
        # association scores
        "overall_score": assoc.overall_score,
        "genetic_score": assoc.genetic_score,
        "literature_score": assoc.literature_score,
        "known_drugs_score": assoc.known_drugs_score,
        "assoc_source_link": assoc.source_link,
        "assoc_text": assoc.text,
        # tractability
        "tractability_small_molecule": tract.small_molecule,
        "tractability_antibody": tract.antibody,
        "tractability_other": tract.other_modalities,
        "tractability_score": 1.0 if (tract.small_molecule or tract.antibody) else 0.0,
        "tract_source_link": tract.source_link,
        "tract_text": tract.text,
        # known drugs
        "known_drugs_count": unique_drug_counts(drugs.drugs)["total"] or drugs.total_count,
        "known_drugs_approved_count": unique_drug_counts(drugs.drugs)["approved"],
        "known_drugs_phase3_count": unique_drug_counts(drugs.drugs)["phase3"],
        "known_drugs": [d.model_dump() for d in drugs.drugs],
        "known_drugs_text": drugs.text,
        # safety liabilities
        "safety_liability_count": len(safety.liabilities),
        "safety_liability_events": [li.event for li in safety.liabilities],
        "safety_liabilities": [li.model_dump() for li in safety.liabilities],
        "safety_text": safety.text,
        # mouse phenotypes
        "mouse_phenotype_count": len(mouse.phenotypes),
        "mouse_phenotype_labels": [p.phenotype_label for p in mouse.phenotypes],
        "mouse_phenotypes": [p.model_dump() for p in mouse.phenotypes],
        "mouse_text": mouse.text,
    }

    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    content = json.dumps(extra, indent=2, default=str)
    uri = archive_raw(
        gene, disease_id, direction_enum.value, "opentargets", f"{gene_id}.json", content
    )

    prov = make_provenance(
        _SERVICE,
        "get_associations+get_tractability+get_known_drugs+get_safety+get_mouse_phenotypes",
        trace_id,
    )
    evidence = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene=gene,
        gene_id=gene_id,
        disease=disease,
        disease_id=disease_id,
        evidence_type=EvidenceType.GENETICS,
        scope="abstract",
        source=f"opentargets:{gene_id}:{disease_id}",
        source_link=assoc.source_link,
        artifact_uri=uri,
        classification=DataClass.NON_SENSITIVE,
        provenance=prov,
        direction=direction_enum,
        extra=extra,
    )
    return OpenTargetsResult(evidences=[evidence], gene_id=gene_id, disease_id=disease_id)
