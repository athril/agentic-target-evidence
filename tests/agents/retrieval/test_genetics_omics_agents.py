# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GeneticsAgent (MP-34) and OmicsAgent (MP-35)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.retrieval.genetics.agent import GeneticsAgent
from agents.retrieval.omics.agent import OmicsAgent
from mcp_servers.clingen.tools import ClinGenAssociation, ClinGenBundle
from mcp_servers.encode.tools import RegulatoryCoverageBundle
from mcp_servers.expression_atlas.tools import DifferentialExpressionBundle
from mcp_servers.gencc.tools import GenCCAssociation, GenCCBundle
from mcp_servers.gnomad.tools import ClinVarBundle, ConstraintBundle, LofVariantBundle
from mcp_servers.gtex.tools import ExpressionBundle, TissueExpression
from mcp_servers.gwas_catalog.tools import GWASBundle, GWASHit
from mcp_servers.omim.tools import OmimAssociation, OmimBundle
from mcp_servers.ontology.tools import GenePhenotypeBundle
from mcp_servers.opentargets.tools import ColocBundle, DiseaseOntology, L2GBundle
from mcp_servers.orphanet.tools import (
    OrphanetAssociation,
    OrphanetBundle,
    OrphanetPrevalence,
    OrphanetPrevalenceBundle,
)
from mcp_servers.spoke.tools import (
    AnatomyExpressionBundle,
    GeneDiseaseBundle,
    SpokeAnatomyExpression,
    SpokeGeneDiseaseAssociation,
)
from schemas.evidence import DataClass, EvidenceType
from tests.agents.conftest import make_task_msg

_GWAS_ROWS = [
    {
        "gene_symbol": "BRCA1",
        "trait": "breast cancer",
        "pvalue": 1e-10,
        "study_id": "GCST001",
        "variant_id": "rs123",
        "_classification": "SENSITIVE",
    },
    {
        "gene_symbol": "BRCA1",
        "trait": "breast cancer",
        "pvalue": 5e-8,
        "study_id": "GCST002",
        "variant_id": "rs456",
        "_classification": "SENSITIVE",
    },
]

_GWAS_CATALOG_BUNDLE = GWASBundle(
    gene_symbol="BRCA1",
    hits=[],
    source_link="https://www.ebi.ac.uk/gwas",
    text="No GWAS catalog hits.",
)

_COLOC_BUNDLE = ColocBundle(
    gene_id="",
    hits=[],
    text="No colocalizations.",
)

_L2G_BUNDLE = L2GBundle(
    gene_id="",
    disease_id="",
    hits=[],
    text="No L2G evidence.",
)

_EXPR_ROWS = [
    {
        "gene_symbol": "BRCA1",
        "tissue": "breast",
        "tpm_median": 12.5,
        "dataset_id": "GTEx_v8",
        "_classification": "SENSITIVE",
    },
]

_GNOMAD_BUNDLE = ConstraintBundle(
    gene_symbol="BRCA1",
    ensembl_id="ENSG00000012048",
    loeuf=0.12,
    pli=0.99,
    source_link="https://gnomad.broadinstitute.org/gene/ENSG00000012048",
    text="gnomAD LOEUF=0.12, pLI=0.99.",
)

_CLINVAR_BUNDLE = ClinVarBundle(
    gene_symbol="BRCA1",
    ensembl_id="ENSG00000012048",
    total_clinvar=5,
    text="ClinVar variants in BRCA1 (via gnomAD): 2 Pathogenic out of 5 total.",
)

_LOF_BUNDLE = LofVariantBundle(
    gene_symbol="BRCA1",
    ensembl_id="ENSG00000012048",
    hc_lof_count=10,
    text="gnomAD v4: 10 HC pLoF variants observed in BRCA1.",
)

