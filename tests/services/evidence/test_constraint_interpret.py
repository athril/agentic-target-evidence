# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for constraint_interpret.py — golden band table + direction classifier.

Regression locks for E1–E3 from the TRPC6×FSGS report:
  E1: LOEUF 0.759 must NOT produce "haploinsufficient"
  E2: mis_z 1.70 must NOT produce "critical" or "intolerant" (direction: higher = more constrained)
  E3: raw pLoF count must not be treated as a selection metric
"""

from __future__ import annotations

from schemas.evidence import Direction
from services.evidence.constraint_interpret import (
    ConstraintReading,
    apply_constraint_guards,
    apply_mendelian_floor_guard,
    compute_mendelian_grade,
    infer_mechanism_direction,
    interpret_constraint,
    interpret_expression_context,
    interpret_expression_context_for_mechanism,
    interpret_gof_tolerance_support,
    loeuf_band,
    misz_band,
    moeuf_band,
    pli_band,
)

# ---------------------------------------------------------------------------
# Band function golden table
# ---------------------------------------------------------------------------


class TestLoeufBand:
    def test_strong_constraint(self):
        assert "haploinsufficient" in loeuf_band(0.20)

    def test_moderate_constraint(self):
        b = loeuf_band(0.50)
        assert "moderate" in b
        assert "haploinsufficient" not in b

    def test_trpc6_tolerant(self):
        """TRPC6 LOEUF=0.759 must land in LoF-tolerant, NOT positively claim haploinsufficiency (E1 lock)."""
        b = loeuf_band(0.759)
        assert "tolerant" in b.lower()
        # The band may say "(NOT haploinsufficient)" to remind the LLM — but must NOT
        # positively assert haploinsufficiency (the positive claim is "candidate haploinsufficient").
        assert "candidate haploinsufficient" not in b.lower()

    def test_tolerant_high(self):
        b = loeuf_band(0.95)
        assert "tolerant" in b.lower()
        assert "candidate haploinsufficient" not in b.lower()

    def test_boundary_035_is_moderate(self):
        # Exactly 0.35 is NOT haploinsufficient
        b = loeuf_band(0.35)
        assert "haploinsufficient" not in b.lower()

    def test_boundary_075_is_tolerant(self):
        b = loeuf_band(0.75)
        assert "tolerant" in b.lower()


class TestMiszBand:
    def test_significant(self):
        assert "significant" in misz_band(3.5)

    def test_mild(self):
        b = misz_band(2.5)
        assert "mild" in b

    def test_trpc6_no_constraint(self):
        """TRPC6 mis_z=1.70 must be 'no meaningful constraint', NOT critical (E2 lock)."""
        b = misz_band(1.70)
        assert "no meaningful" in b.lower()
        assert "critical" not in b.lower()
        assert "significant" not in b.lower()

    def test_boundary_2_is_mild(self):
        b = misz_band(2.0)
        assert "mild" in b

    def test_boundary_309_is_significant(self):
        b = misz_band(3.09)
        assert "significant" in b


class TestMoeufBand:
    def test_intolerant(self):
        assert "intolerant" in moeuf_band(0.5)

    def test_intermediate(self):
        b = moeuf_band(0.7)
        assert "intermediate" in b

    def test_tolerant(self):
        assert "tolerant" in moeuf_band(1.1)


class TestPLIBand:
    def test_intolerant(self):
        assert "intolerant" in pli_band(0.95)

    def test_tolerant(self):
        assert "tolerant" in pli_band(0.05)

    def test_indeterminate(self):
        assert "indeterminate" in pli_band(0.5)


# ---------------------------------------------------------------------------
# interpret_constraint() — golden results
# ---------------------------------------------------------------------------


class TestInterpretConstraint:
    def test_trpc6_loeuf_is_tolerant(self):
        """E1 regression: LOEUF=0.759 must not claim haploinsufficiency."""
        r = interpret_constraint("TRPC6", loeuf=0.759, pli=0.00, mis_z=1.70)
        assert not r.claims_haploinsufficiency_ok
        assert r.is_lof_tolerant
        assert not r.is_lof_constrained
        # Boolean flag must be False — "candidate haploinsufficient" must not be asserted.
        assert "candidate haploinsufficient" not in r.summary_text.lower()
        assert "tolerant" in r.summary_text.lower()

    def test_trpc6_misz_no_constraint(self):
        """E2 regression: mis_z=1.70 must produce 'no meaningful missense constraint'."""
        r = interpret_constraint("TRPC6", mis_z=1.70)
        assert not r.is_missense_constrained
        assert "no meaningful" in r.summary_text.lower()
        assert "critical" not in r.summary_text.lower()

    def test_haploinsufficent_gene(self):
        r = interpret_constraint("HAPLOINSUFFICIENT_GENE", loeuf=0.20, pli=0.99)
        assert r.claims_haploinsufficiency_ok
        assert r.is_lof_constrained
        assert not r.is_lof_tolerant
        assert "haploinsufficient" in r.summary_text.lower()

    def test_all_none_returns_empty_summary(self):
        r = interpret_constraint("UNKNOWN")
        assert r.summary_text  # still returns a sentence
        assert not r.is_lof_constrained
        assert not r.is_lof_tolerant

    def test_homozygous_lof_present(self):
        r = interpret_constraint("GENE", loeuf=0.80, any_homozygous=True)
        assert r.hom_lof_present is True
        assert "tolerated" in r.hom_lof_note.lower()

    def test_homozygous_lof_absent_not_lethal(self):
        """E3 regression: absence of homozygous carriers must NOT positively claim lethality."""
        r = interpret_constraint("GENE", loeuf=0.80, hc_lof_count=194, any_homozygous=False)
        assert r.hom_lof_present is False
        assert "uninformative" in r.hom_lof_note.lower()
        # The note may say "NOT evidence of biallelic lethality" — the word "lethal" appears in
        # a negation, which is correct.  What must not appear is a bare positive claim.
        assert "biallelic loss likely lethal" not in r.hom_lof_note.lower()
        # The note must explicitly state the count-based interpretation is invalid.
        assert "raw plof count" in r.hom_lof_note.lower() or "loeuf" in r.hom_lof_note.lower()
        # The summary must contain the uninformative note
        assert "LOEUF" in r.summary_text

    def test_missense_constrained_flags(self):
        r = interpret_constraint("GENE", mis_z=3.5, moeuf=0.5)
        assert r.is_missense_constrained

    def test_moeuf_band_in_summary(self):
        r = interpret_constraint("GENE", moeuf=0.5)
        assert "missense" in r.summary_text.lower()


# ---------------------------------------------------------------------------
# infer_mechanism_direction() — decision table
# ---------------------------------------------------------------------------


def _make_plp(
    n_missense: int,
    n_truncating: int,
    *,
    gold_stars: int = 2,
    n_synonymous: int = 0,
    n_zero_star_truncating: int = 0,
) -> list[dict]:
    variants = []
    for i in range(n_missense):
        variants.append(
            {
                "major_consequence": "missense_variant",
                "variant_id": f"missense_{i}",
                "gold_stars": gold_stars,
            }
        )
    for i in range(n_truncating):
        variants.append(
            {
                "major_consequence": "stop_gained",
                "variant_id": f"trunc_{i}",
                "gold_stars": gold_stars,
            }
        )
    for i in range(n_zero_star_truncating):
        variants.append(
            {
                "major_consequence": "frameshift_variant",
                "variant_id": f"trunc_zerostar_{i}",
                "gold_stars": 0,
            }
        )
    for i in range(n_synonymous):
        variants.append(
            {
                "major_consequence": "synonymous_variant",
                "variant_id": f"syn_{i}",
                "gold_stars": gold_stars,
            }
        )
    return variants


class TestInferMechanismDirection:
    def test_trpc6_gof_inhibit(self):
        """TRPC6-like: missense-predominant + LoF-tolerant → INHIBIT (GoF)."""
        reading = interpret_constraint("TRPC6", loeuf=0.759, pli=0.00)
        plp = _make_plp(n_missense=10, n_truncating=0)
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"
        assert md.confidence > 0.0
        assert "inhibition" in md.rationale.lower() or "inhibit" in md.rationale.lower()

    def test_haploinsufficiency_activate(self):
        """LoF-constrained gene + truncating-predominant → ACTIVATE / restore."""
        reading = interpret_constraint("HI_GENE", loeuf=0.20, pli=0.99)
        plp = _make_plp(n_missense=1, n_truncating=8)
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.ACTIVATE
        assert md.mechanism == "lof"
        assert md.confidence > 0.0

    def test_mixed_spectrum_unspecified(self):
        """Mixed missense + truncating → UNSPECIFIED."""
        reading = interpret_constraint("MIXED", loeuf=0.50, pli=0.50)
        plp = _make_plp(n_missense=3, n_truncating=3)
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.UNSPECIFIED
        assert md.mechanism == "ambiguous"

    def test_sparse_plp_unspecified(self):
        """<2 P/LP → UNSPECIFIED regardless of constraint."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        md = infer_mechanism_direction(reading, [{"major_consequence": "missense_variant"}])
        assert md.direction == Direction.UNSPECIFIED
        assert md.mechanism == "ambiguous"

    def test_lof_tolerant_but_truncating_predominant_unspecified(self):
        """Truncating-predominant but LoF-tolerant → UNSPECIFIED (contradicts LoF mechanism)."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=0, n_truncating=5)
        md = infer_mechanism_direction(reading, plp)
        # Truncating predominant but NOT LoF-constrained → ambiguous
        assert md.mechanism == "ambiguous"

    def test_gof_supporting_variant_ids_populated(self):
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=5, n_truncating=0)
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.INHIBIT
        assert len(md.supporting_variant_ids) == 5

    def test_trpc6_regression_exact_report_counts(self):
        """TRPC6 report spectrum: 38 missense / 21 truncating / 5 synonymous-pathogenic
        of 64 total. Raw missense_frac=0.59 would have read 'ambiguous' under the old
        rigid gate. With gold-star/synonymous filtering (some truncating calls are
        0-star, synonymous-pathogenic calls never vote), missense clearly dominates
        and must resolve to INHIBIT/gof with confidence >= 0.6 (WS1 acceptance)."""
        reading = interpret_constraint("TRPC6", loeuf=0.759, pli=0.00, mis_z=1.70)
        plp = _make_plp(
            n_missense=38,
            n_truncating=6,
            gold_stars=2,
            n_zero_star_truncating=15,
            n_synonymous=5,
        )
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"
        assert md.confidence >= 0.6

    def test_synonymous_pathogenic_excluded_from_vote(self):
        """A 'Pathogenic' synonymous call must never count toward either bucket."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=2, n_truncating=0, n_synonymous=10)
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"
        assert len(md.supporting_variant_ids) == 2

    def test_zero_star_variants_excluded_from_vote(self):
        """gold_stars in {None, 0} must not vote on mechanism direction."""
        reading = interpret_constraint("GENE", loeuf=0.20, pli=0.99)
        plp = _make_plp(n_missense=0, n_truncating=2, gold_stars=2, n_zero_star_truncating=20)
        md = infer_mechanism_direction(reading, plp)
        # Only the 2 gold-starred truncating variants vote; the 20 zero-star calls don't.
        assert md.direction == Direction.ACTIVATE
        assert md.mechanism == "lof"

    def test_dominance_ratio_replaces_rigid_truncating_gate(self):
        """2:1 missense:truncating dominance fires GoF even below the 0.70 fast path,
        and even with more than one truncating P/LP (the old n_truncating<=1 gate
        would have rejected this case)."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=8, n_truncating=3)  # frac=8/11=0.727 >= 0.70 too
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"

    def test_gof_lean_when_missense_merely_predominates(self):
        """0.5 <= missense_frac < 0.70 in a LoF-tolerant gene must emit a low-confidence
        GoF lean rather than going silent on 'ambiguous'."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=6, n_truncating=5)  # frac=6/11=0.545, ratio 6<10 fails
        md = infer_mechanism_direction(reading, plp)
        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"
        assert 0.0 < md.confidence < 0.55

    def test_gof_lean_not_emitted_when_not_lof_tolerant(self):
        """The lean must require LoF-tolerance — it should not fire in an indeterminate
        or LoF-constrained gene."""
        reading = interpret_constraint("GENE", loeuf=0.50)
        plp = _make_plp(n_missense=6, n_truncating=5)
        md = infer_mechanism_direction(reading, plp)
        assert md.mechanism == "ambiguous"

    # -- WS3: inheritance-mode tie-breaker -----------------------------------

    def test_dominant_inheritance_breaks_borderline_missense_tie_into_gof(self):
        """AD + borderline-missense (lean zone) + LoF-tolerant must resolve to a
        firm-strength GoF call, not the low-confidence lean."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=6, n_truncating=5)  # frac=0.545, lean zone
        md_no_moi = infer_mechanism_direction(reading, plp)
        md = infer_mechanism_direction(reading, plp, inheritance_mode="Autosomal dominant")

        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"
        assert md.confidence >= 0.60
        assert md.confidence > md_no_moi.confidence
        assert "autosomal dominant" in md.rationale.lower()

    def test_recessive_inheritance_corroborates_firm_lof_call(self):
        """AR + truncating-predominant + LoF-constrained: already-firm LoF call,
        inheritance corroborates (confidence nudged up, direction unchanged)."""
        reading = interpret_constraint("HI_GENE", loeuf=0.20, pli=0.99)
        plp = _make_plp(n_missense=1, n_truncating=8)
        md_no_moi = infer_mechanism_direction(reading, plp)
        md = infer_mechanism_direction(reading, plp, inheritance_mode="Autosomal recessive")

        assert md.direction == Direction.ACTIVATE
        assert md.mechanism == "lof"
        assert md.confidence >= md_no_moi.confidence

    def test_conflicting_recessive_inheritance_lowers_confidence_spectrum_wins(self):
        """AR inheritance conflicts with a GoF-leaning missense spectrum — the
        spectrum-driven direction is retained, but confidence must drop below
        the no-inheritance lean baseline."""
        reading = interpret_constraint("GENE", loeuf=0.80)
        plp = _make_plp(n_missense=6, n_truncating=5)  # frac=0.545, lean zone
        md_no_moi = infer_mechanism_direction(reading, plp)
        md = infer_mechanism_direction(reading, plp, inheritance_mode="Autosomal recessive")

        assert md.direction == Direction.INHIBIT  # spectrum wins, not overridden
        assert md.confidence < md_no_moi.confidence
        assert "recessive" in md.rationale.lower()

    def test_recessive_inheritance_lowers_confidence_on_firm_gof_call(self):
        """AR conflicting with an already-firm GoF call: direction retained, confidence reduced."""
        reading = interpret_constraint("TRPC6", loeuf=0.759, pli=0.00)
        plp = _make_plp(n_missense=10, n_truncating=0)
        md_no_moi = infer_mechanism_direction(reading, plp)
        md = infer_mechanism_direction(reading, plp, inheritance_mode="Autosomal recessive")

        assert md.direction == Direction.INHIBIT
        assert md.mechanism == "gof"
        assert md.confidence < md_no_moi.confidence

    def test_unspecified_inheritance_mode_has_no_effect(self):
        reading = interpret_constraint("TRPC6", loeuf=0.759, pli=0.00)
        plp = _make_plp(n_missense=10, n_truncating=0)
        md_none = infer_mechanism_direction(reading, plp)
        md_unspecified = infer_mechanism_direction(reading, plp, inheritance_mode="Unspecified")

        assert md_unspecified.confidence == md_none.confidence
        assert md_unspecified.direction == md_none.direction


# ---------------------------------------------------------------------------
# apply_constraint_guards()
# ---------------------------------------------------------------------------


class TestApplyConstraintGuards:
    def _lof_tolerant_reading(self) -> ConstraintReading:
        return interpret_constraint("TRPC6", loeuf=0.759, pli=0.00, mis_z=1.70)

    def test_haploinsufficiency_flagged_when_loeuf_high(self):
        text = "TRPC6 shows haploinsufficiency based on LOEUF values."
        r = self._lof_tolerant_reading()
        result = apply_constraint_guards(text, r)
        assert "CONSTRAINT GUARD" in result
        assert "haploinsufficiency" in result.lower() or "haploinsufficient" in result.lower()

    def test_missense_critical_flagged_when_not_constrained(self):
        text = "The missense intolerant profile suggests functional criticality."
        r = self._lof_tolerant_reading()
        result = apply_constraint_guards(text, r)
        assert "CONSTRAINT GUARD" in result

    def test_plof_count_selection_flagged(self):
        text = "194 observed pLoF variants indicate selection against biallelic loss."
        r = self._lof_tolerant_reading()
        result = apply_constraint_guards(text, r)
        assert "not a selection metric" in result.lower() or "CONSTRAINT GUARD" in result

    def test_correct_text_unchanged(self):
        text = "TRPC6 is LoF-tolerant with no meaningful missense constraint."
        r = self._lof_tolerant_reading()
        result = apply_constraint_guards(text, r)
        # No guards should fire — text is correct
        assert "CONSTRAINT GUARD" not in result

    def test_haploinsufficiency_ok_when_loeuf_low(self):
        """When LOEUF < 0.35, haploinsufficiency claims must NOT be flagged."""
        text = "This gene shows haploinsufficiency: LOEUF=0.20."
        r = interpret_constraint("HI_GENE", loeuf=0.20)
        result = apply_constraint_guards(text, r)
        assert "CONSTRAINT GUARD" not in result


# ---------------------------------------------------------------------------
# WS4: Mendelian causality floor
# ---------------------------------------------------------------------------


class TestComputeMendelianGrade:
    def test_true_when_high_star_plp_and_plp_count_present(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=1,
                plp_count=2,
                clingen_classification=None,
                graph_association=None,
            )
            is True
        )

    def test_false_when_plp_count_present_but_no_gold_star(self):
        """plp_count alone (no gold-star review) must never qualify — WS1's gold-star discipline."""
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=64,
                clingen_classification=None,
                graph_association=None,
            )
            is False
        )

    def test_true_for_clingen_definitive(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification="Definitive",
                graph_association=None,
            )
            is True
        )

    def test_true_for_clingen_strong(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification="Strong",
                graph_association=None,
            )
            is True
        )

    def test_false_for_clingen_limited(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification="Limited",
                graph_association=None,
            )
            is False
        )

    def test_true_for_strong_corroborating_graph_association(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification=None,
                graph_association={"corroborates_causality": True, "diseases_score": 0.9},
            )
            is True
        )

    def test_false_for_weak_graph_association_score(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification=None,
                graph_association={"corroborates_causality": True, "diseases_score": 0.3},
            )
            is False
        )

    def test_false_for_noncorroborating_graph_association(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification=None,
                graph_association={"corroborates_causality": False, "diseases_score": 0.95},
            )
            is False
        )

    def test_false_when_nothing_present(self):
        assert (
            compute_mendelian_grade(
                high_star_plp=0,
                plp_count=0,
                clingen_classification=None,
                graph_association=None,
            )
            is False
        )


