# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Profile fixes, each tested on the record shape a real run produced:
FAERS cuts, both dosage directions, records that answered with no value, the matrix's
gated-off sources and the order of the allelic series.

Offline: the records are fixtures copied from raw/ files of the testing runs. Gene names
are the real ones, since the rules key on them."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gather  # noqa: E402
import profile_drugs as drugs  # noqa: E402
import profile_retrieval as retrieval  # noqa: E402
import profile_tables as tables  # noqa: E402

# ------------------------------------------------------- FAERS cuts (EGFR)


def _faers_entry() -> dict[str, Any]:
    return {"total_reports": 100, "serious_reports": 50, "serious_rate": 0.5,
            "death_reports": 10, "death_rate": 0.1, "top_reactions": []}  # fmt: skip


def test_the_faers_caption_says_how_many_drugs_the_table_leaves_out() -> None:
    data = {
        "by_drug": {"ERLOTINIB": _faers_entry(), "CETUXIMAB": _faers_entry(),
                    "LAPATINIB": _faers_entry()},  # fmt: skip
        "drugs_queried": ["ERLOTINIB", "CETUXIMAB", "LAPATINIB", "GEFITINIB", "AFATINIB", "OSIMERTINIB"],
        "reporting_total": 6,
        "approved_total": 9,
    }  # fmt: skip
    text = tables.faers_md(data, "2026-11-01", {"faers"})
    assert "The table shows 3 of the 6 drugs with reports" in text
    assert "6 of the 9 approved drugs were queried" in text


def test_the_faers_caption_says_nothing_when_nothing_was_left_out() -> None:
    data = {"by_drug": {"ERLOTINIB": _faers_entry()}, "drugs_queried": ["ERLOTINIB"],
            "reporting_total": 1, "approved_total": 1}  # fmt: skip
    text = tables.faers_md(data, "2026-11-01", {"faers"})
    assert "The table shows" not in text and "were queried" not in text


# ----------------------------------------------------- both dosage directions (MECP2)

# MECP2 run, raw/clingen_dosage.json: triplosensitivity is curated as no evidence.
_MECP2_DOSAGE: dict[str, Any] = {
    "gene_symbol": "MECP2", "curated": True, "haploinsufficiency_score": "3",
    "haploinsufficiency": "Sufficient evidence for dosage pathogenicity",
    "triplosensitivity_score": "0", "triplosensitivity": "No evidence available",
}  # fmt: skip


def test_the_triplosensitivity_result_is_shown_beside_haploinsufficiency() -> None:
    text = tables.tolerance_md({"clingen_dosage": _MECP2_DOSAGE}, "", {"clingen_dosage"})
    assert "haploinsufficiency score 3" in text
    assert "triplosensitivity score 0 (No evidence available)" in text


def test_a_gene_not_curated_for_dosage_shows_no_dosage_line() -> None:
    text = tables.tolerance_md({"clingen_dosage": {"curated": False}}, "", {"clingen_dosage"})
    assert "ClinGen dosage:" not in text


# ------------------------------------ a source that answered with no value (MALAT1)

# MALAT1 run: gnomAD answered (ok) with every metric null; DepMap failed; somatic answered for a
# gene with no protein (length 0).
_MALAT1_CONSTRAINT: dict[str, Any] = {"gene_symbol": "MALAT1", "loeuf": None, "pli": None}
_MALAT1_SOMATIC: dict[str, Any] = {"entrez_id": "378938", "studies": [{"mutations": []}]}


def test_an_answer_with_no_value_is_not_called_not_checked() -> None:
    text = tables.tolerance_md(
        {"gnomad_constraint": _MALAT1_CONSTRAINT, "somatic": _MALAT1_SOMATIC, "depmap": {}},
        "",
        {"gnomad_constraint", "somatic"},
        length=0,
    )
    assert "| Is variation selected against in the population? | no value in the record |" in text
    assert "not applicable: no protein sequence to test" in text
    assert "| Do cultured cells need it? | not checked |" in text


def test_a_somatic_record_with_no_protein_sequence_is_not_applicable_not_absent() -> None:
    text = tables.tolerance_md({"somatic": _MALAT1_SOMATIC}, "", {"somatic"}, length=0)
    assert "not applicable: no protein sequence" in text
    assert "not checked" not in text.split("Is it a somatic driver?")[1].split("\n")[0]


