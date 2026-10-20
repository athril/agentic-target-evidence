# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-run variant registry (services/evidence/variant_registry.py).

The failure it prevents: association sentences naming an allele without its effect
("the L46 allele protects", "the B2 allele lowered risk") were typed "unknown" one by
one, although another sentence of the same run stated that variant's effect on the
gene, so most of a target's genetic association literature never reached the
direction-of-effect reading.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.evidence.variant_registry import (
    build_registry,
    fill_unknown_changes,
    variant_keys,
)

_GENE = ["ABC1"]


def _claim(i: int, text: str, **effect):
    return SimpleNamespace(
        evidence_id=f"c{i}",
        claim_text=text,
        perturbation=effect.get("perturbation", "none"),
        effect_basis=effect.get("effect_basis", ""),
        off_target=False,
    )


def _answer(change: str = "unknown", risk: str = "lower", gene: str = "ABC1") -> dict:
    return {"variant_gene": gene, "gene_change": change, "risk": risk, "outcome": "disease a"}


@pytest.mark.parametrize(
    "text",
    [
        "ABC1 R46L carriers",
        "carriers of p.Arg46Leu in ABC1",
        "the L46 allele of ABC1",
        "the ABC1 46L polymorphism",
    ],
)
def test_one_protein_change_written_four_ways_is_one_key(text):
    assert set(variant_keys(text)) == {("p", "46", "L")}


def test_the_reference_residue_is_another_key():
    assert set(variant_keys("the R46 allele of ABC1")) == {("p", "46", "R")}


def test_an_rs_id_counts_only_with_its_allele():
    assert variant_keys("ABC1 rs12345 was associated with risk") == {}
    assert set(variant_keys("the T allele of rs12345")) == {("rs", "rs12345", "T")}
    assert set(variant_keys("rs12345-T carriers")) == {("rs", "rs12345", "T")}


def test_restriction_site_names_are_normalised():
    assert set(variant_keys("the Xyz1B B2 allele")) == set(variant_keys("XyzIB B2B2 carriers"))


def test_a_stated_effect_fills_an_unknown_change_of_the_same_variant():
    seed = _claim(0, "The ABC1 R46L loss-of-function variant lowered disease a risk.")
    other = _claim(1, "The L46 allele of ABC1 protects against disease a.")
    answers = {"c0": _answer("loss"), "c1": _answer("unknown")}

    registry = build_registry([seed, other], answers, target_symbols=_GENE)
    filled, n = fill_unknown_changes([seed, other], answers, registry, target_symbols=_GENE)

    assert n == 1
    assert filled["c1"]["gene_change"] == "loss"
    assert filled["c1"]["change_basis"] == "L46 allele"
    assert filled["c1"]["change_source"] == "variant registry: claim c0"


def test_the_other_allele_of_a_pair_is_never_inferred():
    seed = _claim(0, "The XyzIB B2 allele lowers ABC1 activity.")
    other = _claim(1, "The XyzIB B1 allele raised disease a risk.")
    answers = {"c0": _answer("loss"), "c1": _answer("unknown", "higher")}

    registry = build_registry([seed, other], answers, target_symbols=_GENE)
    filled, n = fill_unknown_changes([seed, other], answers, registry, target_symbols=_GENE)

    assert n == 0
    assert filled["c1"]["gene_change"] == "unknown"


def test_a_checked_target_level_seeds_but_a_drug_does_not():
    level = _claim(
        0,
        "The XyzIB B2 allele is associated with lower concentrations of ABC1.",
        perturbation="decrease",
        effect_basis="lower concentrations of ABC1",
    )
    drug = _claim(
        1,
        "In R46L carriers the ABC1 inhibitor drug-x lowered events.",
        perturbation="decrease",
        effect_basis="the ABC1 inhibitor drug-x",
    )

    registry = build_registry([level, drug], {}, target_symbols=_GENE)

    assert set(registry.entries) == {"site|xyzib|B2"}


def test_disagreeing_seeds_drop_the_variant_and_multi_variant_sentences_seed_nothing():
    claims = [
        _claim(0, "ABC1 R46L is a loss-of-function variant."),
        _claim(1, "ABC1 R46L is a gain-of-function variant."),
        _claim(2, "ABC1 E67G and R12C are loss-of-function variants."),
    ]
    answers = {"c0": _answer("loss"), "c1": _answer("gain"), "c2": _answer("loss")}

    registry = build_registry(claims, answers, target_symbols=_GENE)

    assert registry.entries == {}
    assert registry.conflicting == ["p|46|L"]


def test_a_truncating_change_is_a_loss_and_another_genes_variant_is_left_alone():
    stop = _claim(0, "Carriers of ABC1 p.Tyr74Ter had lower disease a risk.")
    other_gene = _claim(1, "Carriers of XYZ2 p.Tyr74Ter had lower disease a risk.")
    answers = {"c0": _answer("unknown"), "c1": _answer("unknown", gene="XYZ2")}

    filled, n = fill_unknown_changes(
        [stop, other_gene],
        answers,
        build_registry([], {}, target_symbols=_GENE),
        target_symbols=_GENE,
    )

    assert n == 1
    assert filled["c0"]["gene_change"] == "loss"
    assert filled["c1"]["gene_change"] == "unknown"