_GTEX_BUNDLE = ExpressionBundle(
    gene_symbol="BRCA1",
    ensembl_id="ENSG00000012048",
    gtex_expressions=[
        TissueExpression(tissue="Breast_Mammary_Tissue", median_tpm=28.7),
        TissueExpression(tissue="Ovary", median_tpm=20.1),
        TissueExpression(tissue="Heart_Left_Ventricle", median_tpm=8.3),  # sentinel
    ],
    hpa_tissue_specificity="Tissue enhanced (breast)",
    hpa_subcellular_location=["Nucleus", "Cytoplasm"],
    source_link="https://gtexportal.org/home/gene/BRCA1",
    text="GTEx top tissues: Breast_Mammary_Tissue=28.7. HPA specificity: Tissue enhanced (breast). Subcellular: Nucleus, Cytoplasm.",
)

_SPOKE_ANATOMY_EMPTY = AnatomyExpressionBundle(
    gene_symbol="BRCA1",
    expressions=[],
    source_link="https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/BRCA1",
    text="SPOKE: 0 anatomy expression edge(s) for BRCA1.",
)

_EXPR_ATLAS_BUNDLE_EMPTY = DifferentialExpressionBundle(
    gene_symbol="BRCA1",
    text="Expression Atlas: no differential expression data found for BRCA1.",
)

_ENCODE_BUNDLE_EMPTY = RegulatoryCoverageBundle(
    gene_symbol="BRCA1",
    text="ENCODE: no regulatory-assay coverage found for BRCA1.",
)


# ---------------------------------------------------------------------------
# GeneticsAgent
# ---------------------------------------------------------------------------

_DEFAULT_ONTO = DiseaseOntology(efo_ids={"EFO_0000305"}, therapeutic_areas=set())

_SPOKE_BUNDLE_EMPTY = GeneDiseaseBundle(
    gene_symbol="BRCA1",
    associations=[],
    source_link="https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/BRCA1",
    text="SPOKE: 0 disease association edge(s) for BRCA1.",
)

_CLINGEN_BUNDLE_EMPTY = ClinGenBundle(
    gene_symbol="BRCA1",
    associations=[],
    text="No ClinGen gene-disease validity assertions found for BRCA1.",
)

_PHENOTYPE_BUNDLE_EMPTY = GenePhenotypeBundle(
    gene_symbol="BRCA1",
    text="No HPO phenotype data found for BRCA1.",
)

_GENCC_BUNDLE_EMPTY = GenCCBundle(
    gene_symbol="BRCA1",
    associations=[],
    text="No GenCC gene-disease validity classifications found for BRCA1.",
)

_ORPHANET_BUNDLE_EMPTY = OrphanetBundle(
    gene_symbol="BRCA1",
    associations=[],
    text="No Orphanet gene-disease associations found for BRCA1.",
)

_ORPHANET_PREVALENCE_BUNDLE_EMPTY = OrphanetPrevalenceBundle(
    orphacodes=[],
    records=[],
    text="No Orphanet prevalence records found for OrphaCode(s) none provided.",
)

_OMIM_BUNDLE_EMPTY = OmimBundle(
    gene_symbol="BRCA1",
    associations=[],
    text="OMIM_API_KEY not configured — OMIM source skipped.",
)