# ----------------------------------------- gated-off sources in the matrix


@pytest.mark.parametrize(
    ("entries", "status"),
    [
        ([("skipped", "gated off (OMIM_ENABLED / OMIM_API_KEY)")], retrieval.NOT_CHECKED),
        (
            [("skipped", "not a protein-coding gene (Genebass covers the exome analysis)")],
            retrieval.NOT_APPLICABLE,
        ),  # fmt: skip
        ([("ok", ""), ("skipped", "gated off (TTD_ENABLED)")], retrieval.SEARCHED),
        ([("error", "after 2 attempts")], retrieval.FAILED),
        ([("empty", "")], retrieval.NOTHING),
    ],
)
def test_a_dimension_reports_why_its_records_are_missing(
    entries: list[tuple[str, str]], status: str
) -> None:
    assert retrieval._status(entries) == status


# ------------------------------------- the series table keeps the best-evidenced rows (CFTR)

# CFTR run, raw/allelic_series.json: F508del is the most reported CF allele, at record index 86;
# G551D comes first in record order.
_G551D_ROW: dict[str, Any] = {
    "change": "G551D", "position": 551, "rsid": "rs75527207", "consequence": "missense_variant",
    "origins": ["UniProt", "ClinVar Pathogenic for Cystic fibrosis (4★, 36 submissions)", "named in 63 papers"],
    "uniprot_note": "decrease in the frequency of channel opening in vitro",
    "refs": [],
}  # fmt: skip
_F508_ROW: dict[str, Any] = {
    "change": "F508", "position": 508, "rsid": "rs113993960", "consequence": "inframe_deletion",
    "origins": ["ClinVar Pathogenic for Cystic fibrosis (1★, 1 submission)",
                "ClinVar Pathogenic for Cystic fibrosis (4★, 102 submissions)",
                "named in 501 papers", "named in 6 papers", "GWAS lead"],
    "refs": [],
}  # fmt: skip


def test_the_series_table_ranks_by_evidence_not_record_order() -> None:
    assert tables._series_evidence(_F508_ROW) == (103, 507)
    assert tables._series_evidence(_G551D_ROW) == (36, 63)
    text = tables.allelic_series_md({"rows": [_G551D_ROW, _F508_ROW]}, {"allelic_series"})
    body = [line for line in text.splitlines() if line.startswith("| ")]
    assert "F508" in body[1] and "G551D" in body[2]


# --------------------------------------------- a source may not report a negative

# IMPC's own record for Cftr: phenotyping was never done, so an empty phenotype list says
# nothing about the knockout. The connector declares that as coverage.
_IMPC_NOT_COVERED: dict[str, Any] = {
    "gene_symbol": "CFTR",
    "coverage": "not_covered",
    "coverage_note": "assignment: Withdrawn",
    "viability": "unknown",
    "phenotypes": [],
    "total": 0,
}
_IMPC_COVERED: dict[str, Any] = {
    "gene_symbol": "PCSK9",
    "coverage": "covered",
    "viability": "viable",
    "phenotypes": [{"mp_term_name": "decreased circulating cholesterol level"}],
    "total": 1,
}


def test_a_source_declaring_no_coverage_is_not_an_empty_result() -> None:
    declared, note = gather._declared_coverage(_IMPC_NOT_COVERED)
    assert declared is True
    assert "Withdrawn" in note


def test_a_covered_source_keeps_its_ok_or_empty_status() -> None:
    assert gather._declared_coverage(_IMPC_COVERED) == (False, "")
    # A source that cannot establish its own scope says nothing and is unaffected.
    assert gather._declared_coverage({"gene_symbol": "CFTR", "total": 0}) == (False, "")


def test_coverage_unknown_is_also_not_a_negative() -> None:
    declared, note = gather._declared_coverage(
        {"coverage": "unknown", "coverage_note": "no MGI accession for the mouse ortholog"}
    )
    assert declared is True
    assert "no MGI accession" in note


