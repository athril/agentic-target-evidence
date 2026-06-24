# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/retrieval — service-level contract tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from mcp_servers.chembl.tools import ChemistryBundle
from mcp_servers.clinicaltrials.tools import ConditionTrialLandscape, TrialRecord
from mcp_servers.depmap.tools import DependencyBundle
from mcp_servers.dgidb.tools import CategoryBundle, DrugInteraction, GeneCategory, InteractionBundle
from mcp_servers.gbd.tools import GBDBundle, GBDPrevalenceRecord
from mcp_servers.impc.tools import ImpcBundle
from mcp_servers.openfda.tools import IndicationDrugLandscape
from mcp_servers.opentargets.tools import (
    AssociationBundle,
    KnownDrugsBundle,
    MousePhenotypeBundle,
    SafetyBundle,
    TractabilityBundle,
)
from mcp_servers.project_score.tools import ProjectScoreBundle
from mcp_servers.uniprot.tools import ProteinProfile
from mcp_servers.uspto.tools import PatentRecord
from schemas.evidence import DataClass, Direction, EvidenceType
from services.retrieval.clinical_trial import fetch_trials
from services.retrieval.druggability import fetch_druggability
from services.retrieval.functional import fetch_functional
from services.retrieval.gbd import fetch_gbd
from services.retrieval.indication_competition import fetch_indication_competition
from services.retrieval.opentargets import OpenTargetsResult, fetch_opentargets
from services.retrieval.patent import fetch_patents

_RUN_ID = uuid.uuid4()
_TRACE = "test-trace"


# ── patent ──────────────────────────────────────────────────────────────────


def _make_patent_records(n: int = 2) -> list[PatentRecord]:
    return [
        PatentRecord(
            patent_id=f"US{1000 + i}",
            app_number=f"1600000{i}",
            title=f"Patent {i}",
            abstract="A method for treating cancer.",
            assignee=f"Corp{i}",
            filing_date="2022-01-01",
        )
        for i in range(n)
    ]


