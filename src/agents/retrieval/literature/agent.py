# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Literature data-acquisition agent."""

from __future__ import annotations

import uuid

from agents._common import make_provenance, result_msg
from agents.retrieval.literature.contract import CONTRACT
from core.persistence.artifact_store import archive_raw
from harness.base_agent import BaseAgent
from harness.context import RunContext
from mcp_servers.pubmed.tools import PubMedRecord, resolve_mesh_term, search_pubmed
from schemas.evidence import DataClass, Evidence, EvidenceType
from schemas.messages import AgentMessage

_TARGET_MIN = 20
_TARGET_MAX = 1000


def _concept_term(label: str, mesh: str | None) -> str:
    """A controlled-vocabulary clause for a disease/population concept.

    When the concept resolves to a MeSH descriptor we OR the canonical
    "[MeSH Terms]" heading with the free-text "[tiab]" label — MeSH gives the
    indexed recall, tiab catches very recent, not-yet-indexed papers. When no
    descriptor exists, fall back to an untagged Automatic Term Mapping term
    rather than a bare "[tiab]" phrase, which matches almost nothing.
    """
    if mesh:
        return f'("{mesh}"[MeSH Terms] OR "{label}"[tiab])'
    return f"({label})"


def _build_base_query(
    gene: str,
    disease: str,
    population: str | None,
    *,
    disease_mesh: str | None = None,
    population_mesh: str | None = None,
) -> str:
    gene_term = f'"{gene}"[tiab]'
    scope = '("journal article"[pt] OR "review"[pt]) AND "english"[la] AND "2000/01/01"[pdat] : "3000/12/31"[pdat]'
    parts = [gene_term, _concept_term(disease, disease_mesh), scope]
    if population:
        parts.append(_concept_term(population, population_mesh))
    return " AND ".join(parts)


def _narrow(query: str) -> str:
    return query + ' AND ("2020/01/01"[pdat] : "3000/12/31"[pdat])'


def _widen(gene: str, disease: str, disease_mesh: str | None = None) -> str:
    # Drop the article-type/date scope so the fallback can escape a low-result
    # base query, but keep the resolved MeSH disease clause.
    return f'"{gene}"[tiab] AND {_concept_term(disease, disease_mesh)} AND "english"[la]'


def _render_markdown(record: PubMedRecord) -> str:
    authors = ", ".join(record.authors) or "—"
    return (
        f"# {record.title}\n\n"
        f"**PMID:** {record.pmid}  \n"
        f"**Authors:** {authors}  \n"
        f"**Journal:** {record.journal}  \n"
        f"**Year:** {record.pub_year or '—'}  \n"
        f"**Link:** https://pubmed.ncbi.nlm.nih.gov/{record.pmid}/\n\n"
        f"## Abstract\n\n{record.abstract or '_No abstract available._'}\n"
    )


def _to_evidence(
    record: PubMedRecord,
    msg: AgentMessage,
    query: str,
    artifact_uri: str | None,
    gene_id: str,
    disease_id: str,
) -> Evidence:
    prov = make_provenance("literature", "search_pubmed", msg.trace_id)
    year = record.pub_year or 2000
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=msg.run_id,
        gene=msg.task_spec["target_gene"],
        gene_id=gene_id,
        disease=msg.task_spec["disease"],
        disease_id=disease_id,
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source=f"PMID:{record.pmid}",
        source_link=f"https://pubmed.ncbi.nlm.nih.gov/{record.pmid}/",
        query_used=query,
        artifact_uri=artifact_uri,
        classification=DataClass.NON_SENSITIVE,
        provenance=prov,
        extra={
            "pmid": record.pmid,
            "title": record.title,
            "abstract": record.abstract,
            "journal": record.journal,
            "full_journal": record.full_journal,
            "issn": record.issn,
            "essn": record.essn,
            "pub_year": year,
            "authors": record.authors,
        },
    )


class LiteratureAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        gene = spec["target_gene"]
        gene_id = spec.get("gene_id") or ""
        disease = spec["disease"]
        disease_id = spec.get("disease_id") or ""
        direction = spec.get("direction") or "unspecified"
        population = spec.get("population")

        # Resolve concepts to their canonical MeSH descriptors so the
        # "[MeSH Terms]" clauses actually match (PubMed silently drops a quoted
        # MeSH phrase whose casing/plural differs from the real heading).
        disease_mesh: str | None = None
        query = spec.get("query")
        if not query:
            disease_mesh = await resolve_mesh_term(disease)
            population_mesh = await resolve_mesh_term(population) if population else None
            query = _build_base_query(
                gene,
                disease,
                population,
                disease_mesh=disease_mesh,
                population_mesh=population_mesh,
            )

        records: list[PubMedRecord] = []
        for _ in range(self.contract.max_loops):
            records = await search_pubmed(query, max_results=_TARGET_MAX)
            count = len(records)
            if count > _TARGET_MAX:
                query = _narrow(query)
            elif count < _TARGET_MIN:
                query = _widen(gene, disease, disease_mesh)
            else:
                break

        evidences = []
        for r in records:
            uri = archive_raw(
                gene, disease_id, direction, "papers", f"{r.pmid}.md", _render_markdown(r)
            )
            evidences.append(_to_evidence(r, msg, query, uri, gene_id, disease_id))
        return result_msg(msg, evidences)