def test_the_matrix_reads_a_not_covered_source_as_not_checked() -> None:
    assert retrieval._status([("not_covered", "does not cover this gene")]) == retrieval.NOT_CHECKED
    # Beside a source that did answer, the dimension is still searched.
    assert retrieval._status([("not_covered", ""), ("ok", "")]) == retrieval.SEARCHED
    # And it is never "searched, nothing found", which would license an absence claim.
    assert retrieval._status([("not_covered", "")]) != retrieval.NOTHING


# ------------------------------------- region-level dosage curation (MECP2)

_MECP2_DOSAGE: dict[str, Any] = {
    "gene_symbol": "MECP2",
    "curated": True,
    "haploinsufficiency_score": "3",
    "haploinsufficiency": "Sufficient evidence for dosage pathogenicity",
    "triplosensitivity_score": "0",
    "triplosensitivity": "No evidence available",
    "regions": [
        {
            "isca_id": "ISCA-46304",
            "name": "Xq28 region (includes MECP2)",
            "cytoband": "Xq28",
            "haploinsufficiency_score": "0",
            "triplosensitivity_score": "3",
            "disease_id": "MONDO:0010283",
        }
    ],
    "regions_checked": True,
}


def test_the_dosage_cell_shows_the_region_curation_beside_the_gene_row() -> None:
    cell = tables._germline_value({}, _MECP2_DOSAGE, {}, {})
    assert "triplosensitivity score 0" in cell
    assert "ISCA-46304" in cell
    assert "triplosensitivity 3" in cell
    # The region's score must not be presented as the gene's own.
    assert "the region's score" in cell


def test_a_gene_in_no_curated_region_adds_no_region_line() -> None:
    cell = tables._germline_value({}, {**_MECP2_DOSAGE, "regions": []}, {}, {})
    assert "ISCA" not in cell


# --------------------------------- IMPC coverage in the models table

_ORTHOLOG: dict[str, Any] = {"reading": "ortholog", "summary": "Mouse ortholog Cftr (NCBI curated)"}


def _models(impc: dict[str, Any], ot_mouse: dict[str, Any] | None = None) -> str:
    from profile_biology import models_md

    return models_md(_ORTHOLOG, ot_mouse or {}, impc, {}, {}, {"orthologs", "ot_mouse", "impc"})


def test_a_gene_impc_never_phenotyped_is_not_a_negative_in_the_models_table() -> None:
    """CFTR's record. The report previously read "no significant knockout phenotype" here,
    twelve lines above its own MGI table of 36 phenotypes including lethality."""
    text = _models(
        {"coverage": "not_covered", "coverage_note": "assignment: Withdrawn", "phenotypes": []}
    )
    assert "Not checked" in text
    assert "no phenotyping data for this gene" in text
    assert "Withdrawn" in text
    assert "No conclusion about the knockout follows" in text


def test_a_screened_gene_reports_the_scope_of_its_negative() -> None:
    text = _models(
        {
            "coverage": "covered",
            "viability": "viable",
            "significant_systems": ["homeostasis/metabolism phenotype"],
            "tested_not_significant_systems": [
                "mortality/aging",
                "cardiovascular system phenotype",
            ],
        }
    )
    assert "IMPC: systems with a significant call" in text
    assert "IMPC: screened, no significant call" in text
    assert "a tested negative, limited to these screens" in text
    assert "mortality/aging" in text


def test_the_models_table_no_longer_claims_impc_for_an_open_targets_gap() -> None:
    """The "no knockout phenotyped in MGI/IMPC" line spoke for a source that may hold no
    data for the gene at all."""
    text = _models({"coverage": "not_covered", "coverage_note": "no phenotyping data"})
    assert "No knockout phenotype curated in Open Targets" in text
    assert "MGI/IMPC" not in text


# ------------------------- the human column says which design it rests on


def _hwk(design: str, species: list[str], author: str = "Lab A") -> str:
    """The How-we-know row for a single supporting paper of this design."""
    import profile_writer as writer

    strengths = {
        "1": {
            "pmid": "1",
            "design": design,
            "species": species,
            "last_author": author,
            "n": None,
        }
    }
    md, _ = writer.how_we_know_md(
        [{"claim": "The protein is an ATP-gated channel", "supports": ["pmid:1"]}],
        strengths,
        {"pmid:1"},
    )
    return md.splitlines()[2]


