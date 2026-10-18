# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Literature data-acquisition agent."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agents._common import make_provenance, result_msg
from agents.retrieval.literature.contract import CONTRACT
from core.persistence.artifact_store import archive_raw
from harness.base_agent import BaseAgent
from harness.context import RunContext
from mcp_servers.pubmed.tools import (
    ArticleMetadata,
    MeshHeading,
    PubMedRecord,
    fetch_article_metadata,
    resolve_mesh_term,
    search_pubmed,
)
from schemas.evidence import DataClass, Evidence, EvidenceType
from schemas.messages import AgentMessage
from services.retrieval.clinical_trial import known_drug_names

logger = logging.getLogger(__name__)

_TARGET_MIN = 20
_TARGET_MAX = 1000

# Target-level queries that do not depend on the disease. The gene × disease query
# misses evidence the lenses need about the *target*: human genetics outside the
# indication (PCSK9: the Mendelian-randomisation papers linking PCSK9 inhibition to
# type-2-diabetes risk), the outcome trials of the target's drugs, and safety studies.
# Each is small and relevance-ranked; its papers carry ``retrieval_role``.
_ROLE_MAX = {
    "target_genetics": 40,
    "target_human_genetics": 30,
    "target_drug_trials": 60,
    "target_safety": 30,
    "indication_drug_trials": 40,
}
_MAX_DRUGS_IN_QUERY = 6


def target_level_queries(gene: str, drugs: list[str]) -> dict[str, str]:
    """PubMed queries for disease-independent evidence about the target."""
    gene_term = f'"{gene}"[tiab]'
    english = '"english"[la]'
    queries = {
        "target_genetics": (
            f'{gene_term} AND ("mendelian randomization"[tiab] OR "mendelian randomisation"[tiab] '
            'OR "loss-of-function"[tiab] OR "gain-of-function"[tiab] OR "protective variant"[tiab] '
            f'OR "genetic variants"[tiab]) AND {english}'
        ),
        # Gene in the title: Best Match ranks broad reviews above the genetic-proxy
        # studies that matter for on-target safety (PCSK9 → type-2 diabetes MR; LRRK2
        # → loss-of-function carriers in biobanks).
        "target_human_genetics": (
            f'"{gene}"[ti] AND ("mendelian randomization"[tiab] OR "mendelian randomisation"[tiab] '
            'OR "genetic variants"[ti] OR "loss-of-function"[ti] OR "genetic proxies"[tiab]) '
            f"AND {english}"
        ),
        "target_safety": (
            f'{gene_term} AND (safety[ti] OR adverse[ti] OR toxicity[ti] OR "knockout mice"[tiab] '
            f'OR "deficient mice"[tiab]) AND {english}'
        ),
    }
    names = [d for d in dict.fromkeys(drugs) if d][:_MAX_DRUGS_IN_QUERY]
    if names:
        drug_clause = " OR ".join(f'"{d}"[tiab]' for d in names)
        queries["target_drug_trials"] = (
            f'({drug_clause}) AND ("randomized controlled trial"[pt] OR "meta-analysis"[pt]) '
            f"AND {english}"
        )
    return queries