class TestApplyMendelianFloorGuard:
    def test_flags_no_gwas_support_phrasing(self):
        text = "There is no GWAS support for this gene-disease pair, weakening causality."
        result = apply_mendelian_floor_guard(text)
        assert "MENDELIAN FLOOR GUARD" in result

    def test_flags_coloc_absent_phrasing(self):
        text = "Colocalization evidence is absent, undermining the causality case."
        result = apply_mendelian_floor_guard(text)
        assert "MENDELIAN FLOOR GUARD" in result

    def test_correct_text_unchanged(self):
        text = "P/LP variant support is strong; GWAS absence is expected for this Mendelian gene."
        result = apply_mendelian_floor_guard(text)
        assert "MENDELIAN FLOOR GUARD" not in result

    def test_empty_text_unchanged(self):
        assert apply_mendelian_floor_guard("") == ""


# ---------------------------------------------------------------------------
# WS7: expression breadth + GoF-tolerance framing
# ---------------------------------------------------------------------------


class TestInterpretExpressionContextBroad:
    def test_high_bulk_tpm_triggers_breadth_caveat(self):
        result = interpret_expression_context(25.0, None, "kidney")
        assert "breadth or magnitude alone is NOT" in result

    def test_low_tissue_specificity_triggers_breadth_caveat(self):
        result = interpret_expression_context(8.0, "Low tissue specificity", "kidney")
        assert "breadth or magnitude alone is NOT" in result

    def test_breadth_caveat_frames_risk_as_on_target_not_off_target(self):
        result = interpret_expression_context(25.0, None, "kidney")
        assert "ON-TARGET" in result
        # Must explicitly correct the off-target mislabeling, not endorse it.
        assert "Do NOT call this 'off-target'" in result

    def test_moderate_tpm_with_specific_hpa_is_neutral(self):
        result = interpret_expression_context(8.0, "Tissue enhanced", "kidney")
        assert result == ""

    def test_none_bulk_tpm_returns_empty(self):
        assert interpret_expression_context(None, "Low tissue specificity", "kidney") == ""