async def test_fetch_patents_returns_evidence_list():
    with patch(
        "services.retrieval.patent.search_patents", AsyncMock(return_value=_make_patent_records(3))
    ):
        ev = await fetch_patents("BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE)
    assert len(ev) == 3
    assert all(e.evidence_type == EvidenceType.PATENT for e in ev)
    assert all(e.classification == DataClass.NON_SENSITIVE for e in ev)


async def test_fetch_patents_source_is_application_number():
    with patch(
        "services.retrieval.patent.search_patents", AsyncMock(return_value=_make_patent_records(2))
    ):
        ev = await fetch_patents("BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE)
    assert {e.source for e in ev} == {"16000000", "16000001"}


async def test_fetch_patents_empty():
    with patch("services.retrieval.patent.search_patents", AsyncMock(return_value=[])):
        ev = await fetch_patents("BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev == []


async def test_fetch_patents_sets_direction():
    with patch(
        "services.retrieval.patent.search_patents", AsyncMock(return_value=_make_patent_records(1))
    ):
        ev = await fetch_patents(
            "BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE, direction="inhibit"
        )
    assert ev[0].direction == Direction.INHIBIT


async def test_fetch_patents_unknown_direction_defaults_unspecified():
    with patch(
        "services.retrieval.patent.search_patents", AsyncMock(return_value=_make_patent_records(1))
    ):
        ev = await fetch_patents(
            "BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE, direction="unknown_val"
        )
    assert ev[0].direction == Direction.UNSPECIFIED


# ── clinical_trial ───────────────────────────────────────────────────────────


def _make_trial_records(n: int = 2, scope: str = "abstract") -> list[TrialRecord]:
    return [TrialRecord(nct_id=f"NCT{1000 + i}", title=f"Trial {i}", scope=scope) for i in range(n)]


async def test_fetch_trials_returns_evidence_list():
    with patch(
        "services.retrieval.clinical_trial.search_trials",
        AsyncMock(return_value=_make_trial_records(4)),
    ):
        ev = await fetch_trials("BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE)
    assert len(ev) == 4
    assert all(e.evidence_type == EvidenceType.CLINICAL_TRIAL for e in ev)


async def test_fetch_trials_empty():
    with patch("services.retrieval.clinical_trial.search_trials", AsyncMock(return_value=[])):
        ev = await fetch_trials("BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev == []


async def test_fetch_trials_passes_population():
    mock = AsyncMock(return_value=[])
    with patch("services.retrieval.clinical_trial.search_trials", mock):
        await fetch_trials(
            "BRCA1", "breast cancer", population="adults", run_id=_RUN_ID, trace_id=_TRACE
        )
    mock.assert_awaited_once_with("BRCA1", "breast cancer", "adults")


# ── opentargets ──────────────────────────────────────────────────────────────


_ASSOC = AssociationBundle(
    gene_id="ENSG00000012048",
    disease_id="EFO_0000305",
    overall_score=0.87,
    genetic_score=0.9,
    known_drugs_score=0.7,
)
_TRACT = TractabilityBundle(gene_id="ENSG00000012048", small_molecule=True, antibody=False)
_DRUGS = KnownDrugsBundle(gene_id="ENSG00000012048", total_count=0)
_SAFETY = SafetyBundle(gene_id="ENSG00000012048")
_MOUSE = MousePhenotypeBundle(gene_id="ENSG00000012048")

_OT_PATCHES = {
    "get_associations": AsyncMock(return_value=_ASSOC),
    "get_tractability": AsyncMock(return_value=_TRACT),
    "get_known_drugs": AsyncMock(return_value=_DRUGS),
    "get_safety": AsyncMock(return_value=_SAFETY),
    "get_mouse_phenotypes": AsyncMock(return_value=_MOUSE),
}


async def test_fetch_opentargets_returns_result_object():
    with patch.multiple("services.retrieval.opentargets", **_OT_PATCHES):
        result = await fetch_opentargets(
            "BRCA1",
            "breast cancer",
            gene_id="ENSG00000012048",
            disease_id="EFO_0000305",
            run_id=_RUN_ID,
            trace_id=_TRACE,
        )
    assert isinstance(result, OpenTargetsResult)
    assert len(result.evidences) == 1
    assert result.gene_id == "ENSG00000012048"
    assert result.disease_id == "EFO_0000305"


async def test_fetch_opentargets_tractability_score():
    with patch.multiple("services.retrieval.opentargets", **_OT_PATCHES):
        result = await fetch_opentargets(
            "BRCA1",
            "breast cancer",
            gene_id="ENSG00000012048",
            disease_id="EFO_0000305",
            run_id=_RUN_ID,
            trace_id=_TRACE,
        )
    extra = result.evidences[0].extra
    assert extra["tractability_score"] == 1.0
    assert "known_drugs_count" in extra
    assert "safety_liability_count" in extra
    assert "mouse_phenotype_count" in extra


async def test_fetch_opentargets_resolves_ids_when_missing():
    with (
        patch("services.retrieval.opentargets.resolve_gene", AsyncMock(return_value="ENSG000X")),
        patch("services.retrieval.opentargets.resolve_disease", AsyncMock(return_value="EFO_X")),
        patch.multiple("services.retrieval.opentargets", **_OT_PATCHES),
    ):
        result = await fetch_opentargets("BRCA1", "breast cancer", run_id=_RUN_ID, trace_id=_TRACE)
    assert result.gene_id == "ENSG000X"
    assert result.disease_id == "EFO_X"


# ── functional ───────────────────────────────────────────────────────────────


_SCREENS_ROWS = [
    {
        "gene_symbol": "KRAS",
        "screen_id": "PRISM_001",
        "cell_line": "A549",
        "cancer_type": "Lung",
        "gene_effect": -2.1,
        "is_essential": True,
        "dataset_version": "22Q4",
        "_classification": "SENSITIVE",
    },
]
_DEPMAP_BUNDLE = DependencyBundle(
    gene_symbol="KRAS",
    gene_effect_mean=-1.45,
    num_dependent_lines=312,
    total_lines=850,
    is_common_essential=False,
    selective_lineages=["Lung", "Pancreas"],
    source_link="https://depmap.org/portal/gene/KRAS",
    text="DepMap: KRAS mean gene effect=-1.45",
)
_IMPC_BUNDLE_EMPTY = ImpcBundle(
    gene_symbol="KRAS",
    viability="unknown",
    phenotypes=[],
    total=0,
    source_link="https://www.ebi.ac.uk/mi/impc/",
    text="No statistically significant IMPC phenotypes found for KRAS.",
)
_PROJECT_SCORE_BUNDLE_EMPTY = ProjectScoreBundle(
    gene_symbol="KRAS",
    sidg_id="",
    text="Project Score: no gene record found for KRAS.",
)


async def test_fetch_functional_returns_sensitive_and_non_sensitive():
    with (
        patch(
            "services.retrieval.functional.query_internal_db", AsyncMock(return_value=_SCREENS_ROWS)
        ),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    sensitive = [e for e in ev if e.classification == DataClass.SENSITIVE]
    non_sensitive = [e for e in ev if e.classification == DataClass.NON_SENSITIVE]
    assert len(sensitive) == 1
    assert len(non_sensitive) == 1


async def test_fetch_functional_no_internal_rows_returns_only_depmap():
    with (
        patch("services.retrieval.functional.query_internal_db", AsyncMock(return_value=[])),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    assert len(ev) == 1
    assert ev[0].classification == DataClass.NON_SENSITIVE


async def test_fetch_functional_excludes_classification_from_extra():
    with (
        patch(
            "services.retrieval.functional.query_internal_db", AsyncMock(return_value=_SCREENS_ROWS)
        ),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    for e in ev:
        assert "_classification" not in e.extra


async def test_fetch_functional_includes_impc_when_phenotypes_present():
    impc_bundle = ImpcBundle(
        gene_symbol="KRAS",
        viability="lethal",
        phenotypes=[
            {
                "mp_term_name": "preweaning lethality, complete penetrance",
                "mp_term_id": "MP:0011100",
                "p_value": 1e-8,
                "zygosity": "homozygote",
                "life_stage_name": "Early adult",
                "procedure_name": "Viability Primary Screen",
            }
        ],
        total=1,
        source_link="https://www.ebi.ac.uk/mi/impc/",
        text="IMPC: KRAS knockout is lethal.",
    )
    with (
        patch("services.retrieval.functional.query_internal_db", AsyncMock(return_value=[])),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=impc_bundle),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    impc_evidence = [e for e in ev if e.source == "impc:KRAS"]
    assert len(impc_evidence) == 1
    assert impc_evidence[0].evidence_type == EvidenceType.FUNCTIONAL_GENOMICS
    assert impc_evidence[0].classification == DataClass.NON_SENSITIVE


async def test_fetch_functional_impc_failure_is_caught_and_skipped():
    with (
        patch("services.retrieval.functional.query_internal_db", AsyncMock(return_value=[])),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(side_effect=RuntimeError("network down")),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    assert len(ev) == 1
    assert ev[0].source == "depmap:KRAS"


async def test_fetch_functional_includes_project_score_when_data_present():
    score_bundle = ProjectScoreBundle(
        gene_symbol="KRAS",
        sidg_id="SIDG13960",
        bf_scaled_mean=1.2,
        num_fitness_lines=172,
        total_lines=350,
        fitness_fraction=0.49,
        is_pancan_core_fitness=False,
        cancer_specific_core_fitness_tissues=["lung", "colon", "pancreas"],
        source_link="https://score.depmap.sanger.ac.uk/gene/SIDG13960",
        text="Project Score: KRAS a fitness gene in 172/350 Sanger cell lines.",
    )
    with (
        patch("services.retrieval.functional.query_internal_db", AsyncMock(return_value=[])),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=score_bundle),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    score_evidence = [e for e in ev if e.source == "project_score:KRAS"]
    assert len(score_evidence) == 1
    assert score_evidence[0].evidence_type == EvidenceType.FUNCTIONAL_GENOMICS
    assert score_evidence[0].classification == DataClass.NON_SENSITIVE


async def test_fetch_functional_project_score_failure_is_caught_and_skipped():
    with (
        patch("services.retrieval.functional.query_internal_db", AsyncMock(return_value=[])),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(side_effect=RuntimeError("network down")),
        ),
    ):
        ev = await fetch_functional("KRAS", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    assert len(ev) == 1


# ── druggability ─────────────────────────────────────────────────────────────


_PROFILE = ProteinProfile(
    gene_symbol="EGFR",
    uniprot_accession="P00533",
    chembl_target_id="CHEMBL203",
    source_link="https://www.uniprot.org/uniprotkb/P00533",
    text="UniProt: EGFR (P00533)",
)
_CHEMISTRY = ChemistryBundle(
    gene_symbol="EGFR",
    chembl_target_id="CHEMBL203",
    num_mechanisms=3,
    source_link="https://www.ebi.ac.uk/chembl/target_report_card/CHEMBL203",
    text="ChEMBL: 3 mechanisms for EGFR",
)
_DGIDB_INTERACTIONS = InteractionBundle(
    gene_symbol="EGFR",
    gene_concept_id="dgidb:1",
    total_count=1,
    interactions=[DrugInteraction(drug_name="ERLOTINIB", interaction_score=0.568)],
    text="DGIdb: 1 interaction for EGFR",
)
_DGIDB_INTERACTIONS_EMPTY = InteractionBundle(gene_symbol="EGFR", text="DGIdb: no interactions.")
_DGIDB_CATEGORIES = CategoryBundle(
    gene_symbol="EGFR",
    categories=[GeneCategory(name="DRUGGABLE GENOME")],
    is_druggable_genome=True,
    text="DGIdb: EGFR is in the druggable genome.",
)
_DGIDB_CATEGORIES_EMPTY = CategoryBundle(gene_symbol="EGFR", text="DGIdb: no categories.")

_DRUGGABILITY_PATCHES = {
    "get_protein_profile": AsyncMock(return_value=_PROFILE),
    "get_chemistry": AsyncMock(return_value=_CHEMISTRY),
    "get_gene_drug_interactions": AsyncMock(return_value=_DGIDB_INTERACTIONS),
    "get_gene_categories": AsyncMock(return_value=_DGIDB_CATEGORIES),
}


async def test_fetch_druggability_includes_dgidb_interactions_and_categories():
    with patch.multiple("services.retrieval.druggability", **_DRUGGABILITY_PATCHES):
        ev = await fetch_druggability("EGFR", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    sources = {e.source for e in ev}
    assert "dgidb:dgidb:1" in sources
    assert "dgidb:EGFR" in sources
    dgidb_ev = [e for e in ev if e.source.startswith("dgidb:")]
    assert all(e.evidence_type == EvidenceType.DRUGGABILITY for e in dgidb_ev)
    assert all(e.classification == DataClass.NON_SENSITIVE for e in dgidb_ev)


async def test_fetch_druggability_omits_dgidb_rows_when_empty():
    patches = {
        **_DRUGGABILITY_PATCHES,
        "get_gene_drug_interactions": AsyncMock(return_value=_DGIDB_INTERACTIONS_EMPTY),
        "get_gene_categories": AsyncMock(return_value=_DGIDB_CATEGORIES_EMPTY),
    }
    with patch.multiple("services.retrieval.druggability", **patches):
        ev = await fetch_druggability("EGFR", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    assert not any(e.source.startswith("dgidb:") for e in ev)
    assert len(ev) == 2  # uniprot + chembl only


async def test_fetch_druggability_dgidb_failure_is_caught_and_skipped():
    patches = {
        **_DRUGGABILITY_PATCHES,
        "get_gene_drug_interactions": AsyncMock(side_effect=RuntimeError("network down")),
        "get_gene_categories": AsyncMock(side_effect=RuntimeError("network down")),
    }
    with patch.multiple("services.retrieval.druggability", **patches):
        ev = await fetch_druggability("EGFR", "lung cancer", run_id=_RUN_ID, trace_id=_TRACE)

    assert not any(e.source.startswith("dgidb:") for e in ev)
    assert {e.source for e in ev} == {"uniprot:P00533", "chembl:CHEMBL203"}


# ── gbd ──────────────────────────────────────────────────────────────────────


def _make_gbd_bundle(mapping: str = "exact") -> GBDBundle:
    record = GBDPrevalenceRecord(
        cause_id="587",
        cause_name="Type 2 diabetes mellitus",
        measure="Prevalence",
        metric="Number",
        location="Global",
        year=2021,
        value=529000000.0,
    )
    return GBDBundle(
        disease="type 2 diabetes",
        cause_name=record.cause_name,
        records=[record],
        total=1,
        text="Type 2 diabetes mellitus (GBD): prevalence 529,000,000 cases (Global, 2021).",
        mapping=mapping,
    )


_GBD_BUNDLE_NONE = GBDBundle(disease="unmapped disease", mapping="none")


async def test_fetch_gbd_returns_evidence_list():
    with patch(
        "services.retrieval.gbd.get_disease_burden", AsyncMock(return_value=_make_gbd_bundle())
    ):
        ev = await fetch_gbd("type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE)
    assert len(ev) == 1
    assert ev[0].evidence_type == EvidenceType.EPIDEMIOLOGY
    assert ev[0].source == "gbd:burden:587"
    assert ev[0].classification == DataClass.NON_SENSITIVE


async def test_fetch_gbd_empty_on_no_mapping():
    with patch(
        "services.retrieval.gbd.get_disease_burden", AsyncMock(return_value=_GBD_BUNDLE_NONE)
    ):
        ev = await fetch_gbd("unmapped disease", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev == []


async def test_fetch_gbd_empty_when_no_records():
    bundle = GBDBundle(disease="type 2 diabetes", mapping="exact", records=[], total=0)
    with patch("services.retrieval.gbd.get_disease_burden", AsyncMock(return_value=bundle)):
        ev = await fetch_gbd("type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev == []


async def test_fetch_gbd_sets_direction():
    with patch(
        "services.retrieval.gbd.get_disease_burden", AsyncMock(return_value=_make_gbd_bundle())
    ):
        ev = await fetch_gbd(
            "type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE, direction="inhibit"
        )
    assert ev[0].direction == Direction.INHIBIT


async def test_fetch_gbd_extra_carries_bundle():
    with patch(
        "services.retrieval.gbd.get_disease_burden", AsyncMock(return_value=_make_gbd_bundle())
    ):
        ev = await fetch_gbd("type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev[0].extra["mapping"] == "exact"
    assert ev[0].extra["text"].startswith("Type 2 diabetes mellitus (GBD)")


# ── indication_competition ───────────────────────────────────────────────────

_DRUGS_LANDSCAPE = IndicationDrugLandscape(
    indication="type 2 diabetes",
    mapping="phrase",
    approved_drug_count=1,
    drugs=["METFORMIN"],
    moa_examples=["Decreases hepatic glucose production via AMPK activation."],
    source_link="https://api.fda.gov/drug/label.json?search=...",
    text="1 approved drug for type 2 diabetes (OpenFDA): METFORMIN.",
)
_DRUGS_LANDSCAPE_NONE = IndicationDrugLandscape(indication="unmapped disease", mapping="none")

_TRIALS_LANDSCAPE = ConditionTrialLandscape(
    condition="type 2 diabetes",
    total_count=500,
    active_count=120,
    phase3_count=30,
    recruiting_count=40,
    mapping="cond",
    source_link="https://clinicaltrials.gov/api/v2/studies?query.cond=type+2+diabetes",
    text="type 2 diabetes (ClinicalTrials.gov): 500 trials, 120 active, 30 in Phase 3.",
)
_TRIALS_LANDSCAPE_NONE = ConditionTrialLandscape(condition="unmapped disease", mapping="none")

_COMPETITION_PATCHES = {
    "count_indication_drugs": AsyncMock(return_value=_DRUGS_LANDSCAPE),
    "count_condition_trials": AsyncMock(return_value=_TRIALS_LANDSCAPE),
}


async def test_fetch_indication_competition_returns_one_row():
    with patch.multiple("services.retrieval.indication_competition", **_COMPETITION_PATCHES):
        ev = await fetch_indication_competition("type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE)

    assert len(ev) == 1
    row = ev[0]
    assert row.evidence_type == EvidenceType.COMPETITION
    assert row.classification == DataClass.NON_SENSITIVE
    assert row.extra["approved_drug_count"] == 1
    assert row.extra["active_trial_count"] == 120
    assert row.extra["phase3_trial_count"] == 30
    assert row.extra["total_trial_count"] == 500
    assert "METFORMIN" in row.extra["text"]


async def test_fetch_indication_competition_direction_always_unspecified():
    """Competition is a property of the disease, not the gene→disease direction."""
    with patch.multiple("services.retrieval.indication_competition", **_COMPETITION_PATCHES):
        ev = await fetch_indication_competition(
            "type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE, direction="inhibit"
        )
    assert ev[0].direction == Direction.UNSPECIFIED


async def test_fetch_indication_competition_empty_when_both_unmapped():
    patches = {
        "count_indication_drugs": AsyncMock(return_value=_DRUGS_LANDSCAPE_NONE),
        "count_condition_trials": AsyncMock(return_value=_TRIALS_LANDSCAPE_NONE),
    }
    with patch.multiple("services.retrieval.indication_competition", **patches):
        ev = await fetch_indication_competition("unmapped disease", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev == []


async def test_fetch_indication_competition_one_sided_mapping_still_returns_row():
    """Only one of the two sources matching is still useful context — emit the row."""
    patches = {
        "count_indication_drugs": AsyncMock(return_value=_DRUGS_LANDSCAPE),
        "count_condition_trials": AsyncMock(return_value=_TRIALS_LANDSCAPE_NONE),
    }
    with patch.multiple("services.retrieval.indication_competition", **patches):
        ev = await fetch_indication_competition("type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE)
    assert len(ev) == 1
    assert ev[0].extra["approved_drug_count"] == 1
    assert ev[0].extra["active_trial_count"] == 0


async def test_fetch_indication_competition_api_exception_returns_empty():
    patches = {
        "count_indication_drugs": AsyncMock(side_effect=RuntimeError("network down")),
        "count_condition_trials": AsyncMock(return_value=_TRIALS_LANDSCAPE),
    }
    with patch.multiple("services.retrieval.indication_competition", **patches):
        ev = await fetch_indication_competition("type 2 diabetes", run_id=_RUN_ID, trace_id=_TRACE)
    assert ev == []