def test_biochemistry_on_a_human_protein_is_marked_human_material() -> None:
    """Vergani 2005 (CFTR single-channel recordings in a heterologous system) is indexed with
    MeSH's Humans check tag and no cell heading, so it landed in the human column as a tick —
    reading as cohort evidence for a claim that rests on electrophysiology."""
    row = _hwk("other primary research", ["human"])
    assert "other primary research (human material)" in row
    assert "largest n" not in row.split("|")[2] or "n not stated" in row


def test_a_study_in_people_keeps_its_own_design() -> None:
    assert "genetic association" in _hwk("genetic association", ["human"])
    assert "human material" not in _hwk("genetic association", ["human"])
    assert "human material" not in _hwk("cohort", ["human"])


def test_the_column_title_no_longer_promises_cohorts_and_tissues() -> None:
    from profile_strength import KIND_TITLES

    assert KIND_TITLES["human"] == "Human / human material"


def test_a_design_outside_the_human_column_is_not_relabelled() -> None:
    from profile_strength import design_label

    assert design_label("other primary research", "animal") == "other primary research"
    assert design_label("cells / in vitro", "cells") == "cells / in vitro"
    assert design_label("", "human") == ""


# ---------------------------- the drugs' label warnings reach the report

# CFTR run, raw/openfda_labels.json: the boxed warnings were retrieved and shown nowhere, so
# the report's safety row read "no curated liability" while its own records held these.
_TRIKAFTA_LABEL: dict[str, Any] = {
    "drug_name": "ELEXACAFTOR, IVACAFTOR, TEZACAFTOR",
    "brand_names": ["TRIKAFTA"],
    "boxed_warning": (
        "WARNING: DRUG-INDUCED LIVER INJURY AND LIVER FAILURE TRIKAFTA can cause serious and "
        "potentially fatal drug-induced liver injury."
    ),
    "warnings": "Assess liver function tests prior to initiating and throughout.",
}
_KALYDECO_LABEL: dict[str, Any] = {
    "drug_name": "IVACAFTOR",
    "brand_names": ["KALYDECO"],
    "warnings": "Elevated transaminases (ALT or AST): assess every 3 months.",
}
_LINZESS_LABEL: dict[str, Any] = {
    "drug_name": "LINACLOTIDE",
    "brand_names": ["LINZESS"],
    "boxed_warning": "WARNING: RISK OF SERIOUS DEHYDRATION IN PEDIATRIC PATIENTS",
}
_CFTR_LANDSCAPE: dict[str, Any] = {
    "drugs": [
        {"name": "ELEXACAFTOR, IVACAFTOR, TEZACAFTOR", "brands": ["TRIKAFTA"]},
        {"name": "IVACAFTOR", "brands": ["KALYDECO"]},
    ]
}


def test_the_boxed_warning_of_a_drug_table_drug_is_reported() -> None:
    text = tables.label_warnings_md(
        [_TRIKAFTA_LABEL, _KALYDECO_LABEL, _LINZESS_LABEL], _CFTR_LANDSCAPE, {"openfda_labels"}
    )
    assert "DRUG-INDUCED LIVER INJURY" in text
    assert "Elevated transaminases" in text
    assert "not a property of the gene" in text


def test_a_label_of_a_drug_outside_the_drug_table_is_left_out() -> None:
    """Linaclotide names CFTR in its mechanism but acts elsewhere: its boxed warning is not
    evidence about modulating the gene."""
    text = tables.label_warnings_md(
        [_TRIKAFTA_LABEL, _LINZESS_LABEL], _CFTR_LANDSCAPE, {"openfda_labels"}
    )
    assert "DEHYDRATION" not in text
    assert tables.label_warnings_md([_LINZESS_LABEL], _CFTR_LANDSCAPE, {"openfda_labels"}) == ""


def test_only_a_drug_table_drugs_boxed_warning_blocks_a_no_flag_claim() -> None:
    found = tables.boxed_warning_drugs(
        [_TRIKAFTA_LABEL, _KALYDECO_LABEL, _LINZESS_LABEL], _CFTR_LANDSCAPE
    )
    assert found == ["Elexacaftor, Ivacaftor, Tezacaftor"]


