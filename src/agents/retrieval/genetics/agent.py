# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Genetics data-acquisition agent.

Queries internal GWAS/LoF data via the internal_data MCP server (SENSITIVE),
gnomAD constraint via the public gnomAD GraphQL API (NON_SENSITIVE),
genome-wide significant associations via the EBI GWAS Catalog REST API (NON_SENSITIVE),
and OT Genetics Locus-to-Gene scores + eQTL/pQTL colocalizations via the Open
Targets Platform GraphQL API (NON_SENSITIVE).
"""

from __future__ import annotations

import json
import uuid

from agents._common import make_provenance, result_msg
from agents.retrieval.genetics.contract import CONTRACT
from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from harness.base_agent import BaseAgent
from harness.context import RunContext
from mcp_servers.clingen.tools import get_clingen_validity
from mcp_servers.gencc.tools import get_gencc_validity
from mcp_servers.gnomad.tools import get_clinvar_variants, get_constraint, get_lof_variants
from mcp_servers.gwas_catalog.tools import get_gwas_associations
from mcp_servers.internal_data.tools import query_internal_db
from mcp_servers.omim.tools import get_omim_validity, omim_configured
from mcp_servers.ontology.tools import get_gene_phenotypes
from mcp_servers.opentargets.tools import (
    get_colocalizations,
    get_disease_descendants,
    get_l2g_scores,
)
from mcp_servers.orphanet.tools import get_orphanet_associations
from mcp_servers.spoke.tools import get_gene_disease_associations
from schemas.evidence import DataClass, Evidence, EvidenceType
from schemas.messages import AgentMessage

_COLOC_BASE = "https://platform.opentargets.org"

# EFO therapeutic-area IDs that classify a disease as oncology.
_ONCOLOGY_AREA_IDS = frozenset(
    {
        "MONDO_0045024",  # cancer or benign tumor
        "EFO_0000616",  # neoplasm
        "EFO_0005803",  # malignant neoplasm of the digestive system (example)
    }
)

_GWAS_SQL = """
SELECT gene_symbol, trait, pvalue, beta, odds_ratio, study_id, variant_id,
       lof_score, is_lof_intolerant
