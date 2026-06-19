# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Druggability retrieval service — MCP-backed fetch.

Queries UniProt (protein profile + ChEMBL cross-reference) then ChEMBL
(drug mechanisms + bioactivity). Both public APIs → NON_SENSITIVE. Produces
``DRUGGABILITY`` evidence consumed by the biology lens (druggability / MoA axes).
"""

from __future__ import annotations

import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.druggability.tools import get_chemistry, get_protein_profile
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/druggability"


async def fetch_druggability(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> list[Evidence]:
    """Fetch druggability evidence from public UniProt + ChEMBL."""
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []

    # Protein profile (UniProt) — also carries the ChEMBL target cross-reference.
    async with span(f"{_SERVICE}:uniprot", trace_id=trace_id, input_data=gene) as ps:
        profile = await get_protein_profile(gene)
        ps.set_attribute("output", profile.text)

    prot_uri = archive_raw(
        gene,
        disease_id,
        direction_enum.value,
        "druggability",
        f"{gene}_uniprot.json",
        profile.model_dump_json(indent=2),
    )
    prot_prov = make_provenance(_SERVICE, "druggability.get_protein_profile", trace_id)
    evidences.append(
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=run_id,
            gene=gene,
            gene_id=gene_id,
            disease=disease,
            disease_id=disease_id,
            evidence_type=EvidenceType.DRUGGABILITY,
            scope="abstract",
            source=f"uniprot:{profile.uniprot_accession or gene}",
            source_link=profile.source_link,
            artifact_uri=prot_uri,
            classification=DataClass.NON_SENSITIVE,
            provenance=prot_prov,
            direction=direction_enum,
            extra=profile.model_dump(),
        )
    )

    # Chemistry (ChEMBL) — keyed off the UniProt-resolved ChEMBL target id.
    async with span(
        f"{_SERVICE}:chembl", trace_id=trace_id, input_data=profile.chembl_target_id
    ) as cs:
        chemistry = await get_chemistry(profile.chembl_target_id, gene_symbol=gene)
        cs.set_attribute("output", chemistry.text)

    chem_uri = archive_raw(
        gene,
        disease_id,
        direction_enum.value,
        "druggability",
        f"{gene}_chembl.json",
        chemistry.model_dump_json(indent=2),
    )
    chem_prov = make_provenance(_SERVICE, "druggability.get_chemistry", trace_id)
    evidences.append(
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=run_id,
            gene=gene,
            gene_id=gene_id,
            disease=disease,
            disease_id=disease_id,
            evidence_type=EvidenceType.DRUGGABILITY,
            scope="abstract",
            source=f"chembl:{chemistry.chembl_target_id or gene}",
            source_link=chemistry.source_link or profile.source_link,
            artifact_uri=chem_uri,
            classification=DataClass.NON_SENSITIVE,
            provenance=chem_prov,
            direction=direction_enum,
            extra=chemistry.model_dump(),
        )
    )

    return evidences
