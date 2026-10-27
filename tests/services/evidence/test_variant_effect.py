# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A variant's effect on the target by lookup (services/evidence/variant_effect.py).

The failures these prevent: a registry seed typed an HDL-C level as the target's own,
so every CETP variant's seeds disagreed; and a missense pQTL (an antibody that binds the
variant protein less) read as less protein.
"""

from __future__ import annotations

from types import SimpleNamespace

from mcp_servers.opentargets.tools import VariantQtl, VariantTargetEffect
from schemas.statement import TargetStatement
from services.evidence.direction_of_effect import from_qtl_colocalisation
from services.evidence.grounding import Mention
from services.evidence.variant_effect import (
    allele_role,
    effect_of,
    group_by_variant,
    qtl_change,
)


def _variant(**kw) -> VariantTargetEffect:
    base = {"variant_id": "1_100_G_T", "rsids": ["rs1"], "ref": "G", "alt": "T"}
    return VariantTargetEffect(**{**base, **kw})


def _mention(allele: str, hgvs: str = "", rsid: str = "rs1") -> Mention:
    return Mention(
        number=1, kind="variant", text="v", is_target=True, rsid=rsid, hgvs=hgvs, allele=allele
    )


_LOWERING = [VariantQtl(study_type="eqtl", beta=-0.3)]


def test_the_alternate_allele_takes_the_qtl_sign_and_the_reference_the_opposite():
    data = _variant(qtls=_LOWERING)

    assert effect_of(_mention("T"), [data]) == ("decrease", "qtl")
    assert effect_of(_mention("G"), [data]) == ("increase", "qtl")


def test_a_base_on_the_other_strand_is_matched_by_its_complement():
    data = _variant(qtls=_LOWERING)  # G/T: a paper on the gene's minus strand writes C/A

    assert allele_role("A", "", data) == "alt"
    assert allele_role("C", "", data) == "ref"


def test_a_palindromic_variant_is_skipped():
    data = _variant(ref="A", alt="T", qtls=_LOWERING)

    assert allele_role("T", "", data) is None
    assert effect_of(_mention("T"), [data]) is None


def test_a_protein_change_is_oriented_by_the_targets_amino_acid_change():
    data = _variant(amino_acid_change="R46L", curated=["LoF"])

    assert effect_of(_mention("L", "p.R46L"), [data]) == ("decrease", "curated")
    assert effect_of(_mention("R", "p.L46R"), [data]) == ("increase", "curated")
    assert effect_of(_mention("Q", "p.R46Q"), [data]) is None


def test_curated_directions_must_agree_and_come_before_the_qtl():
    split = _variant(curated=["LoF", "GoF"], qtls=[VariantQtl(study_type="pqtl", beta=0.4)])

    assert effect_of(_mention("T"), [split]) == ("increase", "qtl")


def test_a_truncating_change_needs_no_lookup():
    assert effect_of(_mention("*", "p.Y74*"), []) == ("decrease", "consequence")


def test_a_pqtl_led_by_a_missense_change_of_the_target_is_not_read():
    artefact = VariantQtl(study_type="pqtl", beta=-0.22, lead_missense_in_target=True)
    expression = VariantQtl(study_type="eqtl", beta=0.1)

    assert qtl_change(_variant(qtls=[artefact])) is None
    assert qtl_change(_variant(qtls=[artefact, expression])) == "increase"


def test_tissue_signs_are_netted_and_a_tie_says_nothing():
    tie = [VariantQtl(study_type="eqtl", beta=0.2), VariantQtl(study_type="eqtl", beta=-0.1)]

    assert qtl_change(_variant(qtls=tie)) is None


def test_the_colocalisation_reading_skips_a_missense_led_pqtl():
    rows = [
        SimpleNamespace(study_type="pqtl", beta_ratio_sign=1.0, lead_missense_in_target=True),
        SimpleNamespace(study_type="eqtl", beta_ratio_sign=-1.0, lead_missense_in_target=False),
    ]

    (obs,) = from_qtl_colocalisation(rows, "GENE-A")

    assert "eqtl" in obs.source and obs.outcome == "benefit"


def test_three_spellings_of_one_variant_are_one_key():
    """tmVar gives "R46L", "p.Arg46Leu" and "rs11591147" the same rs id."""

    def st(text: str, source: str) -> TargetStatement:
        return TargetStatement(
            source=source,
            sentence=0,
            text=text,
            exposure_kind="variant",
            exposure_id="rs11591147",
            exposure_text=text,
        )

    groups = group_by_variant(
        [st("R46L", "PMID:1"), st("p.Arg46Leu", "PMID:2"), st("rs11591147", "PMID:3")]
    )

    assert list(groups) == ["rs11591147"]
    assert [s.exposure_text for s in groups["rs11591147"]] == ["R46L", "p.Arg46Leu", "rs11591147"]


def test_no_qtl_is_read_for_a_variant_that_changes_the_targets_protein():
    """A QTL measures amount, not activity: a protective missense variant that impairs the
    protein can raise its expression."""
    missense = _variant(
        amino_acid_change="P1104A",
        consequences=["missense_variant"],
        qtls=[VariantQtl(study_type="eqtl", beta=0.43)],
    )

    assert qtl_change(missense) is None
    assert effect_of(_mention("A", "p.P1104A"), [missense]) is None


def test_a_protein_change_at_another_position_is_not_oriented():
    """Transcripts number residues differently; residues alone could match by chance."""
    data = _variant(amino_acid_change="W1161R", curated=["GoF"])

    assert allele_role("W", "p.R1150W", data) is None


def test_a_splice_variants_qtl_is_not_read():
    """HSD17B13 rs72613567:TA — a splice-region insertion whose protein is inactive raises
    the gene's mRNA in every eQTL; read as "more target", it inverted the direction."""
    splice = _variant(
        variant_id="4_87310240_T_TA",
        ref="T",
        alt="TA",
        consequences=["intron_variant", "splice_region_variant"],
        qtls=[VariantQtl(study_type="eqtl", beta=0.5)],
    )
    assert qtl_change(splice) is None
    assert effect_of(_mention("TA"), [splice]) is None


def test_an_indel_is_oriented_by_its_own_alleles():
    insertion = _variant(ref="T", alt="TA", qtls=_LOWERING)
    assert allele_role("TA", "", insertion) == "alt"
    assert allele_role("T", "", insertion) == "ref"
    assert allele_role("A", "", insertion) is None


def test_a_consequence_that_removes_the_protein_lowers_the_target():
    stop = _variant(consequences=["stop_gained"])
    assert effect_of(_mention("T"), [stop]) == ("decrease", "consequence")
    assert effect_of(_mention("G"), [stop]) == ("increase", "consequence")