FROM gwas_hits
WHERE gene_symbol = '{gene}' AND trait ILIKE '%{disease}%'
ORDER BY pvalue ASC
LIMIT 200
"""


class GeneticsAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        gene = spec["target_gene"]
        gene_id = spec.get("gene_id") or ""
        disease = spec["disease"]
        disease_id = spec.get("disease_id") or ""
        direction = spec.get("direction") or "unspecified"

        # Resolve EFO ontology once so GWAS/coloc sources can be disease-scoped.
        onto = None
        if disease_id:
            async with span(
                "opentargets:get_disease_descendants", trace_id=msg.trace_id, input_data=disease_id
            ) as onto_span:
                onto = await get_disease_descendants(disease_id)
                onto_span.set_attribute(
                    "gen_ai.completion",
                    f"{len(onto.efo_ids)} EFO ids, {len(onto.therapeutic_areas)} therapeutic areas",
                )

        is_oncology = bool(onto and onto.therapeutic_areas & _ONCOLOGY_AREA_IDS)
        trait_terms = [disease]

        sql = _GWAS_SQL.format(gene=gene, disease=disease.replace("'", "''"))
        async with span("query_internal_db:gwas", trace_id=msg.trace_id, input_data=sql) as db_span:
            rows = await query_internal_db(sql)
            db_span.set_attribute("gen_ai.completion", f"{len(rows)} rows returned")

        # Archive the full query result set as a single JSON file; each
        # Evidence row below points to this shared archive file.
        archive_rows = [{k: v for k, v in r.items() if k != "_classification"} for r in rows]
        uri = archive_raw(
            gene,
            disease_id,
            direction,
            "genetics",
            f"{gene}_internal_gwas.json",
            json.dumps(archive_rows, indent=2, default=str),
        )

        prov = make_provenance("genetics", "query_internal_db", msg.trace_id)
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
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=row.get("study_id", "internal"),
                    source_link=f"internal://gwas/{row.get('study_id', 'unknown')}",
                    artifact_uri=uri,
                    classification=DataClass.SENSITIVE,
                    provenance=prov,
                    extra={k: v for k, v in row.items() if k != "_classification"},
                )
            )
        # Public constraint evidence from gnomAD (NON_SENSITIVE — may route to cloud).
        async with span("gnomad:get_constraint", trace_id=msg.trace_id, input_data=gene) as gs:
            bundle = await get_constraint(gene, ensembl_id=gene_id)
            gs.set_attribute("gen_ai.completion", bundle.text)

        c_uri = archive_raw(
            gene,
            disease_id,
            direction,
            "genetics",
            f"{gene}_gnomad.json",
            bundle.model_dump_json(indent=2),
        )
        gnomad_prov = make_provenance("genetics", "gnomad.get_constraint", msg.trace_id)
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=msg.run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.CONSTRAINT,
                scope="abstract",
                source=f"gnomad:{bundle.ensembl_id or gene}",
                source_link=bundle.source_link,
                artifact_uri=c_uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=gnomad_prov,
                extra=bundle.model_dump(),
            )
        )

        # ClinVar variants and observed pLoF variants require an Ensembl ID.
        if bundle.ensembl_id:
            async with span(
                "gnomad:get_clinvar_variants", trace_id=msg.trace_id, input_data=bundle.ensembl_id
            ) as cvs:
                cv_bundle = await get_clinvar_variants(bundle.ensembl_id, gene)
                cvs.set_attribute("gen_ai.completion", cv_bundle.text)

            cv_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_gnomad_clinvar.json",
                cv_bundle.model_dump_json(indent=2),
            )
            cv_prov = make_provenance("genetics", "gnomad.get_clinvar_variants", msg.trace_id)
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.CONSTRAINT,
                    scope="abstract",
                    source=f"gnomad_clinvar:{bundle.ensembl_id}",
                    source_link=bundle.source_link,
                    artifact_uri=cv_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=cv_prov,
                    extra=cv_bundle.model_dump(),
                )
            )

            async with span(
                "gnomad:get_lof_variants", trace_id=msg.trace_id, input_data=bundle.ensembl_id
            ) as lvs:
                lof_bundle = await get_lof_variants(bundle.ensembl_id, gene)
                lvs.set_attribute("gen_ai.completion", lof_bundle.text)

            lof_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_gnomad_lof_variants.json",
                lof_bundle.model_dump_json(indent=2),
            )
            lof_prov = make_provenance("genetics", "gnomad.get_lof_variants", msg.trace_id)
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.CONSTRAINT,
                    scope="abstract",
                    source=f"gnomad_lof:{bundle.ensembl_id}",
                    source_link=bundle.source_link,
                    artifact_uri=lof_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=lof_prov,
                    extra=lof_bundle.model_dump(),
                )
            )

        # Public GWAS associations from EBI GWAS Catalog (NON_SENSITIVE — may route to cloud).
        # Disease-scoped via EFO descendant set + trait-term substring fallback.
        async with span(
            "gwas_catalog:get_gwas_associations", trace_id=msg.trace_id, input_data=gene
        ) as gs:
            gwas_bundle = await get_gwas_associations(
                gene,
                efo_ids=onto.efo_ids if onto else None,
                trait_terms=trait_terms,
            )
            gs.set_attribute("gen_ai.completion", gwas_bundle.text)
            gs.set_attribute("dropped_off_target", gwas_bundle.dropped_off_target)

        gwas_uri = archive_raw(
            gene,
            disease_id,
            direction,
            "genetics",
            f"{gene}_gwas_catalog.json",
            gwas_bundle.model_dump_json(indent=2),
        )
        gwas_prov = make_provenance("genetics", "gwas_catalog.get_gwas_associations", msg.trace_id)

        # Deduplicate by study accession: keep only the lead (lowest-p) hit per study.
        seen_studies: dict[str, bool] = {}
        for hit in gwas_bundle.hits:
            acc = hit.study_accession or hit.association_id
            if acc in seen_studies:
                continue
            seen_studies[acc] = True
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=f"gwas_catalog:{hit.study_accession}",
                    source_link=f"https://www.ebi.ac.uk/gwas/studies/{hit.study_accession}",
                    artifact_uri=gwas_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=gwas_prov,
                    extra=hit.model_dump(),
                )
            )

        # Emit a trait-breadth summary row whenever off-target hits were suppressed.
        if gwas_bundle.dropped_off_target > 0:
            off_traits = [
                t for t in gwas_bundle.all_traits if t not in set(gwas_bundle.kept_traits)
            ]
            off_sample = ", ".join(off_traits[:5])
            breadth_text = (
                f"{gene} locus: {len(gwas_bundle.all_traits)} distinct GWAS traits found "
                f"({len(gwas_bundle.hits)} matched {disease}). "
                f"Excluded as off-indication: {off_sample or 'various'}."
            )
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source="gwas_catalog:locus_breadth_summary",
                    source_link=f"https://www.ebi.ac.uk/gwas/genes/{gene}",
                    artifact_uri=gwas_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=gwas_prov,
                    extra={
                        "summary": breadth_text,
                        "all_traits": gwas_bundle.all_traits,
                        "kept_traits": gwas_bundle.kept_traits,
                        "dropped_off_target": gwas_bundle.dropped_off_target,
                        "is_oncology": is_oncology,
                    },
                )
            )

        if gene_id:
            # OT Genetics: Locus-to-Gene scores require both Ensembl ID and disease ID.
            if disease_id:
                async with span(
                    "opentargets:get_l2g_scores", trace_id=msg.trace_id, input_data=gene_id
                ) as ls:
                    l2g_bundle = await get_l2g_scores(gene_id, disease_id)
                    ls.set_attribute("gen_ai.completion", l2g_bundle.text)

                l2g_uri = archive_raw(
                    gene,
                    disease_id,
                    direction,
                    "genetics",
                    f"{gene}_ot_l2g.json",
                    l2g_bundle.model_dump_json(indent=2),
                )
                l2g_prov = make_provenance("genetics", "opentargets.get_l2g_scores", msg.trace_id)
                for hit in l2g_bundle.hits:
                    evidences.append(
                        Evidence(
                            evidence_id=uuid.uuid4(),
                            run_id=msg.run_id,
                            gene=gene,
                            gene_id=gene_id,
                            disease=disease,
                            disease_id=disease_id,
                            evidence_type=EvidenceType.GENETICS,
                            scope="abstract",
                            source=f"ot_genetics_l2g:{hit.study_locus_id}",
                            source_link=hit.source_link,
                            artifact_uri=l2g_uri,
                            classification=DataClass.NON_SENSITIVE,
                            provenance=l2g_prov,
                            extra=hit.model_dump(),
                        )
                    )

            # OT Genetics: eQTL/pQTL ↔ GWAS colocalizations, disease-scoped.
            async with span(
                "opentargets:get_colocalizations", trace_id=msg.trace_id, input_data=gene_id
            ) as cs:
                coloc_bundle = await get_colocalizations(
                    gene_id,
                    efo_ids=onto.efo_ids if onto else None,
                    trait_terms=trait_terms,
                )
                cs.set_attribute("gen_ai.completion", coloc_bundle.text)
                cs.set_attribute("dropped_off_target", coloc_bundle.dropped_off_target)

            coloc_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_ot_coloc.json",
                coloc_bundle.model_dump_json(indent=2),
            )
            coloc_prov = make_provenance(
                "genetics", "opentargets.get_colocalizations", msg.trace_id
            )
            for hit in coloc_bundle.hits:
                evidences.append(
                    Evidence(
                        evidence_id=uuid.uuid4(),
                        run_id=msg.run_id,
                        gene=gene,
                        gene_id=gene_id,
                        disease=disease,
                        disease_id=disease_id,
                        evidence_type=EvidenceType.GENETICS,
                        scope="abstract",
                        source=f"ot_genetics_coloc:{hit.qtl_study_id}",
                        source_link=hit.source_link,
                        artifact_uri=coloc_uri,
                        classification=DataClass.NON_SENSITIVE,
                        provenance=coloc_prov,
                        extra=hit.model_dump(),
                    )
                )

            # Emit coloc breadth summary row when off-target hits were suppressed.
            if coloc_bundle.dropped_off_target > 0:
                coloc_off = [
                    t for t in coloc_bundle.all_traits if t not in set(coloc_bundle.kept_traits)
                ]
                coloc_off_sample = ", ".join(coloc_off[:5])
                coloc_breadth_text = (
                    f"{gene} coloc: {len(coloc_bundle.all_traits)} distinct GWAS traits found "
                    f"({len(coloc_bundle.hits)} matched {disease}). "
                    f"Excluded as off-indication: {coloc_off_sample or 'various'}."
                )
                evidences.append(
                    Evidence(
                        evidence_id=uuid.uuid4(),
                        run_id=msg.run_id,
                        gene=gene,
                        gene_id=gene_id,
                        disease=disease,
                        disease_id=disease_id,
                        evidence_type=EvidenceType.GENETICS,
                        scope="abstract",
                        source="ot_genetics_coloc:locus_breadth_summary",
                        source_link=f"{_COLOC_BASE}/target/{gene_id}",
                        artifact_uri=coloc_uri,
                        classification=DataClass.NON_SENSITIVE,
                        provenance=coloc_prov,
                        extra={
                            "summary": coloc_breadth_text,
                            "all_traits": coloc_bundle.all_traits,
                            "kept_traits": coloc_bundle.kept_traits,
                            "dropped_off_target": coloc_bundle.dropped_off_target,
                            "is_oncology": is_oncology,
                        },
                    )
                )

        # ClinGen gene-disease validity (NON_SENSITIVE — public API, no key required).
        async with span(
            "clingen:get_clingen_validity", trace_id=msg.trace_id, input_data=gene
        ) as cg_span:
            try:
                clingen_bundle = await get_clingen_validity(gene)
                cg_span.set_attribute("gen_ai.completion", clingen_bundle.text)
            except Exception as exc:
                cg_span.set_attribute("error", str(exc))
                clingen_bundle = None

        if clingen_bundle and clingen_bundle.associations:
            cg_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_clingen.json",
                clingen_bundle.model_dump_json(indent=2),
            )
            cg_prov = make_provenance("genetics", "clingen.get_clingen_validity", msg.trace_id)
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=f"clingen:{gene}",
                    source_link=f"https://search.clinicalgenome.org/kb/genes?search={gene}",
                    artifact_uri=cg_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=cg_prov,
                    extra={
                        "summary": clingen_bundle.text,
                        "associations": [a.model_dump() for a in clingen_bundle.associations],
                        "total": clingen_bundle.total,
                    },
                )
            )

        # OMIM Mendelian phenotype-gene associations (NON_SENSITIVE — public API,
        # requires a free academic OMIM_API_KEY; non-commercial-licensed, so gated
        # behind OMIM_ENABLED). Skipped entirely (no call, no span) unless OMIM is
        # both opted in and keyed, rather than erroring per run.
        omim_bundle = None
        if omim_configured():
            async with span(
                "omim:get_omim_validity", trace_id=msg.trace_id, input_data=gene
            ) as om_span:
                try:
                    omim_bundle = await get_omim_validity(gene)
                    om_span.set_attribute("gen_ai.completion", omim_bundle.text)
                except Exception as exc:
                    om_span.set_attribute("error", str(exc))
                    omim_bundle = None

        if omim_bundle and omim_bundle.associations:
            omim_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_omim.json",
                omim_bundle.model_dump_json(indent=2),
            )
            omim_prov = make_provenance("genetics", "omim.get_omim_validity", msg.trace_id)
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=f"omim:{gene}",
                    source_link=f"https://omim.org/search?search={gene}",
                    artifact_uri=omim_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=omim_prov,
                    extra={
                        "summary": omim_bundle.text,
                        "associations": [a.model_dump() for a in omim_bundle.associations],
                        "total": omim_bundle.total,
                    },
                )
            )

        # GenCC per-submitter gene-disease validity (NON_SENSITIVE — public, no-auth
        # bulk export). Surfaces agreement/disagreement across independent curation
        # bodies (ClinGen among them) for the same gene-disease pair.
        async with span(
            "gencc:get_gencc_validity", trace_id=msg.trace_id, input_data=gene
        ) as gc_span:
            try:
                gencc_bundle = await get_gencc_validity(gene)
                gc_span.set_attribute("gen_ai.completion", gencc_bundle.text)
            except Exception as exc:
                gc_span.set_attribute("error", str(exc))
                gencc_bundle = None

        if gencc_bundle and gencc_bundle.associations:
            gencc_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_gencc.json",
                gencc_bundle.model_dump_json(indent=2),
            )
            gencc_prov = make_provenance("genetics", "gencc.get_gencc_validity", msg.trace_id)
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=f"gencc:{gene}",
                    source_link=f"https://search.thegencc.org/genes?search={gene}",
                    artifact_uri=gencc_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=gencc_prov,
                    extra={
                        "summary": gencc_bundle.text,
                        "associations": [a.model_dump() for a in gencc_bundle.associations],
                        "total": gencc_bundle.total,
                    },
                )
            )

        # Orphanet rare-disease gene associations (NON_SENSITIVE — public, no-auth
        # bulk dataset). Carries an explicit causal vs. susceptibility/modifier
        # relationship type, finer-grained than a single validity classification.
        async with span(
            "orphanet:get_orphanet_associations", trace_id=msg.trace_id, input_data=gene
        ) as or_span:
            try:
                orphanet_bundle = await get_orphanet_associations(gene)
                or_span.set_attribute("gen_ai.completion", orphanet_bundle.text)
            except Exception as exc:
                or_span.set_attribute("error", str(exc))
                orphanet_bundle = None

        if orphanet_bundle and orphanet_bundle.associations:
            orphanet_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_orphanet.json",
                orphanet_bundle.model_dump_json(indent=2),
            )
            orphanet_prov = make_provenance(
                "genetics", "orphanet.get_orphanet_associations", msg.trace_id
            )
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=f"orphanet:{gene}",
                    source_link="https://www.orphadata.com",
                    artifact_uri=orphanet_uri,
                    classification=DataClass.NON_SENSITIVE,
                    provenance=orphanet_prov,
                    extra={
                        "summary": orphanet_bundle.text,
                        "associations": [a.model_dump() for a in orphanet_bundle.associations],
                        "total": orphanet_bundle.total,
                    },
                )
            )

        # SPOKE knowledge graph: Disease-ASSOCIATES-Gene edges (NON_SENSITIVE — public,
        # no-auth API). Disease-scoped by substring match against `trait_terms`, mirroring
        # the EFO/trait_terms fallback already used for gwas_catalog — SPOKE Disease nodes
        # carry Disease Ontology ids (DOID:*), not EFO/MONDO, so no ID crosswalk is possible.
        async with span(
            "spoke:get_gene_disease_associations", trace_id=msg.trace_id, input_data=gene
        ) as sp_span:
            try:
                spoke_bundle = await get_gene_disease_associations(gene)
                sp_span.set_attribute("gen_ai.completion", spoke_bundle.text)
            except Exception as exc:
                sp_span.set_attribute("error", str(exc))
                spoke_bundle = None

        if spoke_bundle and spoke_bundle.associations:
            spoke_uri = archive_raw(
                gene,
                disease_id,
                direction,
                "genetics",
                f"{gene}_spoke.json",
                spoke_bundle.model_dump_json(indent=2),
            )
            spoke_prov = make_provenance(
                "genetics", "spoke.get_gene_disease_associations", msg.trace_id
            )
            terms = [t.lower() for t in trait_terms]
            for assoc in spoke_bundle.associations:
                if not any(term in assoc.disease_name.lower() for term in terms):
                    continue
                sources_str = ", ".join(assoc.edge_sources) or "unknown source"
                gwas_p_str = f"{assoc.gwas_pvalue:.2e}" if assoc.gwas_pvalue is not None else "n/a"
                score_str = (
                    f"{assoc.diseases_score:.3f}" if assoc.diseases_score is not None else "n/a"
                )
                assoc_text = (
                    f"SPOKE graph: {gene}–{assoc.disease_name} association via "
                    f"{sources_str}; gwas_p={gwas_p_str}, diseases_score={score_str}."
                )
                evidences.append(
                    Evidence(
                        evidence_id=uuid.uuid4(),
                        run_id=msg.run_id,
                        gene=gene,
                        gene_id=gene_id,
                        disease=disease,
                        disease_id=disease_id,
                        evidence_type=EvidenceType.GENETICS,
                        scope="abstract",
                        source=f"spoke:{assoc.disease_identifier or assoc.disease_name}",
                        source_link=spoke_bundle.source_link,
                        artifact_uri=spoke_uri,
                        classification=DataClass.NON_SENSITIVE,
                        provenance=spoke_prov,
                        extra={**assoc.model_dump(), "text": assoc_text},
                    )
                )

        # HPO/Monarch phenotype breadth + inheritance-mode fallback (NON_SENSITIVE —
        # public, no-auth API). Inheritance mode prefers ClinGen (gold standard,
        # already retrieved above) and falls back to HPO/Monarch annotation terms.
        async with span(
            "ontology:get_gene_phenotypes", trace_id=msg.trace_id, input_data=gene
        ) as ph_span:
            try:
                phenotype_bundle = await get_gene_phenotypes(gene)
                ph_span.set_attribute("gen_ai.completion", phenotype_bundle.text)
            except Exception as exc:
                ph_span.set_attribute("error", str(exc))
                phenotype_bundle = None

        clingen_moi = None
        clingen_moi_curie = None
        if clingen_bundle and clingen_bundle.associations:
            top = clingen_bundle.associations[0]
            clingen_moi, clingen_moi_curie = top.mode_of_inheritance, top.mode_of_inheritance_curie

        if clingen_moi:
            inheritance_mode, inheritance_mode_source = clingen_moi, "ClinGen"
        elif phenotype_bundle and phenotype_bundle.inheritance_modes:
            inheritance_mode, inheritance_mode_source = (
                phenotype_bundle.inheritance_modes[0],
                "HPO/Monarch",
            )
        else:
            inheritance_mode, inheritance_mode_source = None, None

        hpo_phenotype_count = phenotype_bundle.phenotype_count if phenotype_bundle else 0
        hpo_specificity_band = phenotype_bundle.specificity_band if phenotype_bundle else "unknown"
        hpo_top_phenotypes = phenotype_bundle.top_phenotypes if phenotype_bundle else []

        if inheritance_mode or hpo_phenotype_count:
            text_parts = []
            if inheritance_mode:
                text_parts.append(
                    f"Mode of inheritance: {inheritance_mode} (source: {inheritance_mode_source})"
                )
            if hpo_phenotype_count:
                text_parts.append(
                    f"HPO phenotype breadth: {hpo_phenotype_count} phenotype(s) ({hpo_specificity_band}); "
                    f"top terms: {', '.join(hpo_top_phenotypes)}"
                )
            ontology_text = f"Ontology constraints for {gene}: " + "; ".join(text_parts) + "."

            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.GENETICS,
                    scope="abstract",
                    source=f"ontology:{gene}",
                    source_link="https://api.monarchinitiative.org",
                    classification=DataClass.NON_SENSITIVE,
                    provenance=make_provenance(
                        "genetics", "ontology.get_gene_phenotypes", msg.trace_id
                    ),
                    extra={
                        "inheritance_mode": inheritance_mode,
                        "inheritance_mode_source": inheritance_mode_source,
                        "inheritance_mode_curie": clingen_moi_curie,
                        "hpo_phenotype_count": hpo_phenotype_count,
                        "hpo_specificity_band": hpo_specificity_band,
                        "hpo_top_phenotypes": hpo_top_phenotypes,
                        "text": ontology_text,
                    },
                )
            )

        return result_msg(msg, evidences)