_GENETICS_PATCHES = {
    "agents.retrieval.genetics.agent.query_internal_db": AsyncMock(return_value=_GWAS_ROWS),
    "agents.retrieval.genetics.agent.get_constraint": AsyncMock(return_value=_GNOMAD_BUNDLE),
    "agents.retrieval.genetics.agent.get_clinvar_variants": AsyncMock(return_value=_CLINVAR_BUNDLE),
    "agents.retrieval.genetics.agent.get_lof_variants": AsyncMock(return_value=_LOF_BUNDLE),
    "agents.retrieval.genetics.agent.get_gwas_associations": AsyncMock(
        return_value=_GWAS_CATALOG_BUNDLE
    ),
    "agents.retrieval.genetics.agent.get_colocalizations": AsyncMock(return_value=_COLOC_BUNDLE),
    "agents.retrieval.genetics.agent.get_l2g_scores": AsyncMock(return_value=_L2G_BUNDLE),
    "agents.retrieval.genetics.agent.get_disease_descendants": AsyncMock(
        return_value=_DEFAULT_ONTO
    ),
    "agents.retrieval.genetics.agent.get_gene_disease_associations": AsyncMock(
        return_value=_SPOKE_BUNDLE_EMPTY
    ),
    "agents.retrieval.genetics.agent.get_clingen_validity": AsyncMock(
        return_value=_CLINGEN_BUNDLE_EMPTY
    ),
    "agents.retrieval.genetics.agent.get_gene_phenotypes": AsyncMock(
        return_value=_PHENOTYPE_BUNDLE_EMPTY
    ),
    "agents.retrieval.genetics.agent.get_gencc_validity": AsyncMock(
        return_value=_GENCC_BUNDLE_EMPTY
    ),
    "agents.retrieval.genetics.agent.get_orphanet_associations": AsyncMock(
        return_value=_ORPHANET_BUNDLE_EMPTY
    ),
    "agents.retrieval.genetics.agent.get_orphanet_prevalence": AsyncMock(
        return_value=_ORPHANET_PREVALENCE_BUNDLE_EMPTY
    ),
    "agents.retrieval.genetics.agent.get_omim_validity": AsyncMock(return_value=_OMIM_BUNDLE_EMPTY),
}


async def test_genetics_agent_returns_sensitive_and_constraint_evidence(run_id, trace_id, ctx):
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    patches = {**_GENETICS_PATCHES}
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    assert result.intent == "result"
    sensitive = [e for e in result.payload if e.classification == DataClass.SENSITIVE]
    non_sensitive = [e for e in result.payload if e.classification == DataClass.NON_SENSITIVE]
    assert len(sensitive) == 2
    constraint = [e for e in non_sensitive if e.evidence_type == EvidenceType.CONSTRAINT]
    assert len(constraint) == 3  # constraint + clinvar + lof_variants


async def test_genetics_agent_excludes_classification_key_from_extra(run_id, trace_id, ctx):
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with patch.multiple(
        "agents.retrieval.genetics.agent",
        **{k.split(".")[-1]: v for k, v in _GENETICS_PATCHES.items()},
    ):
        result = await GeneticsAgent().run(msg, ctx)

    for ev in result.payload:
        assert "_classification" not in ev.extra


async def test_genetics_agent_empty_internal_rows_still_returns_constraint(run_id, trace_id, ctx):
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.query_internal_db": AsyncMock(return_value=[]),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    constraint = [e for e in result.payload if e.evidence_type == EvidenceType.CONSTRAINT]
    assert len(constraint) == 3  # constraint + clinvar + lof_variants


async def test_genetics_agent_deduplicates_gwas_by_study(run_id, trace_id, ctx):
    """Multiple hits from the same study accession should produce a single Evidence row."""

    def _hit(accession: str, pvalue: float) -> GWASHit:
        return GWASHit(
            association_id=f"id_{accession}",
            rs_id="rs123",
            pvalue=pvalue,
            pvalue_mantissa=1,
            pvalue_exponent=-9,
            trait="breast cancer",
            efo_id="EFO_0000305",
            study_accession=accession,
        )

    dup_bundle = GWASBundle(
        gene_symbol="BRCA1",
        hits=[_hit("GCST001", 1e-10), _hit("GCST001", 5e-9)],  # same study twice
        source_link="https://www.ebi.ac.uk/gwas",
        text="2 hits.",
    )
    msg = make_task_msg(
        "genetics",
        {"target_gene": "BRCA1", "disease": "breast cancer", "disease_id": "EFO_0000305"},
        run_id,
        trace_id,
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_gwas_associations": AsyncMock(return_value=dup_bundle),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    gwas_catalog_evs = [
        e
        for e in result.payload
        if "gwas_catalog:" in (e.source or "") and "breadth" not in (e.source or "")
    ]
    # Both hits share GCST001 → only one Evidence row
    assert len(gwas_catalog_evs) == 1
    assert gwas_catalog_evs[0].source == "gwas_catalog:GCST001"


