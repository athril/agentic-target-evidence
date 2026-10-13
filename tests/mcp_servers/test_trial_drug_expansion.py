# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trial retrieval by the target's drug names.

Trial records name the drug, rarely the gene, so a gene-keyword search missed the
landmark outcome trials of PCSK9 (ODYSSEY OUTCOMES, VESALIUS-CV) and most trials of
TRPC6's only clinical asset (BI 764198, which no drug database lists). Drug names now
come from the target's known drugs plus the investigational products of the
gene-matched trials.
"""

from __future__ import annotations

import pytest

from mcp_servers.clinicaltrials.tools import TrialRecord, _is_product_name, investigational_drugs
from mcp_servers.opentargets.tools import KnownDrug, KnownDrugsBundle
from services.retrieval import clinical_trial
from services.retrieval.clinical_trial import (
    _MAX_DRUG_QUERIES,
    _MAX_KNOWN_DRUGS,
    _drug_key,
    drug_query_names,
)


def _rec(nct: str, title: str, interventions: list[str]) -> TrialRecord:
    return TrialRecord(nct_id=nct, title=title, interventions=interventions)


@pytest.mark.parametrize(
    "name,gene,ok",
    [
        ("BI 764198", "TRPC6", True),  # sponsor code (small molecule)
        ("AZD0780", "PCSK9", True),
        ("ALN-PCSSC", "PCSK9", True),  # one-token sponsor code (inclisiran)
        ("evolocumab", "PCSK9", True),  # antibody INN
        ("osimertinib", "EGFR", True),  # kinase inhibitor INN
        ("semaglutide", "GLP1R", True),  # peptide agonist INN
        ("PCSK9 inhibitor", "PCSK9", False),  # class term + gene symbol
        ("Statin", "PCSK9", False),
        ("Chemotherapy", "EGFR", False),
        ("GLP-1 receptor agonist", "GLP1R", False),
        ("anti-IL-23 antibody", "IL23A", False),
        ("18F-DTBZ", "LRRK2", False),  # PET tracer
        ("[18F]MNI-444", "LRRK2", False),
        ("BIIB122", "LRRK2", True),
        ("Trehalose", "GENE-A", False),  # plain substance tested in a disease trial
        ("Glutathione", "GENE-A", False),
        ("sparsentan", "GENE-A", True),  # -sentan
        ("inclisiran", "GENE-A", True),  # -siran
        ("tofersen", "GENE-A", True),  # antisense -rsen
        ("TREHALOSE", "GENE-A", False),  # capitals alone do not make a code
    ],
)
def test_product_names(name, gene, ok):
    assert _is_product_name(name, gene) is ok


def test_investigational_product_must_be_in_the_title_and_not_a_comparator():
    records = [
        _rec("N1", "A Study to Test BI 764198 in FSGS", ["BI 764198", "Placebo"]),
        _rec("N2", "PODOMOUNT-Basket: BI 764198 in Proteinuric Kidney Disease", ["BI 764198"]),
        # background therapy not named in the title is not an investigational product
        _rec("N3", "Cardiac assessment in FSGS", ["Lisinopril", "Echocardiogram"]),
    ]
    assert investigational_drugs(records, gene="TRPC6") == ["BI 764198"]


def test_ranked_by_number_of_matched_trials():
    records = [
        _rec(
            "N1", "Osimertinib versus gefitinib in EGFR-mutant NSCLC", ["Osimertinib", "Gefitinib"]
        ),
        _rec("N2", "Adjuvant osimertinib", ["Osimertinib", "Placebo"]),
        _rec("N3", "Amivantamab plus lazertinib", ["Amivantamab", "Lazertinib"]),
    ]
    assert investigational_drugs(records, gene="EGFR")[0] == "Osimertinib"


def test_salt_forms_and_case_are_one_drug():
    assert _drug_key("INCLISIRAN SODIUM") == _drug_key("inclisiran")
    names = drug_query_names(
        ["INCLISIRAN", "EVOLOCUMAB"],
        [_rec("N1", "Inclisiran sodium in ASCVD", ["Inclisiran sodium"])],
        "PCSK9",
    )
    assert names == ["INCLISIRAN", "EVOLOCUMAB"]


def test_gene_with_no_known_drugs_still_gets_its_clinical_asset():
    records = [_rec("N1", "A Study of XYZ-123 in Disease", ["XYZ-123", "Placebo"])]
    assert drug_query_names([], records, "GENE1") == ["XYZ-123"]


def test_query_count_is_capped():
    known = [f"drug{i}mab" for i in range(10)]
    assert len(drug_query_names(known, [], "G")) == _MAX_DRUG_QUERIES


def test_comparator_next_to_a_known_drug_is_not_a_product():
    """PCSK9 trial titles pair evolocumab with ezetimibe; ezetimibe is not a PCSK9 drug."""
    records = [
        _rec(
            "N1",
            "Evolocumab versus ezetimibe in statin-intolerant patients",
            ["Evolocumab", "Ezetimibe"],
        ),
        _rec("N2", "Alirocumab or ezetimibe after ACS", ["Alirocumab", "Ezetimibe"]),
        _rec("N3", "AZD0780 in hypercholesterolaemia", ["AZD0780", "Placebo"]),
    ]
    names = drug_query_names(["EVOLOCUMAB", "ALIROCUMAB"], records, "PCSK9")
    assert "Ezetimibe" not in names
    assert "AZD0780" in names


def test_prefixed_salt_forms_are_one_drug():
    assert _drug_key("DRUGATINIB DIMALEATE") == _drug_key("drugatinib")
    assert _drug_key("DRUGANIB MESYLATE MONOHYDRATE") == _drug_key("druganib")


def _kd(name: str, disease: str, approved: bool = True, phase: float = 4.0) -> KnownDrug:
    return KnownDrug(
        drug_id=name,
        drug_name=name,
        drug_type="Small molecule",
        max_phase=phase,
        is_approved=approved,
        mechanism_of_action="",
        action_type="INHIBITOR",
        disease_name=disease,
        disease_id=f"ID_{disease}",
        trial_status="",
    )


async def test_known_drugs_in_the_run_disease_rank_first(monkeypatch):
    """Alphabetical order among approved drugs dropped a target's main drugs from the
    capped list; drugs with an indication row for the run's disease now come first."""
    rows = [
        # Many indications, none of them the run's disease.
        *[_kd(f"AAA-{i}TINIB", f"other cancer {i}") for i in range(_MAX_KNOWN_DRUGS)],
        _kd("ZZZTINIB", "disease a"),
        _kd("ZZZTINIB DIMALEATE", "disease a"),
        _kd("YYYMAB", "disease a", approved=False, phase=3.0),
    ]

    async def fake(gene_id: str) -> KnownDrugsBundle:
        return KnownDrugsBundle(gene_id=gene_id, drugs=rows)

    monkeypatch.setattr("mcp_servers.opentargets.tools.get_known_drugs", fake)
    names = await clinical_trial.known_drug_names("ENSG0", "Disease A")
    assert names[:2] == ["ZZZTINIB", "YYYMAB"]
    assert len(names) == _MAX_KNOWN_DRUGS


