# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oligonucleotide programmes found only in the trial registry become known-drug rows."""

from __future__ import annotations

from services.evidence.drug_precedent import assess_precedent, drug_effect
from services.evidence.registry_programmes import oligo_drug, registry_oligo_rows

# Synthetic records shaped like the HSD17B13 registry entries of the 2026-10-26 review.
_SIRNA = {
    "nct_id": "NCT1",
    "title": "Phase 2b Study of DRUG-1 in Adults With Disease One",
    "brief_summary": "DRUG-1 is a small interfering RNA (siRNA) targeting GENE-A mRNA.",
    "interventions": ["DRUG-1", "Placebo"],
    "phase": "PHASE2",
    "status": "ACTIVE_NOT_RECRUITING",
}
_KNOCKDOWN = {
    "nct_id": "NCT2",
    "title": "Knockdown of GENE-A mRNA, Safety and Tolerability of DRUG-2",
    "interventions": ["DRUG-2 Intervention"],
    "phase": "PHASE1",
    "status": "COMPLETED",
}
_STEM = {
    "nct_id": "NCT3",
    "title": "Examplesiran, a silencer of GENE-A, in carriers",
    "interventions": ["Placebo", "examplesiran"],
    "phase": "PHASE1",
    "status": "RECRUITING",
}
_SMALL_MOLECULE = {
    "nct_id": "NCT4",
    "title": "DRUG-3, an oral GENE-A inhibitor",
    "interventions": ["DRUG-3"],
    "phase": "PHASE2",
    "status": "RECRUITING",
}
_OTHER_GENE = {
    "nct_id": "NCT5",
    "title": "An siRNA against GENE-B",
    "interventions": ["DRUG-4"],
    "phase": "PHASE2",
    "status": "RECRUITING",
}


def test_an_oligonucleotide_of_the_target_is_recognised_from_its_record():
    assert oligo_drug(_SIRNA, "GENE-A") == "DRUG-1"
    assert oligo_drug(_KNOCKDOWN, "GENE-A") == "DRUG-2 Intervention"
    assert oligo_drug(_STEM, "GENE-A") == "examplesiran"
    assert oligo_drug(_SMALL_MOLECULE, "GENE-A") == ""  # not an oligonucleotide
    assert oligo_drug(_OTHER_GENE, "GENE-A") == ""  # does not name the target


def _rows(**kw):
    base = dict(
        gene="GENE-A",
        disease="disease one",
        disease_id="D1",
        in_indication={"NCT1": True, "NCT2": True, "NCT3": False},
        known_names=[],
    )
    return registry_oligo_rows([_SIRNA, _KNOCKDOWN, _STEM], **{**base, **kw})


def test_rows_lower_the_target_and_carry_their_trials():
    rows = {r["drug_name"]: r for r in _rows()}
    sirna = rows["DRUG-1"]
    assert drug_effect(sirna["action_type"]) == "decrease"
    assert (sirna["max_phase"], sirna["trial_status"]) == (2.0, "ACTIVE_NOT_RECRUITING")
    assert (sirna["disease_id"], sirna["registry_trials"]) == ("D1", ["NCT1"])
    assert rows["EXAMPLESIRAN"]["disease_id"] == ""  # outside the indication


def test_a_drug_open_targets_lists_is_left_to_its_row():
    names = {r["drug_name"] for r in _rows(known_names=["DRUG-1"])}
    assert "DRUG-1" not in names


def test_the_precedent_sees_a_registry_programme_in_the_indication():
    """HSD17B13: "untested" although phase 2 RNAi trials ran in the indication."""
    reading = assess_precedent(
        _rows(), direction="degrade", disease="disease one", scope_ids={"D1"}
    )
    assert reading.status != "untested"
    assert "DRUG-1" in [d.drug for d in reading.concordant]


def test_a_record_naming_the_gene_beside_another_drug_class_is_not_its_oligonucleotide():
    """Pelacarsen, an apolipoprotein(a) antisense, read as a PCSK9 programme."""
    record = {
        "title": "Influence of Pelacarsen on Patients After Myocardial Infarction",
        "brief_summary": (
            "Besides GENE-A inhibitors, antisense oligonucleotides are the only agents that "
            "reduce the marker. DRUG-9 uses an ASO directed against the mRNA of another gene."
        ),
        "interventions": ["DRUG-9"],
    }
    assert oligo_drug(record, "GENE-A") == ""


def test_a_splice_restoring_oligonucleotide_or_a_procedure_is_not_one():
    """CFTR: splice-correcting oligonucleotides raise the target, and "nasal cells
    sampling" is a procedure."""
    restoring = {
        "title": "Oligonucleotide therapy to restore full length GENE-A mRNA",
        "interventions": ["DRUG-8"],
    }
    procedure = {
        "title": "Knockdown of GENE-A mRNA ex vivo",
        "interventions": ["Nasal cells sampling"],
    }
    assert oligo_drug(restoring, "GENE-A") == ""
    assert oligo_drug(procedure, "GENE-A") == ""


def test_a_sponsor_code_carrying_the_gene_name_aims_the_silencing():
    """ALN-HSD for HSD17B13: the record says "gene silencing" without the gene symbol."""
    record = {
        "title": "Using Gene Silencing to Treat a Chronic Liver Disease",
        "interventions": ["ALN-HSD", "Placebo"],
    }
    assert oligo_drug(record, "HSD17B13") == "ALN-HSD"


def test_every_trial_of_a_qualified_drug_counts():
    silencing = {
        "nct_id": "NCT10",
        "title": "Using Gene Silencing to Treat a Liver Disease",
        "interventions": ["ALN-HSD"],
        "phase": "PHASE2",
        "status": "RECRUITING",
    }
    plain = {
        "nct_id": "NCT11",
        "title": "A Study of ALN-HSD",
        "interventions": ["ALN-HSD"],
        "phase": "PHASE1",
        "status": "TERMINATED",
    }
    rows = registry_oligo_rows(
        [silencing, plain],
        gene="HSD17B13",
        disease="d",
        disease_id="D1",
        in_indication={"NCT10": True, "NCT11": True},
        known_names=[],
    )
    assert [r["registry_trials"] for r in rows] == [["NCT10", "NCT11"]]