async def test_genetics_agent_emits_breadth_summary_when_hits_dropped(run_id, trace_id, ctx):
    """When dropped_off_target > 0, a trait-breadth summary Evidence row must be emitted."""
    pleiotropic_bundle = GWASBundle(
        gene_symbol="PRMT5",
        hits=[],
        source_link="https://www.ebi.ac.uk/gwas/genes/PRMT5",
        text="0 matched.",
        dropped_off_target=183,
        all_traits=["height", "blood cell count", "educational attainment"],
        kept_traits=[],
    )
    msg = make_task_msg(
        "genetics",
        {"target_gene": "PRMT5", "disease": "pancreatic neoplasm", "disease_id": "EFO_0003860"},
        run_id,
        trace_id,
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.query_internal_db": AsyncMock(return_value=[]),
        "agents.retrieval.genetics.agent.get_gwas_associations": AsyncMock(
            return_value=pleiotropic_bundle
        ),
        "agents.retrieval.genetics.agent.get_disease_descendants": AsyncMock(
            return_value=DiseaseOntology(
                efo_ids={"EFO_0003860"}, therapeutic_areas={"MONDO_0045024"}
            )
        ),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    breadth_rows = [e for e in result.payload if "breadth_summary" in (e.source or "")]
    assert len(breadth_rows) == 1
    ev = breadth_rows[0]
    assert ev.extra["dropped_off_target"] == 183
    assert "height" in ev.extra["all_traits"]
    assert ev.extra["is_oncology"] is True
    # Constraint evidence still present
    constraint = [e for e in result.payload if e.evidence_type == EvidenceType.CONSTRAINT]
    assert len(constraint) == 3


async def test_genetics_agent_emits_spoke_disease_scoped_evidence(run_id, trace_id, ctx):
    """SPOKE associations matching the requested disease (by substring) become GENETICS evidence."""
    spoke_bundle = GeneDiseaseBundle(
        gene_symbol="BRCA1",
        associations=[
            SpokeGeneDiseaseAssociation(
                disease_name="breast cancer",
                disease_identifier="DOID:1612",
                edge_sources=["GWAS"],
                gwas_pvalue=8e-09,
            ),
            SpokeGeneDiseaseAssociation(
                disease_name="type 2 diabetes mellitus",
                disease_identifier="DOID:9352",
                edge_sources=["DISEASES"],
                diseases_score=6.291,
            ),
        ],
        source_link="https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/BRCA1",
        text="SPOKE: 2 disease association edge(s) for BRCA1.",
    )
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_gene_disease_associations": AsyncMock(
            return_value=spoke_bundle
        ),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    spoke_evs = [e for e in result.payload if (e.source or "").startswith("spoke:")]
    # Only the breast-cancer-matching association is kept; diabetes is off-indication.
    assert len(spoke_evs) == 1
    ev = spoke_evs[0]
    assert ev.evidence_type == EvidenceType.GENETICS
    assert ev.classification == DataClass.NON_SENSITIVE
    assert ev.extra["disease_name"] == "breast cancer"
    assert ev.extra["gwas_pvalue"] == pytest.approx(8e-09)


async def test_genetics_agent_skips_spoke_when_no_disease_match(run_id, trace_id, ctx):
    """SPOKE associations are dropped entirely when none match the requested disease."""
    spoke_bundle = GeneDiseaseBundle(
        gene_symbol="BRCA1",
        associations=[
            SpokeGeneDiseaseAssociation(
                disease_name="type 2 diabetes mellitus",
                disease_identifier="DOID:9352",
                edge_sources=["DISEASES"],
                diseases_score=6.291,
            ),
        ],
        source_link="https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/BRCA1",
        text="SPOKE: 1 disease association edge(s) for BRCA1.",
    )
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_gene_disease_associations": AsyncMock(
            return_value=spoke_bundle
        ),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    spoke_evs = [e for e in result.payload if (e.source or "").startswith("spoke:")]
    assert spoke_evs == []