def test_a_salt_named_label_is_matched_to_its_drug() -> None:
    """BRAF run: openFDA names the label "DABRAFENIB MESYLATE", the drug table "DABRAFENIB";
    the table left the approved BRAF inhibitor's warnings out."""
    label = {
        "drug_name": "DABRAFENIB MESYLATE",
        "brand_names": ["TAFINLAR"],
        "warnings": "New primary malignancies, cutaneous and non-cutaneous: can occur.",
    }
    landscape = {"drugs": [{"name": "DABRAFENIB", "brands": []}]}
    assert tables.in_drug_table(label, landscape)
    text = tables.label_warnings_md([label], landscape, {"openfda_labels"})
    assert "New primary malignancies" in text


def test_labels_with_no_warning_section_make_no_table() -> None:
    labels = [{"drug_name": "IVACAFTOR", "brand_names": []}]
    assert tables.label_warnings_md(labels, _CFTR_LANDSCAPE, {"openfda_labels"}) == ""


def test_the_label_warnings_block_is_required_only_from_template_5() -> None:
    import profile_blocks as blocks

    assert blocks.REQUIRED_V5 == {
        "table:label_warnings": "translational",
        "table:genetic_prevalence": "genetics",
        "table:disease_tissue": "biology",
    }
    assert not set(blocks.REQUIRED_V5) & set(blocks.REQUIRED_V4)


# ---------------------------- which way a drug acts on the target

# CFTR run, raw/ot_known_drugs.json: ivacaftor is a positive modulator and crofelemer an
# inhibitor of the same protein. Both rendered as "acts on the gene's product".
_CFTR_KNOWN: list[dict[str, Any]] = [
    {
        "drug_name": "IVACAFTOR",
        "drug_type": "Small molecule",
        "is_approved": True,
        "max_phase": 4.0,
        "action_type": "POSITIVE MODULATOR",
        "mechanism_of_action": "Cystic fibrosis transmembrane conductance regulator positive modulator",
    },
    {
        "drug_name": "CROFELEMER",
        "drug_type": "Small molecule",
        "is_approved": True,
        "max_phase": 4.0,
        "action_type": "INHIBITOR",
        "mechanism_of_action": "Cystic fibrosis transmembrane conductance regulator inhibitor",
    },
]


def test_the_drug_table_says_which_way_each_drug_acts() -> None:
    rows = drugs.rows(drugs.candidates(_CFTR_KNOWN, [], ["CFTR"]))
    text = drugs.landscape_md({"drugs": rows}, "[@drug_landscape]")
    assert "Action on the target" in text
    ivacaftor = next(ln for ln in text.splitlines() if "Ivacaftor" in ln)
    crofelemer = next(ln for ln in text.splitlines() if "Crofelemer" in ln)
    assert "positive modulator (raises activity)" in ivacaftor
    assert "inhibitor (lowers activity)" in crofelemer


def test_a_missing_action_is_not_stated_rather_than_assumed() -> None:
    rows = drugs.rows(drugs.candidates([{"drug_name": "X", "is_approved": True}], [], ["CFTR"]))
    assert drugs.action_text(rows[0]) == "not stated"
    assert drugs.action_direction("") == ""
    assert drugs.action_direction("MODULATOR") == ""


# ------------------- constraint bands only where the data support them

# HLA-B and CFTR as gnomAD reports them: HLA-B's synonymous Z is beyond ±2, which is gnomAD's
# own flag that the gene's observed/expected figures are distorted — the MHC case.
_HLAB_CONSTRAINT: dict[str, Any] = {
    "loeuf": 1.008, "pli": 0.0, "mis_z": 1.76, "moeuf": 0.88, "syn_z": 2.213,
    "dataset": "gnomAD v4 (GRCh38)", "transcript": "ENST00000412585.7",
}  # fmt: skip
_CFTR_CONSTRAINT: dict[str, Any] = {
    "loeuf": 1.193, "pli": 0.0, "mis_z": -2.39, "moeuf": 1.177, "syn_z": 0.605,
    "dataset": "gnomAD v4 (GRCh38)", "transcript": "ENST00000003084.11",
}  # fmt: skip


