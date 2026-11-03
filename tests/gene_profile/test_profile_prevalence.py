# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""How common the disease alleles are (M12), on the records that exposed the pitfalls.

Variants are the real ClinVar/gnomAD rows: CFTR's sum is dominated by two intronic poly-T/TG
tract alleles at two review stars, whose effect depends on the repeat in cis.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import profile_prevalence as prev  # noqa: E402
import profile_tables as tables  # noqa: E402

# CFTR, from gnomAD's ClinVar join (2026-11-02).
_CFTR: list[dict[str, Any]] = [
    {"hgvsc": "c.1210-7_1210-6del", "af": 0.029269, "gold_stars": 2,
     "clinical_significance": "Pathogenic/Likely pathogenic", "major_consequence": "intron_variant"},
    {"hgvsp": "p.Phe508del", "af": 0.011931, "gold_stars": 4,
     "clinical_significance": "Pathogenic", "major_consequence": "inframe_deletion"},
    {"hgvsc": "c.1210-11T>G", "af": 0.009789, "gold_stars": 2,
     "clinical_significance": "Pathogenic/Likely pathogenic; other", "major_consequence": "intron_variant"},
    {"hgvsp": "p.Arg117His", "af": 0.002138, "gold_stars": 4,
     "clinical_significance": "Pathogenic", "major_consequence": "missense_variant"},
    {"hgvsp": "p.Gly542Ter", "af": 0.000363, "gold_stars": 4,
     "clinical_significance": "Pathogenic", "major_consequence": "stop_gained"},
    # Held by ClinVar but absent from gnomAD: no frequency, which is not a frequency of zero.
    {"hgvsp": "p.Gly1349Asp", "af": None, "gold_stars": 3, "clinical_significance": "Pathogenic"},
    # A conditional allele: excluded from the sum by ClinVar's own wording.
    {"hgvsc": "c.1210-12T[5]", "af": 0.05, "gold_stars": 2,
     "clinical_significance": "Pathogenic, low penetrance", "major_consequence": "intron_variant"},
]  # fmt: skip


def test_a_conditional_allele_is_left_out_of_the_sum() -> None:
    assert prev.is_conditional("Pathogenic, low penetrance")
    assert prev.is_conditional("drug response")
    assert not prev.is_conditional("Pathogenic/Likely pathogenic")
    # The 5% low-penetrance allele would otherwise double the estimate.
    assert prev.allele_sum(_CFTR, 1) < 0.06


def test_the_sensitivity_shows_what_rests_on_weakly_reviewed_alleles() -> None:
    """At ≥1★ CFTR reads about 1 in 9 carriers, against the literature's ~1 in 25; at ≥3★ the
    two intronic 2★ alleles drop out. The table reports both rather than picking one."""
    out = prev.prevalence(_CFTR, ["Autosomal recessive"])
    by_stars = dict(out["sensitivity"])
    assert by_stars[1] > by_stars[3]
    assert 0.012 < by_stars[3] < 0.016  # F508del, R117H, G542X
    assert out["top"][0]["share"] > 0.4
    assert out["top"][0]["stars"] == 2


def test_the_statistic_follows_the_recorded_inheritance() -> None:
    labels = [r[0] for r in prev.statistic(1e-2, ["Autosomal recessive"])]
    assert any("Carriers" in label for label in labels)
    assert any("biallelic" in label for label in labels)
    # X-linked recessive contains "recessive": an X-linked gene must not get autosomal rows
    # or an expected-biallelic rate.
    x = [r[0] for r in prev.statistic(1e-4, ["X-linked dominant", "X-linked recessive"])]
    assert x == ["Hemizygous males (X-linked)", "Heterozygous females (X-linked)"]
    # No recorded mode: the sum alone, with the reason no statistic was chosen.
    assert prev.statistic(1e-4, [])[0][0] == "Statistic not chosen"
    assert prev.statistic(0.0, ["Autosomal recessive"]) == []


def test_variants_absent_from_gnomad_are_counted_as_uncovered() -> None:
    out = prev.prevalence(_CFTR, ["Autosomal recessive"])
    assert out["variants_total"] == 7
    assert out["variants_with_frequency"] == 5  # one has no af, one is conditional
    assert out["conditional_excluded"] == 1


def test_the_table_states_the_bound_and_its_coverage() -> None:
    md = tables.genetic_prevalence_md(
        prev.prevalence(_CFTR, ["Autosomal recessive"]), {"gnomad_clinvar", "hpo"}
    )
    assert "upper bound" in md
    assert "Largest contributor: c.1210-7_1210-6del" in md
    assert "By review status" in md
    assert "de novo" in md
    assert "Penetrance is not modelled" in md
    assert "repeat alleles are outside this source" in md


def test_no_frequency_anywhere_renders_nothing() -> None:
    nothing = prev.prevalence(
        [{"hgvsp": "p.X", "af": None, "gold_stars": 3, "clinical_significance": "Pathogenic"}],
        ["Autosomal recessive"],
    )
    assert tables.genetic_prevalence_md(nothing, {"gnomad_clinvar"}) == ""


def test_a_gene_recording_two_modes_gets_both_and_the_ambiguity_named() -> None:
    """HPO lists every mode any of the gene's diseases uses, so BRCA1, EGFR and SCN5A all
    record dominant and recessive. Picking one by priority gave SCN5A a recessive carrier rate
    and an expected biallelic rate of 1 in 6 million, for a dominant disease."""
    labels = [r[0] for r in prev.statistic(4.08e-4, ["Autosomal dominant", "Autosomal recessive"])]
    assert "Carriers of one allele (autosomal dominant)" in labels
    assert "Carriers of one allele (autosomal recessive)" in labels
    assert labels[-1] == "More than one mode recorded"
    note = prev.statistic(4.08e-4, ["Autosomal dominant", "Autosomal recessive"])[-1][1]
    assert "which disease is meant" in note


def test_one_recorded_mode_gets_no_ambiguity_note() -> None:
    labels = [r[0] for r in prev.statistic(1e-2, ["Autosomal recessive"])]
    assert "More than one mode recorded" not in labels