async def test_genetics_agent_emits_ontology_row_with_clingen_moi_preferred(run_id, trace_id, ctx):
    """ClinGen's mode-of-inheritance (gold standard) wins over HPO/Monarch's when both present."""
    clingen_bundle = ClinGenBundle(
        gene_symbol="BRCA1",
        associations=[
            ClinGenAssociation(
                gene_symbol="BRCA1",
                hgnc_id="HGNC:1100",
                disease_label="hereditary breast cancer",
                classification="Definitive",
                mode_of_inheritance="Autosomal dominant",
                mode_of_inheritance_curie="HP:0000006",
            ),
        ],
        total=1,
        text="ClinGen gene validity for BRCA1: hereditary breast cancer (Definitive).",
    )
    phenotype_bundle = GenePhenotypeBundle(
        gene_symbol="BRCA1",
        hgnc_id="HGNC:1100",
        phenotype_count=3,
        top_phenotypes=["Breast carcinoma", "Ovarian neoplasm", "Family history of cancer"],
        inheritance_modes=["Autosomal recessive"],  # deliberately conflicting w/ ClinGen
        specificity_band="focal",
        text="HPO phenotype profile for BRCA1: 3 annotated phenotype(s) (focal).",
    )
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_clingen_validity": AsyncMock(
            return_value=clingen_bundle
        ),
        "agents.retrieval.genetics.agent.get_gene_phenotypes": AsyncMock(
            return_value=phenotype_bundle
        ),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    onto_evs = [e for e in result.payload if (e.source or "").startswith("ontology:")]
    assert len(onto_evs) == 1
    ev = onto_evs[0]
    assert ev.evidence_type == EvidenceType.GENETICS
    assert ev.classification == DataClass.NON_SENSITIVE
    assert ev.extra["inheritance_mode"] == "Autosomal dominant"
    assert ev.extra["inheritance_mode_source"] == "ClinGen"
    assert ev.extra["hpo_phenotype_count"] == 3
    assert ev.extra["hpo_specificity_band"] == "focal"
    assert "Autosomal dominant" in ev.extra["text"]


async def test_genetics_agent_ontology_row_falls_back_to_hpo_inheritance(run_id, trace_id, ctx):
    """No ClinGen MOI (or no ClinGen curation at all) → fall back to HPO/Monarch."""
    phenotype_bundle = GenePhenotypeBundle(
        gene_symbol="TRPC6",
        hgnc_id="HGNC:12338",
        phenotype_count=4,
        top_phenotypes=["Nephrotic syndrome", "Proteinuria"],
        inheritance_modes=["Autosomal dominant"],
        specificity_band="focal",
        text="HPO phenotype profile for TRPC6.",
    )
    msg = make_task_msg("genetics", {"target_gene": "TRPC6", "disease": "FSGS"}, run_id, trace_id)
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_clingen_validity": AsyncMock(
            return_value=_CLINGEN_BUNDLE_EMPTY
        ),
        "agents.retrieval.genetics.agent.get_gene_phenotypes": AsyncMock(
            return_value=phenotype_bundle
        ),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    onto_evs = [e for e in result.payload if (e.source or "").startswith("ontology:")]
    assert len(onto_evs) == 1
    assert onto_evs[0].extra["inheritance_mode"] == "Autosomal dominant"
    assert onto_evs[0].extra["inheritance_mode_source"] == "HPO/Monarch"


async def test_genetics_agent_skips_ontology_row_when_nothing_to_report(run_id, trace_id, ctx):
    """No inheritance mode and no phenotypes → no ontology evidence row emitted."""
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    with patch.multiple(
        "agents.retrieval.genetics.agent",
        **{k.split(".")[-1]: v for k, v in _GENETICS_PATCHES.items()},
    ):
        result = await GeneticsAgent().run(msg, ctx)

    onto_evs = [e for e in result.payload if (e.source or "").startswith("ontology:")]
    assert onto_evs == []


