# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Omics data-acquisition agent.

Queries internal RNA-seq/expression data via the internal_data MCP server
(SENSITIVE) and GTEx/HPA tissue expression, SPOKE anatomy, Expression Atlas
disease-vs-control expression, and ENCODE regulatory-assay coverage via public
APIs (NON_SENSITIVE).

For each GTEx/HPA and Expression Atlas fetch the agent emits:
  • one archive-blob Evidence (artifact_uri → JSON on disk; claim_text="")
  • granular EXPRESSION Evidence rows — one per significant tissue, one for HPA
    tissue specificity, one for subcellular localisation, one per top
    differential-expression contrast — each carrying an atomic claim_text and a
    source_evidence_id back to the blob.

ENCODE regulatory-element coverage and SPOKE anatomy are single-row signals
(no per-locus breadth that warrants a blob/granular split) and are emitted as
one self-contained Evidence row each.
"""

from __future__ import annotations

import asyncio
import uuid

from agents._common import make_provenance, result_msg
from agents.retrieval.omics.contract import CONTRACT
from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from harness.base_agent import BaseAgent
from harness.context import RunContext
from mcp_servers.encode.tools import get_regulatory_coverage
from mcp_servers.expression_atlas.tools import (
    DifferentialExpressionBundle,
    get_differential_expression,
)
from mcp_servers.gtex.tools import ExpressionBundle, get_expression
from mcp_servers.internal_data.tools import query_internal_db
from mcp_servers.spoke.tools import get_anatomy_expression
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from schemas.messages import AgentMessage

_EXPRESSION_SQL = """
SELECT gene_symbol, tissue, tpm_median, tpm_q1, tpm_q3,
       sample_count, dataset_id, differential_expression_padj, log2_fold_change
FROM expression_data
WHERE gene_symbol = '{gene}'
  AND tissue ILIKE '%{tissue}%'
