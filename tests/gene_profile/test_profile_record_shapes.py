# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 fixes for the 2026-11 testing plan, each on the record shape a real run produced:
a label-only drug named as direct, pharmacogenetic boxed warnings, FAERS cuts, both dosage
directions, records that answered with no value, and the matrix's gated-off sources.

Offline: the records are fixtures copied from raw/ files of the testing runs. Gene names
are the real ones, since the rules key on them."""

from __future__ import annotations

import asyncio
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

# ------------------------------------------------ direct target vs label-only (BRCA1)

# BRCA1 run, openfda_labels: the mechanism names PARP first, and BRCA1 only as a defect in
# the cells the drug kills. Open Targets lists no BRCA1 drug.
_TALAZOPARIB_LABEL: dict[str, Any] = {
    "drug_name": "TALAZOPARIB",
    "brand_names": ["Talzenna"],
    "mechanism_of_action": (
        "12.1 Mechanism of Action Talazoparib is an inhibitor of PARP enzymes, including PARP1 "
        "and PARP2, which play a role in DNA repair. In vitro studies with cancer cell lines that "
        "harbored defects in DNA repair genes, including BRCA1 and BRCA2, have shown that "
        "talazoparib-induced cytotoxicity may involve inhibition of PARP enzymatic activity."
    ),
}
_APPROVAL: dict[str, Any] = {
    "application_number": "NDA211651",
    "approval_date": "2018-10-16",
    "sponsor": "PFIZER",
    "brand_names": ["TALZENNA"],
    "ingredients": ["TALAZOPARIB"],
    "route": "ORAL",
}


def _landscape(known: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    entries = drugs.candidates(known, labels, ["BRCA1"])
    rows = drugs.rows(entries, {k: _APPROVAL if k == "TALAZOPARIB" else None for k in entries})
    return {"drugs": rows}


def _relation_rows(text: str, relation: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(f"| {relation} |")]


def test_a_label_alone_is_not_a_direct_target() -> None:
    rows = _landscape([], [_TALAZOPARIB_LABEL])["drugs"]
    assert [r["name"] for r in rows] == ["TALAZOPARIB"]
    assert rows[0]["target_linked"] is False


def test_a_target_listed_by_target_linked_stays_direct() -> None:
    known = [{"drug_name": "TALAZOPARIB", "max_phase": 4.0, "is_approved": True}]
    rows = _landscape(known, [_TALAZOPARIB_LABEL])["drugs"]
    assert rows[0]["target_linked"] is True


def test_the_table_files_a_label_only_drug_under_pathway_not_direct() -> None:
    landscape = _landscape([], [_TALAZOPARIB_LABEL])
    text = drugs.relevance_md(landscape, {}, "[@drug_landscape]", lambda _n: "")
    assert _relation_rows(text, drugs.RELATIONS["direct"]) == []
    (row,) = _relation_rows(text, drugs.RELATIONS["pathway"])
    assert "Open Targets does not list it as a target" in row


# ------------------------------------------- pharmacogenetic boxed warnings (HLA-B)

# HLA-B run: openFDA boxed warning of abacavir (the HLA-B*5701 sentence is the second).
_ABACAVIR_BOXED: dict[str, Any] = {
    "drug_name": "ABACAVIR",
    "brand_names": ["Abacavir"],
    "boxed_warning": (
        "WARNING: HYPERSENSITIVITY REACTIONS Serious and sometimes fatal hypersensitivity "
        "reactions, with multiple organ involvement, have occurred with abacavir. Patients who "
        "carry the HLA-B*5701 allele are at a higher risk of a hypersensitivity reaction to "
        "abacavir; although, hypersensitivity reactions have occurred in patients who do not "
        "carry the HLA-B*5701 allele."
    ),
}


def test_a_boxed_warning_naming_the_gene_is_a_genotype_row() -> None:
    (row,) = drugs.genotype_rows([_ABACAVIR_BOXED], ["HLA-B"])
    assert row["name"] == "ABACAVIR"
    assert "Patients who carry the HLA-B*5701 allele" in row["basis"]


def test_a_boxed_warning_not_naming_the_gene_is_left_out() -> None:
    assert drugs.genotype_rows([_ABACAVIR_BOXED], ["CFTR"]) == []


def test_a_gene_symbol_is_matched_with_its_case() -> None:
    # Denosumab's boxed warning says "eGFR" (kidney function): not the gene EGFR.
    denosumab = {"drug_name": "DENOSUMAB", "boxed_warning": "Patients with advanced chronic "
                 "kidney disease (eGFR < 30 mL/min/1.73 m 2) are at greater risk."}  # fmt: skip
    assert drugs.genotype_rows([denosumab], ["EGFR"]) == []


def test_a_long_boxed_warning_keeps_the_sentence_naming_the_gene() -> None:
    # Lamotrigine's boxed warning names HLA-B*1502 in its last clause, past any fixed cut.
    lamotrigine = {"drug_name": "LAMOTRIGINE", "boxed_warning": (
        "WARNING: SERIOUS SKIN RASHES. " + "In addition to age, factors may increase the risk "
        "of rash, including coadministration with valproate and exceeding the recommended dose. " * 3
        + "The risk is also raised by (4) the presence of the HLA-B*1502 allele."
    )}  # fmt: skip
    (row,) = drugs.genotype_rows([lamotrigine], ["HLA-B"])
    assert "HLA-B*1502 allele" in row["basis"]


def test_a_combination_named_by_its_first_mechanism_sentence_is_target_linked() -> None:
    # CFTR run: Trikafta's ingredients are not in Open Targets under the gene's name; the
    # mechanism's first sentence names the gene, so the drug acts on it.
    trikafta = {"drug_name": "ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR", "mechanism_of_action":
                "Elexacaftor, tezacaftor and ivacaftor are CFTR modulators that improve the "
                "function of the CFTR protein."}  # fmt: skip
    rows = drugs.rows(drugs.candidates([], [trikafta], ["CFTR"]), {})
    assert rows[0]["target_linked"] is True


def test_the_table_shows_the_genotype_relation_with_the_boxed_warning_text() -> None:
    data = {"genotype": drugs.genotype_rows([_ABACAVIR_BOXED], ["HLA-B"])}
    text = drugs.relevance_md({"drugs": []}, data, "[@therapy_relevance]", lambda _n: "")
    (row,) = _relation_rows(text, drugs.RELATIONS["genotype"])
    assert "HLA-B*5701" in row


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


# ----------------------------------------- gated-off sources in the matrix (rule 4)


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


# ------------------------------------ approval of a combination by its brand (CFTR)


def test_a_combination_without_an_ingredient_match_is_approved_by_its_brand(monkeypatch) -> None:
    asked: list[str] = []

    async def fake_lookup(name: str) -> dict[str, str] | None:
        asked.append(name)
        return {"application_number": "NDA212273"} if name == "TRIKAFTA" else None

    monkeypatch.setattr(gather.openfda, "search_fda_approval", fake_lookup)
    found = asyncio.run(gather.approval_for("ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR", ["TRIKAFTA"]))
    assert found == {"application_number": "NDA212273"}
    assert asked == ["ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR", "TRIKAFTA"]


# ------------------------------- a direct drug that also selects by the gene (CFTR)

# CFTR run, raw/openfda_labels.json: ivacaftor acts on CFTR and is indicated for patients with
# a CFTR mutation. It was missing from the biomarker rows because it was already direct.
_KALYDECO_INDICATION = (
    "1 INDICATIONS AND USAGE KALYDECO is indicated for the treatment of cystic fibrosis (CF) in "
    "patients aged 1 month and older who have at least one mutation in the CFTR gene that is "
    "responsive to ivacaftor potentiation based on in vitro and/or clinical data."
)


def test_a_direct_drug_that_selects_by_the_gene_is_also_a_biomarker_row() -> None:
    ivacaftor = {"drug_name": "IVACAFTOR", "brand_names": ["Kalydeco"],
                 "indications_and_usage": _KALYDECO_INDICATION,
                 "mechanism_of_action": "Ivacaftor is a CFTR potentiator that increases channel open probability."}  # fmt: skip
    selected, pathway = drugs.relate_labels([ivacaftor], ["CFTR"], direct=["IVACAFTOR"])
    assert [r["name"] for r in selected] == ["IVACAFTOR"]
    assert "at least one mutation in the CFTR gene" in selected[0]["basis"]
    assert pathway == []


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