async def test_genetics_agent_emits_gencc_and_orphanet_evidence(run_id, trace_id, ctx):
    """GenCC and Orphanet bundles with associations become GENETICS evidence rows."""
    gencc_bundle = GenCCBundle(
        gene_symbol="BRCA1",
        associations=[
            GenCCAssociation(
                gene_symbol="BRCA1",
                disease_title="hereditary breast cancer",
                disease_curie="MONDO:0007254",
                classification="Definitive",
                mode_of_inheritance="Autosomal dominant",
                submitter="ClinGen",
            )
        ],
        total=1,
        text="GenCC: BRCA1 — hereditary breast cancer (Definitive, per ClinGen).",
    )
    orphanet_bundle = OrphanetBundle(
        gene_symbol="BRCA1",
        associations=[
            OrphanetAssociation(
                gene_symbol="BRCA1",
                orphacode="145",
                disorder_name="Hereditary breast and ovarian cancer syndrome",
                association_type="Disease-causing germline mutation(s) in",
                association_status="Assessed",
            )
        ],
        total=1,
        text="Orphanet: BRCA1 — Hereditary breast and ovarian cancer syndrome.",
    )
    prevalence_bundle = OrphanetPrevalenceBundle(
        orphacodes=["145"],
        records=[
            OrphanetPrevalence(
                orphacode="145",
                disorder_name="Hereditary breast and ovarian cancer syndrome",
                prevalence_type="Point prevalence",
                prevalence_class="1-9 / 10 000",
                geographic_area="Worldwide",
                validation_status="Validated",
            )
        ],
        total=1,
        text="Orphanet prevalence: Hereditary breast and ovarian cancer syndrome (ORPHA:145): 1-9 / 10 000.",
    )
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_gencc_validity": AsyncMock(return_value=gencc_bundle),
        "agents.retrieval.genetics.agent.get_orphanet_associations": AsyncMock(
            return_value=orphanet_bundle
        ),
        "agents.retrieval.genetics.agent.get_orphanet_prevalence": AsyncMock(
            return_value=prevalence_bundle
        ),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    gencc_evs = [e for e in result.payload if (e.source or "") == "gencc:BRCA1"]
    orphanet_evs = [e for e in result.payload if (e.source or "") == "orphanet:BRCA1"]
    prevalence_evs = [
        e for e in result.payload if (e.source or "") == "orphanet_prevalence:BRCA1"
    ]
    assert len(gencc_evs) == 1
    assert gencc_evs[0].evidence_type == EvidenceType.GENETICS
    assert gencc_evs[0].classification == DataClass.NON_SENSITIVE
    assert gencc_evs[0].extra["total"] == 1
    assert len(orphanet_evs) == 1
    assert orphanet_evs[0].evidence_type == EvidenceType.GENETICS
    assert orphanet_evs[0].classification == DataClass.NON_SENSITIVE
    assert len(prevalence_evs) == 1
    assert prevalence_evs[0].evidence_type == EvidenceType.GENETICS
    assert prevalence_evs[0].extra["total"] == 1
    assert prevalence_evs[0].extra["records"][0]["prevalence_class"] == "1-9 / 10 000"


async def test_genetics_agent_skips_omim_call_when_api_key_unset(
    run_id, trace_id, ctx, monkeypatch
):
    """OMIM must not be called at all (no span, no tool call) when OMIM_API_KEY is unset."""
    monkeypatch.delenv("OMIM_API_KEY", raising=False)
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    omim_mock = AsyncMock(return_value=_OMIM_BUNDLE_EMPTY)
    patches = {**_GENETICS_PATCHES, "agents.retrieval.genetics.agent.get_omim_validity": omim_mock}
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    omim_mock.assert_not_called()
    assert [e for e in result.payload if (e.source or "").startswith("omim:")] == []


