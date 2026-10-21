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
    KnownChange,
    build_registry,
    claim_changes,
    fill_unknown_changes,
    variant_keys,
)


def _claim(i: int, text: str, perturbation: str = "none", source: str = "none", basis: str = ""):
    return SimpleNamespace(
        evidence_id=f"c{i}",
        claim_text=text,
        perturbation=perturbation,
        effect_source=source,
        effect_basis=basis,
        off_target=False,
    )


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


def test_changes_come_from_the_claims_typed_effect_or_a_truncating_change():
    claims = [
        _claim(0, "The B2 allele lowered ABC1 concentration.", "decrease", "claim", "lowered ABC1"),
        _claim(1, "ABC1 activating variants raised risk.", "increase", "claim", "activating"),
        _claim(2, "Carriers of ABC1 p.Tyr74Ter had lower risk."),  # by its consequence
        _claim(3, "In R46L carriers DRUG-X lowered events.", "decrease", "drug", "DRUG-X"),
        _claim(4, "The A allele of ABC1 rs9 was associated with risk."),  # nothing typed
    ]

    changes = claim_changes(claims)

    assert changes == {
        "c0": KnownChange(change="loss", source="claim", basis="lowered ABC1"),
        "c1": KnownChange(change="gain", source="claim", basis="activating"),
        "c2": KnownChange(change="loss", source="consequence", basis="p.Tyr74Ter"),
    }


def test_a_typed_change_fills_an_unknown_change_of_the_same_variant():
    seed = _claim(0, "The ABC1 R46L variant lowered ABC1 levels.", "decrease", "claim", "lowered")
    other = _claim(1, "The L46 allele of ABC1 protects against disease a.")
    changes = claim_changes([seed, other])

    registry = build_registry([seed, other], changes)
    filled, n = fill_unknown_changes([seed, other], changes, registry)

    assert n == 1
    assert filled["c1"] == KnownChange(change="loss", source="registry: c0", basis="L46 allele")


def test_the_other_allele_of_a_pair_is_never_inferred():
    seed = _claim(0, "The XyzIB B2 allele lowers ABC1 activity.", "decrease", "claim")
    other = _claim(1, "The XyzIB B1 allele raised disease a risk.")
    changes = claim_changes([seed, other])

    filled, n = fill_unknown_changes([seed, other], changes, build_registry([seed, other], changes))

    assert n == 0 and "c1" not in filled


def test_a_drugs_action_seeds_nothing():
    drug = _claim(
        0, "In R46L carriers the ABC1 inhibitor DRUG-X lowered events.", "decrease", "drug"
    )

    assert build_registry([drug], claim_changes([drug])).entries == {}


def test_disagreeing_seeds_drop_the_variant_and_multi_variant_sentences_seed_nothing():
    claims = [
        _claim(0, "ABC1 R46L lowers ABC1 function.", "decrease", "claim"),
        _claim(1, "ABC1 R46L raises ABC1 function.", "increase", "claim"),
        _claim(2, "ABC1 E67G and R12C lower ABC1 function.", "decrease", "claim"),
    ]

    registry = build_registry(claims, claim_changes(claims))

    assert registry.entries == {}
    assert registry.conflicting == ["p|46|L"]


def test_a_truncating_change_fills_without_a_registry():
    stop = _claim(0, "Carriers of ABC1 p.Tyr74Ter had lower disease a risk.")

    filled, n = fill_unknown_changes([stop], {}, build_registry([], {}))

    assert n == 1
    assert filled["c0"].change == "loss" and filled["c0"].source == "consequence"


def test_the_registry_keeps_no_vocabulary_lists():
    """Seeding reads code-typed changes only: no genetic- or drug-word lists to extend."""
    from services.evidence import variant_registry

    assert not hasattr(variant_registry, "_GENETIC_WORDS")
    assert not hasattr(variant_registry, "_DRUG_WORDS")