ORDER BY tpm_median DESC
LIMIT 100
"""

# Tissues of primary on-target toxicology significance.  Every tissue in this
# set always gets its own claim row regardless of its TPM rank.
_SAFETY_SENTINEL_TISSUES: frozenset[str] = frozenset(
    {
        "Heart_Left_Ventricle",
        "Heart_Atrial_Appendage",
        "Whole_Blood",
        "Liver",
        "Brain_Cortex",
        "Brain_Cerebellum",
        "Kidney_Cortex",
        "Lung",
        "Pancreas",
    }
)

# Number of highest-TPM tissues to always emit as individual claim rows.
_TOP_TISSUE_COUNT = 5


def _gtex_claim_evidences(
    bundle: ExpressionBundle,
    blob_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    gene: str,
    gene_id: str,
    disease: str,
    disease_id: str,
    prov: Provenance,
    expr_uri: str,
) -> list[Evidence]:
    """Emit granular EXPRESSION Evidence rows from a GTEx/HPA bundle.

    Each row has an atomic claim_text and a source_evidence_id linking it back
    to the archive blob.  Covers: HPA tissue specificity, subcellular
    localisation, top-N tissues by TPM, and all safety-sentinel tissues.
    """
    common: dict = dict(
        run_id=run_id,
        gene=gene,
        gene_id=gene_id,
        disease=disease,
        disease_id=disease_id,
        evidence_type=EvidenceType.EXPRESSION,
        scope="abstract",
        source_evidence_id=blob_id,
        artifact_uri=expr_uri,
        classification=DataClass.NON_SENSITIVE,
        provenance=prov,
    )
    results: list[Evidence] = []

    if bundle.hpa_tissue_specificity:
        results.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                source="hpa:rna_tissue_specificity",
                source_link=f"https://www.proteinatlas.org/{bundle.ensembl_id or gene}",
                claim_text=(
                    f"{gene} RNA tissue specificity: {bundle.hpa_tissue_specificity}"
                    " (Human Protein Atlas)."
                ),
                extra={"hpa_tissue_specificity": bundle.hpa_tissue_specificity},
                **common,
            )
        )

    if bundle.hpa_subcellular_location:
        locs = ", ".join(bundle.hpa_subcellular_location)
        uniprot_url = (
            f"https://www.uniprot.org/uniprotkb/{bundle.uniprot_accession}"
            if bundle.uniprot_accession
            else f"https://www.uniprot.org/uniprotkb?query=gene:{gene}+AND+organism_id:9606"
        )
        results.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                source="uniprot:subcellular_location",
                source_link=uniprot_url,
                claim_text=f"{gene} subcellular localization: {locs} (UniProt/HPA).",
                extra={"subcellular_location": bundle.hpa_subcellular_location},
                **common,
            )
        )

    emitted: set[str] = set()
    for i, t in enumerate(bundle.gtex_expressions):
        if (
            i < _TOP_TISSUE_COUNT or t.tissue in _SAFETY_SENTINEL_TISSUES
        ) and t.tissue not in emitted:
            results.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    source=f"gtex_v8:{t.tissue}",
                    source_link=bundle.source_link,
                    claim_text=(
                        f"{gene} GTEx v8 expression in {t.tissue}: {t.median_tpm:.1f} TPM median."
                    ),
                    extra={"tissue": t.tissue, "median_tpm": t.median_tpm},
                    **common,
                )
            )
            emitted.add(t.tissue)

    return results


def _expression_atlas_claim_evidences(
    bundle: DifferentialExpressionBundle,
    blob_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    gene: str,
    gene_id: str,
    disease: str,
    disease_id: str,
    prov: Provenance,
    expr_uri: str,
) -> list[Evidence]:
    """Emit granular EXPRESSION Evidence rows from an Expression Atlas bundle.

    One atomic claim_text per top differential-expression contrast, capped at
    `_SUMMARY_COUNT`-equivalent breadth; each carries a source_evidence_id
    linking it back to the archive blob, mirroring `_gtex_claim_evidences`.
    """
    common: dict = dict(
        run_id=run_id,
        gene=gene,
        gene_id=gene_id,
        disease=disease,
        disease_id=disease_id,
        evidence_type=EvidenceType.EXPRESSION,
        scope="abstract",
        source_evidence_id=blob_id,
        artifact_uri=expr_uri,
        classification=DataClass.NON_SENSITIVE,
        provenance=prov,
    )
    results: list[Evidence] = []
    for r in bundle.results[:5]:
        disease_clause = (
            f" (matched query disease '{bundle.disease}')" if bundle.disease_specific else ""
        )
        results.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                source=f"expression_atlas:{r.experiment_accession}",
                source_link=f"https://www.ebi.ac.uk/gxa/experiments/{r.experiment_accession}",
                claim_text=(
                    f"{gene} {r.regulation} {r.fold_change:+.1f}-fold (p={r.p_value:.2g}) in "
                    f"{r.comparison} [{r.experiment_accession}]{disease_clause} (Expression Atlas)."
                ),
                extra=r.model_dump(),
                **common,
            )
        )
    return results


class OmicsAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        gene = spec["target_gene"]
        gene_id = spec.get("gene_id") or ""
        disease = spec["disease"]
        disease_id = spec.get("disease_id") or ""
        direction = spec.get("direction") or "unspecified"
        tissue = spec.get("tissue", "")

        sql = _EXPRESSION_SQL.format(
            gene=gene,
            tissue=tissue.replace("'", "''") if tissue else "%",
        )

        # All five fetches below are mutually independent — run them concurrently
        # and let each degrade to None/[] on failure so one source outage doesn't
        # discard evidence already gathered from the others.

        async def _fetch_internal_rows():
            async with span(
                "query_internal_db:expression", trace_id=msg.trace_id, input_data=sql
            ) as sp:
                try:
                    result = await query_internal_db(sql)
                    sp.set_attribute("gen_ai.completion", f"{len(result)} rows returned")
                    return result
                except Exception as exc:
                    sp.set_attribute("error", str(exc))
                    return []

        async def _fetch_gtex():
            async with span("gtex:get_expression", trace_id=msg.trace_id, input_data=gene) as sp:
                try:
                    result = await get_expression(gene)
                    sp.set_attribute("gen_ai.completion", result.text)
                    return result
                except Exception as exc:
                    sp.set_attribute("error", str(exc))
                    return None

        async def _fetch_spoke_anatomy():
            async with span(
                "spoke:get_anatomy_expression", trace_id=msg.trace_id, input_data=gene
            ) as sp:
                try:
                    result = await get_anatomy_expression(gene)
                    sp.set_attribute("gen_ai.completion", result.text)
                    return result
                except Exception as exc:
                    sp.set_attribute("error", str(exc))
                    return None

        async def _fetch_expression_atlas():
            async with span(
                "expression_atlas:get_differential_expression",
                trace_id=msg.trace_id,
                input_data=gene,
            ) as sp:
                try:
                    result = await get_differential_expression(gene, disease=disease)
                    sp.set_attribute("gen_ai.completion", result.text)
                    return result
                except Exception as exc:
                    sp.set_attribute("error", str(exc))
                    return None

        async def _fetch_encode():
            async with span(
                "encode:get_regulatory_coverage", trace_id=msg.trace_id, input_data=gene
            ) as sp:
                try:
                    result = await get_regulatory_coverage(gene)
                    sp.set_attribute("gen_ai.completion", result.text)
                    return result
                except Exception as exc:
                    sp.set_attribute("error", str(exc))
                    return None

        rows, bundle, spoke_anatomy, ea_bundle, encode_bundle = await asyncio.gather(
            _fetch_internal_rows(),
            _fetch_gtex(),
            _fetch_spoke_anatomy(),
            _fetch_expression_atlas(),
            _fetch_encode(),
        )

        prov = make_provenance("omics", "query_internal_db", msg.trace_id)
        evidences: list[Evidence] = []
        for row in rows:
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    population=tissue or None,
                    evidence_type=EvidenceType.OMICS,
                    scope="abstract",
                    source=row.get("dataset_id", "internal"),
                    source_link=f"internal://expression/{row.get('dataset_id', 'unknown')}",
                    classification=DataClass.SENSITIVE,
                    provenance=prov,
                    extra={k: v for k, v in row.items() if k != "_classification"},
                )
            )

        # Public expression evidence from GTEx + HPA (NON_SENSITIVE).
        if bundle:
            expr_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "omics",
                f"{gene}_gtex_hpa.json",
                bundle.model_dump_json(indent=2),
            )
            gtex_prov = make_provenance("omics", "gtex.get_expression", msg.trace_id)

            # Archive blob — single record for provenance and the full JSON artifact.
            blob_id = uuid.uuid4()
            evidences.append(
                Evidence(
                    evidence_id=blob_id,
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    population=tissue or None,
                    evidence_type=EvidenceType.EXPRESSION,
                    scope="abstract",
                    source=f"gtex_hpa:{bundle.ensembl_id or gene}",
                    source_link=bundle.source_link,
                    artifact_uri=expr_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=gtex_prov,
                    extra=bundle.model_dump(),
                )
            )

            # Granular claim rows — one atomic claim_text each, linked to the blob.
            evidences.extend(
                _gtex_claim_evidences(
                    bundle,
                    blob_id,
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    prov=gtex_prov,
                    expr_uri=expr_uri,
                )
            )

        # SPOKE knowledge graph: Anatomy-Gene expression edges (NON_SENSITIVE — public,
        # no-auth API). SPOKE's UBERON anatomy terms have no crosswalk to GTEx tissue
        # codes, so this is kept as a single independently-labeled corroborating row
        # rather than merged into the GTEx granular rows above.
        if spoke_anatomy and spoke_anatomy.expressions:
            spoke_prov = make_provenance("omics", "spoke.get_anatomy_expression", msg.trace_id)
            names = sorted({e.anatomy_name for e in spoke_anatomy.expressions if e.anatomy_name})
            sample = ", ".join(names[:10])
            more = f" (+{len(names) - 10} more)" if len(names) > 10 else ""
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    population=tissue or None,
                    evidence_type=EvidenceType.EXPRESSION,
                    scope="abstract",
                    source=f"spoke_anatomy:{gene}",
                    source_link=spoke_anatomy.source_link,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=spoke_prov,
                    claim_text=(
                        f"SPOKE knowledge graph confirms {gene} expression in {len(names)} "
                        f"anatomical structure(s) (UBERON): {sample}{more}."
                    ),
                    extra={
                        "anatomy_names": names,
                        "edge_types": sorted({e.edge_type for e in spoke_anatomy.expressions}),
                    },
                )
            )

        # Expression Atlas: disease-vs-control differential expression (NON_SENSITIVE —
        # public, no-auth API). Fills the gap GTEx can't: GTEx is normal-tissue-only, so
        # this is the only source answering "is this gene dysregulated in <disease> vs.
        # control" rather than just "is it expressed in <tissue> generally."
        if ea_bundle and ea_bundle.results:
            ea_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "omics",
                f"{gene}_expression_atlas.json",
                ea_bundle.model_dump_json(indent=2),
            )
            ea_prov = make_provenance(
                "omics", "expression_atlas.get_differential_expression", msg.trace_id
            )
            blob_id = uuid.uuid4()
            evidences.append(
                Evidence(
                    evidence_id=blob_id,
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    population=tissue or None,
                    evidence_type=EvidenceType.EXPRESSION,
                    scope="abstract",
                    source=f"expression_atlas:{ea_bundle.ensembl_id or gene}",
                    source_link=ea_bundle.source_link,
                    artifact_uri=ea_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=ea_prov,
                    extra=ea_bundle.model_dump(),
                )
            )
            evidences.extend(
                _expression_atlas_claim_evidences(
                    ea_bundle,
                    blob_id,
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    prov=ea_prov,
                    expr_uri=ea_uri,
                )
            )

        # ENCODE: cis-regulatory assay coverage at the gene locus (NON_SENSITIVE —
        # public, no-auth API). True cCRE (PLS/ELS/CTCF) classification via SCREEN is
        # gated (403)/undocumented (500); this is the coarser but real fallback signal
        # — see mcp_servers/encode/tools.py docstring for the full investigation.
        if encode_bundle and encode_bundle.total_experiments:
            encode_prov = make_provenance("omics", "encode.get_regulatory_coverage", msg.trace_id)
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    population=tissue or None,
                    evidence_type=EvidenceType.REGULATORY_ELEMENT,
                    scope="abstract",
                    source=f"encode_region_search:{gene}",
                    source_link=encode_bundle.source_link,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=encode_prov,
                    claim_text=encode_bundle.text,
                    extra=encode_bundle.model_dump(),
                )
            )

        return result_msg(msg, evidences)