async def test_genetics_agent_emits_omim_evidence_when_api_key_set(
    run_id, trace_id, ctx, monkeypatch
):
    """With OMIM enabled+keyed and associations present, OMIM produces a GENETICS evidence row."""
    monkeypatch.setenv("OMIM_ENABLED", "true")
    monkeypatch.setenv("OMIM_API_KEY", "testkey")
    omim_bundle = OmimBundle(
        gene_symbol="BRCA1",
        associations=[
            OmimAssociation(
                gene_symbol="BRCA1",
                phenotype_label="Breast-ovarian cancer, familial",
                mim_number="604370",
                mapping_key="3",
                mapping_confidence="molecularly confirmed",
                inheritance="Autosomal dominant",
            )
        ],
        total=1,
        text="OMIM: BRCA1 — Breast-ovarian cancer, familial (molecularly confirmed).",
    )
    msg = make_task_msg(
        "genetics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    patches = {
        **_GENETICS_PATCHES,
        "agents.retrieval.genetics.agent.get_omim_validity": AsyncMock(return_value=omim_bundle),
    }
    with patch.multiple(
        "agents.retrieval.genetics.agent", **{k.split(".")[-1]: v for k, v in patches.items()}
    ):
        result = await GeneticsAgent().run(msg, ctx)

    omim_evs = [e for e in result.payload if (e.source or "") == "omim:BRCA1"]
    assert len(omim_evs) == 1
    assert omim_evs[0].evidence_type == EvidenceType.GENETICS
    assert omim_evs[0].classification == DataClass.NON_SENSITIVE
    assert omim_evs[0].extra["total"] == 1


# ---------------------------------------------------------------------------
# OmicsAgent
# ---------------------------------------------------------------------------


async def test_omics_agent_returns_sensitive_and_expression_evidence(run_id, trace_id, ctx):
    msg = make_task_msg(
        "omics",
        {"target_gene": "BRCA1", "disease": "breast cancer", "tissue": "breast"},
        run_id,
        trace_id,
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=_EXPR_ROWS)),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=_SPOKE_ANATOMY_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=_ENCODE_BUNDLE_EMPTY),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    sensitive = [e for e in result.payload if e.classification == DataClass.SENSITIVE]
    non_sensitive = [e for e in result.payload if e.classification == DataClass.NON_SENSITIVE]
    assert len(sensitive) == 1
    # blob + HPA specificity + subcellular + 3 tissues (2 top + Heart sentinel)
    assert len(non_sensitive) == 6
    assert all(e.evidence_type == EvidenceType.EXPRESSION for e in non_sensitive)


async def test_omics_agent_granular_claims_link_to_blob(run_id, trace_id, ctx):
    msg = make_task_msg(
        "omics",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=[])),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=_SPOKE_ANATOMY_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=_ENCODE_BUNDLE_EMPTY),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    non_sensitive = [e for e in result.payload if e.classification == DataClass.NON_SENSITIVE]
    blob = next(e for e in non_sensitive if e.source_evidence_id is None)
    granular = [e for e in non_sensitive if e.source_evidence_id is not None]

    assert all(e.source_evidence_id == blob.evidence_id for e in granular)
    assert all(e.claim_text != "" for e in granular)
    # HPA tissue specificity claim present
    assert any("RNA tissue specificity" in e.claim_text for e in granular)
    # subcellular location claim present
    assert any("subcellular localization" in e.claim_text for e in granular)
    # sentinel tissue (Heart_Left_Ventricle) gets its own claim
    assert any("Heart_Left_Ventricle" in e.claim_text for e in granular)


async def test_omics_agent_sets_tissue_as_population(run_id, trace_id, ctx):
    msg = make_task_msg(
        "omics",
        {"target_gene": "BRCA1", "disease": "breast cancer", "tissue": "breast"},
        run_id,
        trace_id,
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=_EXPR_ROWS)),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=_SPOKE_ANATOMY_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=_ENCODE_BUNDLE_EMPTY),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    sensitive_ev = next(e for e in result.payload if e.classification == DataClass.SENSITIVE)
    assert sensitive_ev.population == "breast"


