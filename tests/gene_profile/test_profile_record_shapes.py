# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 fixes for the 2026-11 testing plan, each on the record shape a real run produced:
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


# --------------------------------------------- a source may not report a negative (G1)

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
