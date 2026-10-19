# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for errors found in the 2026-10 PCSK9 × MI and TRPC6 × FSGS reruns,
written against several gene/disease classes so the fixes do not tune to one target."""

from __future__ import annotations

import pytest

from agents.retrieval.literature.agent import target_level_queries
from mcp_servers.gwas_catalog.tools import implausible_for_sample_size, sample_size
from mcp_servers.opentargets.tools import KnownDrug, unique_drug_counts
from services.evidence.fact_checks import fact_checks


# ---------------------------------------------------------------- GWAS plausibility
@pytest.mark.parametrize(
    "size,log10_p,flag",
    [
        ("5,862 South Asian ancestry individuals", 300, True),  # artefact seen for TRPC6
        ("5,862 South Asian ancestry individuals", 244, True),  # … and for C1orf87
        ("1,654,960 European ancestry individuals", 300, False),  # PCSK9 LDL, real
        ("2,000 cases, 3,000 controls", 20, False),  # ordinary small case-control hit
        ("3,500 European ancestry individuals", 150, True),
        ("", 300, False),  # unknown sample size is never flagged
    ],
)
def test_gwas_p_value_plausibility(size, log10_p, flag):
    assert implausible_for_sample_size(log10_p, sample_size(size)) is flag


def test_sample_size_sums_cases_and_controls():
    assert sample_size("50,775 European ancestry cases, 270,269 European ancestry controls") == (
        321_044
    )


# ------------------------------------------------------------- unique known drugs
def _kd(name: str, phase: float, approved: bool, disease: str) -> KnownDrug:
    return KnownDrug(
        drug_id=name,
        drug_name=name,
        drug_type="Antibody",
        max_phase=phase,
        is_approved=approved,
        mechanism_of_action="",
        action_type="INHIBITOR",
        disease_name=disease,
        disease_id="X",
        trial_status="",
    )


def test_known_drug_counts_are_distinct_drugs_not_indication_rows():
    """PCSK9 became "10 known drugs (72 approved)" — rows are drug × indication."""
    rows = [_kd("EVOLOCUMAB", 4, True, f"d{i}") for i in range(30)]
    rows += [_kd("INCLISIRAN", 4, True, "d"), _kd("INCLISIRAN SODIUM", 4, True, "d")]
    rows += [_kd("BOCOCIZUMAB", 3, False, f"d{i}") for i in range(6)]
    rows += [_kd("FROVOCIMAB", 2, False, "d")]
    assert unique_drug_counts(rows) == {"total": 4, "approved": 2, "phase3": 1}


# ---------------------------------------------------------- approval denial check
@pytest.mark.parametrize(
    "gene,disease,sentence,fires",
    [
        (
            "PCSK9",
            "myocardial infarction",
            "All these drugs reduce LDL-C but none have been approved specifically for myocardial infarction (MI).",
            True,
        ),
        (
            "PCSK9",
            "myocardial infarction",
            "Evolocumab is approved to reduce the risk of myocardial infarction.",
            False,
        ),
        (
            "EGFR",
            "non-small cell lung cancer",
            "No approved drugs for non-small cell lung cancer target EGFR.",
            True,
        ),
        (
            "TRPC6",
            "focal segmental glomerulosclerosis",
            "BI 764198 has not been approved specifically for focal segmental glomerulosclerosis.",
            True,
        ),
    ],
)
def test_indication_approval_denial_patterns(gene, disease, sentence, fires):
    check = next(c for c in fact_checks() if c.id == "indication_approval_denied")
    assert bool(check.denial_re(gene, disease).search(sentence.lower())) is fires


# ------------------------------------------------------ target-level literature
@pytest.mark.parametrize(
    "gene,drugs,roles",
    [
        (
            "PCSK9",
            ["EVOLOCUMAB", "ALIROCUMAB"],
            {"target_genetics", "target_human_genetics", "target_safety", "target_drug_trials"},
        ),
        (
            "TRPC6",
            [],
            {"target_genetics", "target_human_genetics", "target_safety"},
        ),  # no known drugs
        (
            "LRRK2",
            ["BIIB122"],
            {"target_genetics", "target_human_genetics", "target_safety", "target_drug_trials"},
        ),
    ],
)
def test_target_level_queries_do_not_depend_on_the_disease(gene, drugs, roles):
    queries = target_level_queries(gene, drugs)
    assert set(queries) == roles
    assert all(
        f'"{gene}"[tiab]' in q or f'"{gene}"[ti]' in q
        for r, q in queries.items()
        if r != "target_drug_trials"
    )
    if drugs:
        assert all(f'"{d}"[tiab]' in queries["target_drug_trials"] for d in drugs)
    assert "mendelian randomization" in queries["target_genetics"]


def test_indication_trial_query_names_many_drugs_and_the_target_at_any_date():
    """A molecule tested in the indication only before the registry ranks below the
    target's approved drugs: the first six names left it out, and the gene query's date
    scope dropped trials from before 2000."""
    from agents.retrieval.literature.agent import indication_drug_trial_query

    drugs = [f"DRUG-{i}" for i in range(12)]
    query = indication_drug_trial_query(drugs, "disease one", "Disease One", gene="GENE-A")

    assert all(f'"{d}"[tiab]' in query for d in drugs)
    assert '"GENE-A"[tiab]' in query and '"Disease One"[MeSH Terms]' in query
    assert "[pdat]" not in query


def test_indication_trial_query_runs_for_a_target_without_known_drugs():
    from agents.retrieval.literature.agent import indication_drug_trial_query

    assert '"GENE-A"[tiab]' in indication_drug_trial_query([], "disease one", None, gene="GENE-A")
    assert indication_drug_trial_query([], "disease one", None) == ""


# ------------------------------------------- knowledge-graph edges and Mendelian grade
@pytest.mark.parametrize(
    "graph,grade",
    [
        # PCSK9 × MI: a DISEASES text-mining + GWAS edge is association evidence
        (
            {
                "edge_sources": ["DISEASES", "GWAS"],
                "diseases_score": 6.45,
                "corroborates_causality": True,
                "mendelian_sources": False,
            },
            False,
        ),
        # a ClinVar/HPO edge is Mendelian evidence
        (
            {
                "edge_sources": ["ClinVar", "HPO"],
                "diseases_score": 6.45,
                "corroborates_causality": True,
                "mendelian_sources": True,
            },
            True,
        ),
    ],
)
def test_only_curated_mendelian_graph_edges_make_a_pair_mendelian(graph, grade):
    from services.evidence.constraint_interpret import mendelian_grade_from_signals

    assert mendelian_grade_from_signals({"graph_association": graph}) is grade


# ------------------------------------------------------ ClinVar conflicting bucket
def test_conflicting_clinvar_entries_are_not_pathogenic():
    from mcp_servers.gnomad.tools import ClinVarBundle

    assert "conflicting" in ClinVarBundle.model_fields


# --------------------------------------------------------- investigator relevance
def test_investigator_trims_a_drifted_answer_but_not_a_focused_one():
    from agents.synthesis.investigator.agent import trim_off_target

    drifted = "\n\n".join(
        [
            "### 1. AZD0585 in Japan\nTriglycerides fell 22% versus placebo.",
            "### 2. AEM-28 safety\n33 participants had adverse events.",
            "### 3. Psyllium husk\nLDL fell modestly.",
            "**Assessment:** evolocumab evidence is unchanged.",
        ]
    )
    out = trim_off_target(drifted, "PCSK9", "myocardial infarction", ["EVOLOCUMAB"])
    assert "AZD0585" not in out and "Psyllium" not in out
    assert "RELEVANCE TRIM: 3 block(s)" in out
    focused = "FOURIER confirms the MACE reduction."
    assert trim_off_target(focused, "PCSK9", "myocardial infarction", []) == focused


# ------------------------------------------------------- eponymous disease names
@pytest.mark.parametrize(
    "text,disease,hit",
    [
        ("indicated for the treatment of Parkinson's disease", "Parkinson disease", True),
        ("moderately to severely active Crohn's disease", "Crohn disease", True),
        ("Wolff-Parkinson-White syndrome", "Parkinson disease", False),
        ("to reduce the risk of myocardial infarction", "myocardial infarction", True),
        ("focal segmental glomerulosclerosis (FSGS)", "focal segmental glomerulosclerosis", True),
    ],
)
def test_disease_mentions_accept_possessive_eponyms(text, disease, hit):
    from core.evidence_text import mentions_disease

    assert mentions_disease(text, disease) is hit


def test_openfda_indication_phrases_include_the_label_form():
    from mcp_servers.openfda.tools import indication_phrases

    assert indication_phrases("Parkinson disease") == ["Parkinson disease", "Parkinson's disease"]
    assert indication_phrases("myocardial infarction") == ["myocardial infarction"]