async def test_omics_agent_emits_spoke_anatomy_summary_row(run_id, trace_id, ctx):
    """A non-empty SPOKE anatomy bundle produces exactly one summary EXPRESSION row."""
    spoke_bundle = AnatomyExpressionBundle(
        gene_symbol="BRCA1",
        expressions=[
            SpokeAnatomyExpression(
                anatomy_name="liver", anatomy_identifier="UBERON:0002107", edge_type="EXPRESSES_AeG"
            ),
            SpokeAnatomyExpression(
                anatomy_name="kidney",
                anatomy_identifier="UBERON:0002113",
                edge_type="UPREGULATES_AuG",
            ),
        ],
        source_link="https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/BRCA1",
        text="SPOKE: 2 anatomy expression edge(s) for BRCA1.",
    )
    msg = make_task_msg(
        "omics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=[])),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=spoke_bundle),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=_ENCODE_BUNDLE_EMPTY),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    spoke_evs = [e for e in result.payload if (e.source or "").startswith("spoke_anatomy:")]
    assert len(spoke_evs) == 1
    ev = spoke_evs[0]
    assert ev.evidence_type == EvidenceType.EXPRESSION
    assert ev.classification == DataClass.NON_SENSITIVE
    assert ev.claim_text != ""
    assert "liver" in ev.claim_text
    assert "kidney" in ev.claim_text
    assert ev.extra["anatomy_names"] == ["kidney", "liver"]


async def test_omics_agent_skips_spoke_anatomy_row_when_empty(run_id, trace_id, ctx):
    """No SPOKE anatomy edges → no spoke_anatomy Evidence row is emitted."""
    msg = make_task_msg(
        "omics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=[])),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=_SPOKE_ANATOMY_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=_ENCODE_BUNDLE_EMPTY),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    spoke_evs = [e for e in result.payload if (e.source or "").startswith("spoke_anatomy:")]
    assert spoke_evs == []


async def test_omics_agent_emits_encode_regulatory_element_row(run_id, trace_id, ctx):
    """A non-empty ENCODE coverage bundle produces one REGULATORY_ELEMENT row."""
    encode_bundle = RegulatoryCoverageBundle(
        gene_symbol="BRCA1",
        coordinates="BRCA1: (chr17:43044295-43125483) +/- 2kb",
        total_experiments=120,
        top_assays=[],
        top_targets=[],
        top_organs=[],
        source_link="https://www.encodeproject.org/region-search/?region=BRCA1&genome=GRCh38",
        text="ENCODE region-search: 120 experiment(s) overlap the BRCA1 locus.",
    )
    msg = make_task_msg(
        "omics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=[])),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=_SPOKE_ANATOMY_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=encode_bundle),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    encode_evs = [e for e in result.payload if (e.source or "").startswith("encode_region_search:")]
    assert len(encode_evs) == 1
    ev = encode_evs[0]
    assert ev.evidence_type == EvidenceType.REGULATORY_ELEMENT
    assert ev.classification == DataClass.NON_SENSITIVE
    assert ev.claim_text == encode_bundle.text


async def test_omics_agent_skips_encode_row_when_no_experiments(run_id, trace_id, ctx):
    """A zero-experiment ENCODE bundle does not produce a REGULATORY_ELEMENT row."""
    msg = make_task_msg(
        "omics", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with (
        patch("agents.retrieval.omics.agent.query_internal_db", AsyncMock(return_value=[])),
        patch("agents.retrieval.omics.agent.get_expression", AsyncMock(return_value=_GTEX_BUNDLE)),
        patch(
            "agents.retrieval.omics.agent.get_anatomy_expression",
            AsyncMock(return_value=_SPOKE_ANATOMY_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_differential_expression",
            AsyncMock(return_value=_EXPR_ATLAS_BUNDLE_EMPTY),
        ),
        patch(
            "agents.retrieval.omics.agent.get_regulatory_coverage",
            AsyncMock(return_value=_ENCODE_BUNDLE_EMPTY),
        ),
    ):
        result = await OmicsAgent().run(msg, ctx)

    encode_evs = [e for e in result.payload if (e.source or "").startswith("encode_region_search:")]
    assert encode_evs == []