class TestInterpretGofToleranceSupport:
    def test_gof_and_lof_tolerant_supports_tolerability(self):
        result = interpret_gof_tolerance_support("gof", True)
        assert "SUPPORTS the tolerability" in result

    def test_gof_and_lof_tolerant_includes_chronic_inhibition_counterweight(self):
        """LoF-tolerance must not be presentable as standalone proof of inhibitor safety."""
        result = interpret_gof_tolerance_support("gof", True)
        assert "does NOT by itself" in result
        assert "chronic pharmacological inhibition" in result

    def test_gof_but_not_lof_tolerant_returns_empty(self):
        assert interpret_gof_tolerance_support("gof", False) == ""

    def test_lof_mechanism_returns_empty_even_if_tolerant(self):
        assert interpret_gof_tolerance_support("lof", True) == ""

    def test_no_mechanism_returns_empty(self):
        assert interpret_gof_tolerance_support(None, True) == ""


class TestInterpretExpressionContextForMechanism:
    """Biology-lens variant: shares the low-bulk dilution caveat, omits toxicity language."""

    def test_low_bulk_tpm_triggers_mechanism_caveat(self):
        result = interpret_expression_context_for_mechanism(1.17, None, "Kidney_Cortex")
        assert "does NOT establish" in result
        assert "toxicity" not in result.lower()

    def test_low_bulk_with_hpa_specific_mentions_celltype_localization(self):
        result = interpret_expression_context_for_mechanism(1.17, "Tissue enhanced", "Kidney_Cortex")
        assert "cell-type localization" in result

    def test_high_bulk_tpm_returns_empty_no_toxicity_framing(self):
        """High disease-tissue TPM is good mechanistic news for biology — must not emit
        the safety lens's toxicity-risk caveat, which has no place in a mechanism verdict."""
        assert interpret_expression_context_for_mechanism(25.0, None, "Lung") == ""

    def test_none_bulk_tpm_returns_empty(self):
        assert interpret_expression_context_for_mechanism(None, "Tissue enhanced", "kidney") == ""
