# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Druggability retrieval service — MCP-backed fetch.

Queries UniProt (protein profile + ChEMBL cross-reference) then ChEMBL
(drug mechanisms + bioactivity), then DGIdb (curated drug-gene interaction
claims + druggable-genome gene-category annotations). All three are public
APIs → NON_SENSITIVE. Produces ``DRUGGABILITY`` evidence consumed by the
biology lens (druggability / MoA axes).

DGIdb is additive enrichment, not the primary signal — it aggregates per-claim
source provenance and interaction directionality that ChEMBL's narrower
"mechanism" annotation doesn't carry, and is independent of the UniProt/ChEMBL
cross-reference chain above. Its two calls are wrapped individually and degrade
gracefully (mirroring the IMPC/Project Score pattern in
services/retrieval/functional.py): a DGIdb outage drops only the DGIdb evidence
rows, not the whole fetch.

TTD (Therapeutic Target Database) is optional, gated behind ``TTD_ENABLED``
(off by default — see mcp_servers/ttd/tools.py module docstring on its
unconfirmed commercial-use terms) and skipped entirely, not just degraded,
when disabled — mirroring how OMIM is consumed in agents/retrieval/genetics.
It adds TTD's own target development-stage classification (Successful /
Clinical Trial / Research Target, ...) that DGIdb's aggregated claims don't
carry.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.chembl.tools import get_chemistry
from mcp_servers.dgidb.tools import get_gene_categories, get_gene_drug_interactions
from mcp_servers.ttd.tools import get_ttd_target_status, ttd_configured
from mcp_servers.uniprot.tools import get_protein_profile
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

    # DGIdb: curated drug-gene interaction claims, aggregated across dozens of
    # source databases (DrugBank, PharmGKB, CIViC, OncoKB, FDA, ...).
    async with span(f"{_SERVICE}:dgidb_interactions", trace_id=trace_id, input_data=gene) as dis:
        try:
            dgidb_interactions = await get_gene_drug_interactions(gene)
            dis.set_attribute("output", dgidb_interactions.text)
        except Exception as exc:
            dis.set_attribute("error", str(exc))
            dgidb_interactions = None

    if dgidb_interactions and dgidb_interactions.interactions:
        dgidb_int_uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "druggability",
            f"{gene}_dgidb_interactions.json",
            dgidb_interactions.model_dump_json(indent=2),
        )
        dgidb_int_prov = make_provenance(_SERVICE, "dgidb.get_gene_drug_interactions", trace_id)
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
                source=f"dgidb:{dgidb_interactions.gene_concept_id or gene}",
                source_link=f"https://dgidb.org/results?searchType=gene&searchTerm={gene}",
                artifact_uri=dgidb_int_uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=dgidb_int_prov,
                direction=direction_enum,
                extra=dgidb_interactions.model_dump(),
            )
        )

    # DGIdb: druggable-genome / gene-category annotations (e.g. DRUGGABLE GENOME,
    # KINASE, CLINICALLY ACTIONABLE) — a categorical signal UniProt/ChEMBL don't provide.
    async with span(f"{_SERVICE}:dgidb_categories", trace_id=trace_id, input_data=gene) as dcs:
        try:
            dgidb_categories = await get_gene_categories(gene)
            dcs.set_attribute("output", dgidb_categories.text)
        except Exception as exc:
            dcs.set_attribute("error", str(exc))
            dgidb_categories = None

    if dgidb_categories and dgidb_categories.categories:
        dgidb_cat_uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "druggability",
            f"{gene}_dgidb_categories.json",
            dgidb_categories.model_dump_json(indent=2),
        )
        dgidb_cat_prov = make_provenance(_SERVICE, "dgidb.get_gene_categories", trace_id)
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
                source=f"dgidb:{gene}",
                source_link=f"https://dgidb.org/results?searchType=gene&searchTerm={gene}",
                artifact_uri=dgidb_cat_uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=dgidb_cat_prov,
                direction=direction_enum,
                extra=dgidb_categories.model_dump(),
            )
        )

    # TTD: target development-stage classification + mapped drugs. Skipped
    # entirely (no call, no span) unless explicitly opted in via TTD_ENABLED.
    if ttd_configured():
        async with span(f"{_SERVICE}:ttd", trace_id=trace_id, input_data=gene) as ts:
            try:
                ttd_bundle = await get_ttd_target_status(gene)
                ts.set_attribute("output", ttd_bundle.text)
            except Exception as exc:
                ts.set_attribute("error", str(exc))
                ttd_bundle = None

        if ttd_bundle and ttd_bundle.record:
            ttd_uri = archive_raw(
                gene,
                disease_id,
                direction_enum.value,
                "druggability",
                f"{gene}_ttd.json",
                ttd_bundle.model_dump_json(indent=2),
            )
            ttd_prov = make_provenance(_SERVICE, "ttd.get_ttd_target_status", trace_id)
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
                    source=f"ttd:{ttd_bundle.record.ttd_target_id or gene}",
                    source_link=f"https://ttd.idrblab.cn/search?searchType=target&searchTerm={gene}",
                    artifact_uri=ttd_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=ttd_prov,
                    direction=direction_enum,
                    extra=ttd_bundle.model_dump(),
                )
            )

    return evidences