def test_a_flagged_gene_gets_its_values_without_a_band() -> None:
    md = tables.constraint_md(_HLAB_CONSTRAINT, {"gnomad_constraint"})
    assert "| LOEUF | 1.01 | not read as a band (data flagged) |" in md
    assert "no strong depletion" not in md
    assert "Synonymous Z (data quality) | 2.21" in md
    assert "synonymous variants should be neutral" in md


def test_an_unflagged_gene_keeps_its_bands_and_shows_the_quality_row() -> None:
    md = tables.constraint_md(_CFTR_CONSTRAINT, {"gnomad_constraint"})
    assert "| LOEUF | 1.19 | no strong depletion of pLoF variants |" in md
    assert "within the range where the bands above hold" in md


def test_a_negative_missense_z_is_an_excess_not_an_absence() -> None:
    md = tables.constraint_md(_CFTR_CONSTRAINT, {"gnomad_constraint"})
    assert "more missense variants than expected" in md


def test_a_record_without_a_synonymous_z_keeps_its_bands() -> None:
    md = tables.constraint_md(
        {k: v for k, v in _CFTR_CONSTRAINT.items() if k != "syn_z"}, {"gnomad_constraint"}
    )
    assert "no strong depletion of pLoF variants" in md
    assert "data flagged" not in md


# ------------- expression read against the organs the disease affects

# CFTR run, raw/gtex.json: pancreas is the top tissue and lung, which defines the disease,
# is rank 5 of 54 — absent from any top-three display.
_CFTR_GTEX: dict[str, Any] = {
    "gtex_expressions": [
        {"tissue": "Pancreas", "median_tpm": 65.792},
        {"tissue": "Colon_Transverse", "median_tpm": 17.316},
        {"tissue": "Minor_Salivary_Gland", "median_tpm": 9.91875},
        {"tissue": "Lung", "median_tpm": 2.35699},
        {"tissue": "Liver", "median_tpm": 0.561802},
        {"tissue": "Spleen", "median_tpm": 0.0407},
        {"tissue": "Whole_Blood", "median_tpm": 0.0},
    ]
}
_CFTR_HPO_TERMS: dict[str, Any] = {
    "count": 36,
    "organ_systems": {"respiratory system": 9, "digestive system": 8, "immune system": 6},
}


def _disease_tissue(hpo: dict[str, Any], gtex: dict[str, Any]) -> str:
    from profile_biology import disease_tissue_md

    return disease_tissue_md(hpo, gtex, {"hpo_terms", "gtex"})


def test_the_disease_organ_appears_with_its_rank_even_when_it_is_not_a_top_tissue() -> None:
    text = _disease_tissue(_CFTR_HPO_TERMS, _CFTR_GTEX)
    lung = next(ln for ln in text.splitlines() if "respiratory" in ln)
    assert "Lung 2.36" in lung
    assert "rank 4 of 7" in lung  # its rank among all tissues measured, not of the shown rows


def test_a_low_value_in_an_affected_organ_is_shown_not_hidden() -> None:
    """CFTR's immune phenotypes sit beside near-zero expression in immune tissue, which tells
    the reader they are secondary rather than the gene acting in those cells."""
    text = _disease_tissue(_CFTR_HPO_TERMS, _CFTR_GTEX)
    immune = next(ln for ln in text.splitlines() if "immune" in ln)
    assert "Spleen 0.04" in immune and "Whole Blood 0.00" in immune
    assert "a low value does not exclude a role" in text


def test_an_organ_system_with_no_gtex_tissue_says_so() -> None:
    text = _disease_tissue({"count": 2, "organ_systems": {"eye": 1}}, _CFTR_GTEX)
    assert "no GTEx tissue maps to this organ system" in text


def test_no_organ_systems_or_no_expression_renders_nothing() -> None:
    assert _disease_tissue({"count": 0, "organ_systems": {}}, _CFTR_GTEX) == ""
    assert _disease_tissue(_CFTR_HPO_TERMS, {"gtex_expressions": []}) == ""


def test_the_tissue_table_names_the_cut_and_the_floor() -> None:
    md = tables.tissue_evidence_md({}, _CFTR_GTEX, {"gtex", "hpa"})
    assert "top 3 of 7 tissues measured" in md
    assert "lowest 0.00 TPM" in md
