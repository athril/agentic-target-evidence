# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenFDA retrieval service — drug labels (SPL) and FAERS adverse event signal.

Produces REGULATORY evidence consumed by:
  - safety_lens: FAERS adverse-event signal (serious/death rates, top reactions,
    black-box warnings, contraindications) via the faers_text extra-context key.
  - commercial_lens: FDA-approved drug landscape (drugs naming the gene in MoA,
    approved indications) via the fda_label_text extra-context key.
  - regulatory_lens: approval precedent and label-derived safety flags
    (approval_precedent, label_safety, regulatory_de_risking axes) via fda_label_text.
Both sources are NON_SENSITIVE public FDA APIs.

Strategy:
  1. Search drug labels by gene symbol (MoA mentions) and disease indication.
  2. For up to _MAX_FAERS_DRUGS drugs found, fetch FAERS adverse event summaries.
Each label and each FAERS bundle becomes a separate Evidence row so the screening
agent and lenses can independently weight them.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.openfda.tools import search_adverse_events, search_drug_labels
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/openfda"
_MAX_FAERS_DRUGS = 5  # cap FAERS calls to avoid rate-limit pressure


async def fetch_openfda(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> list[Evidence]:
    """Fetch OpenFDA label + FAERS evidence for a gene/disease target."""
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []

    # ── Step 1: Drug labels ──────────────────────────────────────────────────
    async with span(f"{_SERVICE}:labels", trace_id=trace_id, input_data=f"{gene} | {disease}") as s:
        labels = await search_drug_labels(gene, disease)
        s.set_attribute("output", f"{len(labels)} label(s) found")

    label_prov = make_provenance(_SERVICE, "openfda.search_drug_labels", trace_id)
    for label in labels:
        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "openfda",
            f"label_{label.drug_name.replace(' ', '_')}.json",
            label.model_dump_json(indent=2),
        )
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.REGULATORY,
                scope="abstract",
                source=f"fda:label:{label.application_number or label.drug_name}",
                source_link=label.source_link,
                artifact_uri=uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=label_prov,
                direction=direction_enum,
                extra=label.model_dump(),
            )
        )

    # ── Step 2: FAERS adverse event summaries for each labeled drug ──────────
    faers_prov = make_provenance(_SERVICE, "openfda.search_adverse_events", trace_id)
    for label in labels[:_MAX_FAERS_DRUGS]:
        async with span(
            f"{_SERVICE}:faers:{label.drug_name}",
            trace_id=trace_id,
            input_data=label.drug_name,
        ) as fs:
            bundle = await search_adverse_events(label.drug_name)
            fs.set_attribute("output", bundle.text)

        if bundle.total_reports == 0:
            continue

        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "openfda",
            f"faers_{label.drug_name.replace(' ', '_')}.json",
            bundle.model_dump_json(indent=2),
        )
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.REGULATORY,
                scope="abstract",
                source=f"fda:faers:{label.drug_name}",
                source_link=bundle.source_link,
                artifact_uri=uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=faers_prov,
                direction=direction_enum,
                extra=bundle.model_dump(),
            )
        )

    return evidences
