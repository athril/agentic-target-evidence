# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Mendelian curation counts only for the run's indication.

PCSK9 × myocardial infarction: GenCC/ClinGen "Definitive" for *hypercholesterolemia,
autosomal dominant, 3* and gene-wide ClinVar P/LP counts made the pair
"Mendelian-grade", so the genetics lens wrote "the gene-disease pair has a Definitive
classification by ClinGen" and the causality axis was clamped to favourable — the same
would happen for any gene run against any disease. Curation is now scoped by ontology id
(the run disease and its descendants) or, failing ids, by disease name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from capabilities.target_validation.workflow import _genetics_floor_signals
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from services.evidence.constraint_interpret import mendelian_grade_from_signals
from services.evidence.disease_scope import (
    call_scope,
    in_scope,
    names_match,
    normalize_curie,
    strongest_in_scope,
)


@pytest.mark.parametrize(
    "raw,norm",
    [
        ("MONDO:0011369", "MONDO_0011369"),
        ("mondo_0011369", "MONDO_0011369"),
        ("http://purl.obolibrary.org/obo/MONDO_0011369", "MONDO_0011369"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_curie(raw, norm):
    assert normalize_curie(raw) == norm


@pytest.mark.parametrize(
    "a,b,ok",
    [
        ("focal segmental glomerulosclerosis 2", "Focal Segmental Glomerulosclerosis", True),
        ("Parkinson disease 8, autosomal dominant", "Parkinson disease", True),
        ("hypercholesterolemia, autosomal dominant, 3", "myocardial infarction", False),
        ("homozygous familial hypercholesterolemia", "hypercholesterolemia", True),
        ("breast-ovarian cancer, familial, 1", "breast cancer", False),  # not contiguous
        ("cancer", "lung cancer", False),  # too generic to name-match
        ("type 2 diabetes mellitus", "diabetes mellitus", True),
    ],
)
def test_names_match(a, b, ok):
    assert names_match(a, b) is ok


def test_id_match_beats_name_mismatch():
    # FSGS2 curated under its MONDO id, run disease scope includes it as a descendant.
    assert in_scope(
        "MONDO:0011398",
        "FSGS2",
        scope_ids={"MONDO_0100313", "MONDO_0011398"},
        disease_name="focal segmental glomerulosclerosis",
    )


def test_strongest_in_scope_picks_the_indication_not_the_strongest_overall():
    assocs = [
        {"disease_title": "disorder X", "disease_curie": "MONDO:1", "classification": "Definitive"},
        {
            "disease_title": "disease Y subtype",
            "disease_curie": "MONDO:2",
            "classification": "Moderate",
        },
    ]
    assert strongest_in_scope(assocs, scope_ids={"MONDO_2"}, disease_name="disease Y") == "Moderate"
    assert strongest_in_scope(assocs, scope_ids={"MONDO_9"}, disease_name="disease Z") is None


def _ev(gene: str, disease: str, disease_id: str, etype: EvidenceType, extra: dict) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene=gene,
        disease=disease,
        disease_id=disease_id,
        evidence_type=etype,
        scope="abstract",
        source=f"test:{etype.value}",
        source_link="https://example.com",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        extra=extra,
    )


def _curation(gene, disease, did, title, curie, cls):
    return _ev(
        gene,
        disease,
        did,
        EvidenceType.GENETICS,
        {
            "associations": [
                {"disease_title": title, "disease_curie": curie, "classification": cls}
            ],
            "total": 1,
        },
    )


def _clinvar(gene, disease, did, n_gold: int, n_conflicting: int = 0):
    p = [{"clinical_significance": "Pathogenic", "gold_stars": 2}] * n_gold
    p += [
        {"clinical_significance": "Conflicting classifications of pathogenicity", "gold_stars": 1}
    ] * n_conflicting
    return _ev(
        gene, disease, did, EvidenceType.CONSTRAINT, {"pathogenic": p, "likely_pathogenic": []}
    )


# (gene, run disease, run id, scope, curated title, curated curie, classification, expected grade)
_CASES = [
    # gene's Mendelian disease is a different disorder → not Mendelian-grade for this run
    (
        "PCSK9",
        "myocardial infarction",
        "MONDO_0005068",
        {"MONDO_0005068"},
        "hypercholesterolemia, autosomal dominant, 3",
        "MONDO:0011369",
        "Definitive",
        False,
    ),
    (
        "LRRK2",
        "Crohn disease",
        "MONDO_0005011",
        {"MONDO_0005011"},
        "Parkinson disease 8, autosomal dominant",
        "MONDO:0011764",
        "Definitive",
        False,
    ),
    # curated disease is the indication (by id)
    (
        "LRRK2",
        "Parkinson disease",
        "MONDO_0005180",
        {"MONDO_0005180", "MONDO_0011764"},
        "Parkinson disease 8, autosomal dominant",
        "MONDO:0011764",
        "Definitive",
        True,
    ),
    # curated subtype matched by name when the id is outside the fetched scope
    (
        "TRPC6",
        "focal segmental glomerulosclerosis",
        "MONDO_0100313",
        {"MONDO_0100313"},
        "focal segmental glomerulosclerosis 2",
        "MONDO:0011398",
        "Strong",
        True,
    ),
    # limited curation of the indication is in scope but not Mendelian-grade
    (
        "GENE1",
        "disease Y",
        "MONDO_0000002",
        {"MONDO_0000002"},
        "disease Y",
        "MONDO:0000002",
        "Limited",
        False,
    ),
]


@pytest.mark.parametrize("gene,disease,did,scope,title,curie,cls,grade", _CASES)
def test_mendelian_grade_is_scoped_to_the_indication(
    gene, disease, did, scope, title, curie, cls, grade
):
    rows = [
        _curation(gene, disease, did, title, curie, cls),
        _clinvar(gene, disease, did, n_gold=5),
    ]
    signals = _genetics_floor_signals(rows, frozenset(scope))
    assert mendelian_grade_from_signals(signals) is grade
    if not grade and cls in ("Definitive", "Strong"):
        assert title in signals["out_of_scope_curations"]


def test_gene_wide_clinvar_alone_is_not_indication_validation():
    rows = [_clinvar("PCSK9", "myocardial infarction", "MONDO_0005068", n_gold=10)]
    signals = _genetics_floor_signals(rows, frozenset({"MONDO_0005068"}))
    assert signals["plp_in_scope"] is False
    assert mendelian_grade_from_signals(signals) is False


def test_conflicting_classifications_are_not_pathogenic():
    rows = [_clinvar("G", "d", "MONDO_1", n_gold=0, n_conflicting=40)]
    signals = _genetics_floor_signals(rows, frozenset({"MONDO_1"}))
    assert signals["plp_count"] == 0
    assert signals["high_star_plp"] == 0


def test_unscoped_call_keeps_legacy_gene_level_behaviour():
    row = _ev(
        "G",
        "",
        "",
        EvidenceType.GENETICS,
        {"associations": [{"disease_title": "x", "classification": "Definitive"}], "total": 1},
    )
    signals = _genetics_floor_signals([row])
    assert signals["clingen_classification"] == "Definitive"


# ClinVar's own calls name the condition each is for (mcp_servers/clinvar); gnomAD's mirror
# rows do not. A call counts for the disease it names.


def _call(consequence: str, *conditions: tuple[str, str], stars: int = 2) -> dict:
    return {
        "clinical_significance": "Pathogenic",
        "gold_stars": stars,
        "major_consequence": consequence,
        "conditions": [{"name": n, "mondo_id": m} for n, m in conditions],
    }


def _calls(gene, disease, did, *calls):
    return _ev(
        gene,
        disease,
        did,
        EvidenceType.CONSTRAINT,
        {"pathogenic": list(calls), "likely_pathogenic": []},
    )


_SUBTYPE = ("disorder alpha, autosomal dominant, 3", "MONDO:0000103")  # run disease subtype
_OTHER = ("disorder omega due to GENE1 deficiency", "MONDO:0000900")  # another disorder


@pytest.mark.parametrize(
    "conditions,expected",
    [
        ([{"name": _SUBTYPE[0], "mondo_id": _SUBTYPE[1]}], "in_scope"),  # MONDO id in scope
        ([{"name": "familial disorder alpha", "mondo_id": ""}], "in_scope"),  # by name
        ([{"name": _OTHER[0], "mondo_id": _OTHER[1]}], "other"),  # placed outside the scope
        ([], "unnamed"),  # "not provided", or a gnomAD mirror row
        # not another disorder, only less specific: a broader class of the disease, the
        # gene's umbrella term, a name the ontology cannot place
        ([{"name": "hereditary disease", "mondo_id": "MONDO:0000050"}], "unnamed"),
        ([{"name": "GENE1-related disorder", "mondo_id": "MONDO:0700999"}], "unnamed"),
        ([{"name": "Inborn genetic diseases", "mondo_id": ""}], "unnamed"),
        # a call for the run's disease and another disorder is for the run's disease too
        (
            [
                {"name": _OTHER[0], "mondo_id": _OTHER[1]},
                {"name": _SUBTYPE[0], "mondo_id": _SUBTYPE[1]},
            ],
            "in_scope",
        ),  # fmt: skip
    ],
)
def test_a_call_is_for_the_condition_it_names(conditions, expected):
    where = call_scope(
        conditions,
        scope_ids={"MONDO_0000100", "MONDO_0000103"},
        disease_name="disorder alpha",
        ancestor_ids={"MONDO_0000050"},
        gene="GENE1",
    )
    assert where == expected


def test_calls_naming_the_run_disease_count_without_a_curation_and_others_never_count():
    scope = frozenset({"MONDO_0000100", "MONDO_0000103"})
    rows = [
        _calls(
            "GENE1", "disorder alpha", "MONDO_0000100",
            *[_call("missense_variant", _SUBTYPE)] * 3,
            *[_call("stop_gained", _OTHER)] * 4,
        )
    ]  # fmt: skip
    signals = _genetics_floor_signals(rows, scope)

    assert signals["plp_in_scope"] is True
    assert (signals["plp_count"], signals["high_star_plp"]) == (3, 3)
    assert signals["plp_out_of_scope"] == 4
    assert signals["plp_out_of_scope_conditions"] == [_OTHER[0]]


def test_the_mechanism_is_voted_by_the_run_disease_calls_not_the_genes_other_disorders():
    scope = frozenset({"MONDO_0000100", "MONDO_0000103"})
    constraint = _ev("GENE1", "disorder alpha", "MONDO_0000100",
                     EvidenceType.CONSTRAINT, {"loeuf": 1.1, "pli": 0.0, "mis_z": 1.0})  # fmt: skip
    calls = [*[_call("missense_variant", _SUBTYPE)] * 3, *[_call("stop_gained", _OTHER)] * 4]
    scoped = _genetics_floor_signals(
        [constraint, _calls("GENE1", "disorder alpha", "MONDO_0000100", *calls)],
        scope,
    )
    # pooled (a gene-only run), 3 missense against 4 truncating reads no gain of function
    unscoped = _ev("GENE1", "", "", EvidenceType.CONSTRAINT, constraint.extra)
    pooled = _genetics_floor_signals([unscoped, _calls("GENE1", "", "", *calls)])

    assert scoped["mechanism_direction"]["mechanism"] == "gof"
    assert scoped["mechanism_direction"]["from_other_disorders"] is False
    assert pooled["mechanism_direction"]["mechanism"] != "gof"


def test_only_other_disorders_calls_give_a_related_mechanism_never_an_in_scope_one():
    scope = frozenset({"MONDO_0000100"})
    constraint = _ev("GENE1", "disorder alpha", "MONDO_0000100",
                     EvidenceType.CONSTRAINT, {"loeuf": 1.1, "pli": 0.0, "mis_z": 1.0})  # fmt: skip
    other = _calls("GENE1", "disorder alpha", "MONDO_0000100",
                   *[_call("missense_variant", ("disorder X", "MONDO:1"))] * 4)  # fmt: skip
    signals = _genetics_floor_signals([constraint, other], scope)

    assert signals["plp_count"] == 0 and signals["plp_in_scope"] is False
    assert signals["mechanism_direction"]["from_other_disorders"] is True