def test_drug_named_in_ignores_case_spaces_and_hyphens():
    from mcp_servers.clinicaltrials.tools import drug_named_in

    r = TrialRecord(nct_id="N1", title="A study in DISEASE-A", interventions=["BIIB-094"])
    assert drug_named_in(r, "BIIB094")
    aliased = TrialRecord(
        nct_id="N2", title="x", interventions=["Tablet"], intervention_aliases=["Drugatinib"]
    )
    assert drug_named_in(aliased, "drugatinib")
    vaccine = TrialRecord(
        nct_id="N3", title="Live attenuated vaccine in healthy adults", interventions=["Vaccine"]
    )
    assert not drug_named_in(vaccine, "BIIB094")


async def test_drug_query_trials_are_attributed_only_when_they_name_the_drug(monkeypatch):
    """The fallback labelled any drug-query trial with the first drug, so an unrelated
    vaccine trial became "drug:<code>" and skipped screening as target evidence."""

    async def no_gene_trials(*a, **k):
        return []

    async def no_known(*a, **k):
        return ["DRUGA"]

    async def drug_trials(drugs, disease):
        return [
            TrialRecord(
                nct_id="N1",
                title="DRUGA in DISEASE-A",
                interventions=["DrugA"],
                query_drug="DRUGA",
                query_in_disease=True,
            ),
            TrialRecord(
                nct_id="N2",
                title="AB-12 in DISEASE-A",
                interventions=["AB-12"],
                query_drug="DRUGA",
                query_in_disease=True,
            ),
            TrialRecord(
                nct_id="N3",
                title="Vaccine in healthy adults",
                interventions=["Vaccine"],
                query_drug="DRUGA",
                query_in_disease=False,
            ),
        ]

    monkeypatch.setattr(clinical_trial, "search_trials", no_gene_trials)
    monkeypatch.setattr(clinical_trial, "known_drug_names", no_known)
    monkeypatch.setattr(clinical_trial, "search_trials_for_drugs", drug_trials)
    records, matched_by = await clinical_trial.collect_trial_records("GENE-A", "DISEASE-A")
    assert [r.nct_id for r in records] == ["N1", "N2"]
    assert matched_by == {"N1": "drug:DRUGA", "N2": "registry:DRUGA"}