def indication_drug_trial_query(drugs: list[str], disease: str, disease_mesh: str | None) -> str:
    """Trials and trial reports of the target's drugs in this indication.

    The disease-independent drug query ranks a drug's main indication first, so a drug
    approved in one disease and failed in another returned only the approved one's
    trials (IL17A × Crohn's disease: psoriasis RCTs, while the negative Crohn's trial
    came in only by luck through the gene query). Phase 2 reports are indexed as
    "clinical trial", not "randomized controlled trial", hence the wider type set.
    """
    names = [d for d in dict.fromkeys(drugs) if d][:_MAX_DRUGS_IN_QUERY]
    if not names or not disease:
        return ""
    drug_clause = " OR ".join(f'"{d}"[tiab]' for d in names)
    return (
        f"({drug_clause}) AND {_concept_term(disease, disease_mesh)} AND "
        '("clinical trial"[pt] OR "randomized controlled trial"[pt] OR "meta-analysis"[pt] '
        'OR "clinical trial, phase ii"[pt] OR "clinical trial, phase iii"[pt]) '
        'AND "english"[la]'
    )


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
    gene: str,
    gene_id: str,
    disease: str,
    disease_id: str,
) -> Evidence:
    prov = make_provenance("literature", "search_pubmed", msg.trace_id)
    year = record.pub_year or 2000
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=msg.run_id,
        gene=gene,
        gene_id=gene_id,
        disease=disease,
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
            **_metadata_extra(
                record.publication_types, record.mesh_headings, record.retracted, record.indexed
            ),
        },
    )


def _metadata_extra(
    publication_types: list[str],
    mesh_headings: list[MeshHeading],
    retracted: bool,
    indexed: bool,
) -> dict[str, Any]:
    """The indexing metadata a literature row carries (lens input selection reads it)."""
    return {
        "publication_types": list(publication_types),
        "mesh_headings": [h.model_dump() for h in mesh_headings],
        "retracted": retracted,
        "mesh_indexed": indexed,
    }


def lacks_metadata(ev: Evidence) -> bool:
    """A PubMed row retrieved before publication types and MeSH headings were kept."""
    return ev.source.startswith("PMID:") and "publication_types" not in (ev.extra or {})


async def with_article_metadata(evidences: list[Evidence]) -> tuple[list[Evidence], int]:
    """Fill in publication types, MeSH headings and retraction status on rows that lack
    them, with one efetch request per 200 PMIDs; returns the rows and how many changed.

    A PMID efetch no longer returns (a withdrawn record) gets empty metadata, so it is
    not refetched on every run; a PMID whose batch failed is left as it was.
    """
    stale = [ev for ev in evidences if lacks_metadata(ev)]
    if not stale:
        return evidences, 0
    fetched = await fetch_article_metadata([ev.source.removeprefix("PMID:") for ev in stale])
    if not fetched:
        return evidences, 0
    updated: dict[Any, Evidence] = {}
    for ev in stale:
        pmid = ev.source.removeprefix("PMID:")
        meta = fetched.get(pmid) or ArticleMetadata()
        updated[ev.evidence_id] = ev.model_copy(
            update={
                "extra": {
                    **(ev.extra or {}),
                    **_metadata_extra(
                        meta.publication_types, meta.mesh_headings, meta.retracted, meta.indexed
                    ),
                }
            }
        )
    return [updated.get(ev.evidence_id, ev) for ev in evidences], len(updated)


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
            evidences.append(_to_evidence(r, msg, query, uri, gene, gene_id, disease, disease_id))

        if not spec.get("query"):  # an explicit query means "exactly this search"
            seen = {r.pmid for r in records}
            drugs = await known_drug_names(gene_id, disease, disease_id)
            role_queries = target_level_queries(gene, drugs)
            in_indication = indication_drug_trial_query(drugs, disease, disease_mesh)
            if in_indication:
                role_queries["indication_drug_trials"] = in_indication
            for role, role_query in role_queries.items():
                try:
                    extra = await search_pubmed(
                        role_query, max_results=_ROLE_MAX[role], sort="relevance"
                    )
                except Exception as exc:  # supplementary: never lose the main search
                    logger.warning("literature: %s query failed: %s", role, exc)
                    continue
                for r in extra:
                    if r.pmid in seen:
                        continue
                    seen.add(r.pmid)
                    uri = archive_raw(
                        gene, disease_id, direction, "papers", f"{r.pmid}.md", _render_markdown(r)
                    )
                    ev = _to_evidence(r, msg, role_query, uri, gene, gene_id, disease, disease_id)
                    ev.extra["retrieval_role"] = role
                    evidences.append(ev)
        return result_msg(msg, evidences)
