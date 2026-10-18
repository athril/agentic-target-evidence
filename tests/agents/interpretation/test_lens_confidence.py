# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _lens_confidence.py — evidence-computed lens and axis confidence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from agents.interpretation._lens_confidence import (
    MAX_CONFIDENCE,
    NON_DIRECTIONAL_CAP,
    TYPE_CAP,
    fact_check_violations,
    numeric_grounding_violations,
    score_verdict,
)
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.fact_checks import fact_checks
from services.evidence.source_registry import named_sources

_RUN = uuid.uuid4()
_KINDS = [
    EvidenceType.ARTICLE,
    EvidenceType.GENETICS,
    EvidenceType.CONSTRAINT,
    EvidenceType.DRUGGABILITY,
]


def _claim(
    text: str,
    et: EvidenceType = EvidenceType.GENETICS,
    source: uuid.UUID | None = None,
) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=source or uuid.uuid4(),
        run_id=_RUN,
        gene="TRPC6",
        disease="FSGS",
        evidence_type=et,
        claim_text=text,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
    )


def _axis(claims: list[CoreClaim], verdict: bool | None = True, **kw) -> AxisVerdict:
    return AxisVerdict(
        axis=kw.pop("axis", "causality"),
        verdict=verdict,
        confidence=kw.pop("confidence", 0.9),
        rationale=kw.pop("rationale", "Supported."),
        supporting_claim_ids=[str(c.evidence_id) for c in claims],
    )


def _verdict(axes: list[AxisVerdict], overall: str = "support", **kw) -> LensVerdict:
    return LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens=kw.pop("lens", "genetics"),
        target_gene="TRPC6",
        disease="FSGS",
        overall_verdict=overall,
        confidence=kw.pop("confidence", 0.9),
        axes=axes,
        **kw,
    )


def _flat(w: float):
    return lambda _c: w


class TestAxisScore:
    def test_one_top_weight_source_scores_half(self):
        c = _claim("x")
        out = score_verdict(_verdict([_axis([c])]), [c], _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.5)
        assert out.axes[0].evidence_sources == 1

    def test_independent_sources_accumulate_with_diminishing_returns(self):
        claims = [_claim(f"c{i}") for i in range(2)]
        out = score_verdict(_verdict([_axis(claims)]), claims, _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.75)

    def test_claims_from_one_source_count_once(self):
        src = uuid.uuid4()
        claims = [_claim(f"c{i}", source=src) for i in range(5)]
        out = score_verdict(_verdict([_axis(claims)]), claims, _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.5)
        assert out.axes[0].evidence_sources == 1

    def test_source_groups_merge_a_patent_family(self):
        claims = [_claim(f"p{i}", EvidenceType.PATENT) for i in range(3)]
        groups = {str(c.source_evidence_id): "patent-family:acme|inhibitors" for c in claims}
        out = score_verdict(
            _verdict([_axis(claims)], lens="commercial"), claims, _flat(0.8), source_groups=groups
        )
        assert out.axes[0].evidence_sources == 1
        assert out.axes[0].confidence == pytest.approx(0.4)

    def test_never_reaches_certainty(self):
        claims = [_claim(f"c{i}", et) for et in _KINDS for i in range(5)]
        out = score_verdict(_verdict([_axis(claims, axis="custom")]), claims, _flat(1.0))
        assert out.axes[0].confidence == MAX_CONFIDENCE

    def test_one_kind_of_evidence_is_capped_however_many_sources(self):
        """Seventy papers restating one finding are not seventy independent lines."""
        papers = [_claim(f"p{i}", EvidenceType.ARTICLE) for i in range(70)]
        out = score_verdict(_verdict([_axis(papers, axis="custom")]), papers, _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(TYPE_CAP)
        assert out.axes[0].evidence_sources == 70

    def test_independent_kinds_of_evidence_combine(self):
        claims = [
            _claim("a", EvidenceType.ARTICLE),
            _claim("b", EvidenceType.ARTICLE),
            _claim("u", EvidenceType.DRUGGABILITY),
            _claim("c", EvidenceType.DRUGGABILITY),
        ]
        out = score_verdict(_verdict([_axis(claims, axis="custom")]), claims, _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(1 - 0.25 * 0.25, abs=1e-3)

    def test_ids_the_lens_was_not_shown_score_nothing_and_are_flagged(self):
        shown = _claim("shown")
        axis = _axis([shown]).model_copy(
            update={"supporting_claim_ids": [str(shown.evidence_id), "not-a-real-id"]}
        )
        out = score_verdict(_verdict([axis]), [shown], _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.5)
        assert any(f.rule_id == "unknown_claim_ids" for f in out.validation_flags)

    def test_definitive_claim_sets_a_floor_on_a_favourable_axis(self):
        gencc = _claim("GenCC gene-disease validity for TRPC6: FSGS2 (Strong, Ambry Genetics)")
        out = score_verdict(_verdict([_axis([gencc])]), [gencc], _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.85)

    def test_definitive_floor_does_not_apply_to_an_unfavourable_axis(self):
        gencc = _claim("GenCC gene-disease validity for TRPC6: FSGS2 (Strong, Ambry Genetics)")
        out = score_verdict(
            _verdict([_axis([gencc], verdict=False)], overall="oppose"), [gencc], _flat(1.0)
        )
        assert out.axes[0].confidence == pytest.approx(0.5)

    def test_model_value_is_kept_for_audit(self):
        c = _claim("x")
        out = score_verdict(
            _verdict([_axis([c], confidence=0.95)], confidence=0.9), [c], _flat(1.0)
        )
        assert out.axes[0].model_confidence == 0.95
        assert out.model_confidence == 0.9

    def test_rescoring_is_idempotent(self):
        c = _claim("x")
        once = score_verdict(_verdict([_axis([c], confidence=0.95)]), [c], _flat(1.0))
        twice = score_verdict(once, [c], _flat(1.0))
        assert twice.confidence == once.confidence
        assert twice.model_confidence == once.model_confidence
        assert twice.axes[0].model_confidence == 0.95


class TestLensAggregate:
    def test_mean_is_capped_by_the_weakest_agreeing_axis(self):
        strong = [_claim(f"s{i}", et) for et in _KINDS[:2] for i in range(2)]  # ~0.94
        weak = [_claim("w")]  # 0.5
        axes = [_axis(strong, axis="a"), _axis(weak, axis="b")]
        out = score_verdict(_verdict(axes), strong + weak, _flat(1.0))
        assert out.confidence == pytest.approx(0.5 + 0.2)

    def test_contrary_axis_discounts_the_lens(self):
        fav = [_claim("f1"), _claim("f2")]  # 0.75
        contra = [_claim("c1")]  # 0.5
        axes = [_axis(fav, axis="a"), _axis(contra, verdict=False, axis="b")]
        out = score_verdict(_verdict(axes), fav + contra, _flat(1.0))
        assert out.confidence == pytest.approx(0.75 * (1 - 0.5 * 0.5), abs=1e-3)

    def test_directional_verdict_without_agreeing_axis_scores_zero(self):
        c = _claim("x")
        out = score_verdict(_verdict([_axis([c], verdict=None)]), [c], _flat(1.0))
        assert out.confidence == 0.0

    def test_axis_citing_nothing_does_not_cap_the_lens(self):
        """PCSK9 commercial: 'market size: No' with no cited evidence capped the lens at 20%."""
        ip, comp = _claim("72 patents"), _claim("13 phase 3 programs", EvidenceType.CONSTRAINT)
        axes = [
            _axis([ip], verdict=False, axis="ip_landscape"),
            _axis([comp], verdict=False, axis="competitive_opportunity"),
            _axis([], verdict=False, axis="market_size"),
        ]
        out = score_verdict(_verdict(axes, overall="oppose"), [ip, comp], _flat(1.0))
        assert out.axes[2].confidence == 0.0
        assert out.confidence == pytest.approx(0.5, abs=1e-3)

    def test_non_directional_verdict_keeps_model_value_capped(self):
        """A neutral verdict citing nothing used to report the model's 85%."""
        out = score_verdict(
            _verdict([_axis([], verdict=None)], overall="neutral", confidence=0.85), [], _flat(1.0)
        )
        assert out.confidence == NON_DIRECTIONAL_CAP
        assert out.model_confidence == 0.85


class TestNeutralAgainstAxes:
    """A neutral verdict on all-agreeing axes follows the axes (TRPC6 clinical lens:
    two favourable axes at 86%/80% were published as an unscored "neutral")."""

    def test_neutral_on_all_favourable_axes_becomes_support(self):
        a, b = _claim("a"), _claim("b")
        axes = [_axis([a], axis="precedent"), _axis([b], axis="validation")]
        out = score_verdict(_verdict(axes, overall="neutral", confidence=0.7), [a, b], _flat(1.0))
        assert out.overall_verdict == "support"
        assert out.confidence == pytest.approx(0.5, abs=1e-3)
        assert out.model_confidence == 0.7
        assert [f.rule_id for f in out.validation_flags] == ["neutral_axis_conflict"]

    def test_neutral_on_all_unfavourable_axes_becomes_oppose(self):
        a, b = _claim("a"), _claim("b")
        axes = [_axis([a], verdict=False, axis="x"), _axis([b], verdict=False, axis="y")]
        out = score_verdict(_verdict(axes, overall="neutral"), [a, b], _flat(1.0))
        assert out.overall_verdict == "oppose"

    @pytest.mark.parametrize(
        "verdicts", [(True, False), (True, None), (None, None)], ids=["mixed", "one-unsure", "none"]
    )
    def test_neutral_stays_when_axes_disagree_or_are_uncertain(self, verdicts):
        claims = [_claim("a"), _claim("b")]
        axes = [
            _axis([c], verdict=v, axis=f"ax{i}")
            for i, (c, v) in enumerate(zip(claims, verdicts, strict=True))
        ]
        out = score_verdict(_verdict(axes, overall="neutral"), claims, _flat(1.0))
        assert out.overall_verdict == "neutral"
        assert not out.validation_flags

    def test_earlier_flags_are_kept(self):
        clinvar = _claim("69 Pathogenic/Likely-Pathogenic ClinVar variant(s) in TRPC6")
        orphanet = _claim("Orphanet associations for TRPC6: Disease-causing germline mutation(s)")
        axis = _axis([clinvar], axis="custom", rationale="Orphanet and ClinVar support causality.")
        out = score_verdict(_verdict([axis], overall="neutral"), [clinvar, orphanet], _flat(1.0))
        assert {f.rule_id for f in out.validation_flags} == {
            "citation_repair",
            "neutral_axis_conflict",
        }


class TestCitationRepair:
    def test_named_but_uncited_source_is_attached_and_flagged(self):
        clinvar = _claim(
            "69 Pathogenic/Likely-Pathogenic ClinVar variant(s) in TRPC6 (40 P, 29 LP)"
        )
        orphanet = _claim("Orphanet associations for TRPC6: Disease-causing germline mutation(s)")
        axis = _axis([clinvar], axis="custom", rationale="Orphanet and ClinVar support causality.")
        out = score_verdict(_verdict([axis]), [clinvar, orphanet], _flat(1.0))
        assert str(orphanet.evidence_id) in out.axes[0].supporting_claim_ids
        assert out.axes[0].evidence_sources == 2
        flag = next(f for f in out.validation_flags if f.rule_id == "citation_repair")
        assert "Orphanet" in flag.message

    def test_literature_is_never_attached(self):
        paper = _claim("ClinVar lists TRPC6 variants.", EvidenceType.ARTICLE)
        axis = _axis([], rationale="ClinVar supports causality.")
        out = score_verdict(_verdict([axis]), [paper], _flat(1.0))
        assert out.axes[0].supporting_claim_ids == []

    def test_already_cited_source_is_left_alone(self):
        clinvar = _claim("ClinVar variant(s) in TRPC6 (40 P, 29 LP)")
        other = _claim("ClinVar summary row")
        axis = _axis([clinvar], axis="custom", rationale="ClinVar supports causality.")
        out = score_verdict(_verdict([axis]), [clinvar, other], _flat(1.0))
        assert out.axes[0].supporting_claim_ids == [str(clinvar.evidence_id)]


_CLINVAR = "69 Pathogenic/Likely-Pathogenic ClinVar variant(s) in TRPC6 (40 P, 29 LP)"


class TestNumericGrounding:
    def _found(self, text: str, claims: list[str]) -> list:
        return numeric_grounding_violations(text, [_claim(t) for t in claims], named_sources())

    @pytest.mark.parametrize(
        "text",
        [
            "ClinVar reports 40 pathogenic and 29 likely-pathogenic variants.",
            "ClinVar lists 69 pathogenic/likely pathogenic variants.",
            "ClinVar has pathogenic (40) and likely pathogenic (29) variants.",
        ],
    )
    def test_numbers_from_the_named_source_pass(self, text):
        assert self._found(text, [_CLINVAR]) == []

    def test_numbers_absent_from_the_named_source_are_flagged(self):
        """The TRPC6 genetics lens wrote 30/14 while the ClinVar claim said 40/29."""
        text = "multiple pathogenic (30) and likely-pathogenic (14) missense variants in ClinVar"
        found = self._found(text, [_CLINVAR])
        assert len(found) == 2
        assert "30 does not appear in the ClinVar" in found[0].note

    def test_rounded_decimal_matches_to_its_written_precision(self):
        claim = "Constraint interpretation for TRPC6: LOEUF=0.759 (relatively LoF-tolerant)"
        assert self._found("gnomAD LOEUF is 0.76.", [claim]) == []
        assert len(self._found("gnomAD LOEUF is 0.70.", [claim])) == 1

    def test_identifiers_and_years_are_not_numbers(self):
        text = "ClinVar (2024, gnomAD v4) lists 69 variants incl. Q889K."
        assert self._found(text, [_CLINVAR]) == []

    def test_sentences_not_naming_a_checked_source_are_ignored(self):
        text = "A cohort study found 3 pathogenic variants in exon 2. ClinVar agrees overall."
        assert self._found(text, [_CLINVAR]) == []

    def test_flags_a_figure_from_a_source_the_lens_was_never_shown(self):
        """No record of the source reached the lens, so the figure came from the model's
        memory (IL17A × psoriasis: invented gnomAD LOEUF/pLI in a run without gnomAD)."""
        found = self._found("ClinVar lists 12 variants.", ["Unrelated claim 5"])
        assert [v.rule_id for v in found] == ["grounding.number_unsourced"]
        assert "no ClinVar data reached this lens" in found[0].note

    def test_a_source_named_without_a_figure_is_not_flagged(self):
        assert self._found("ClinVar has no record for this gene.", ["Unrelated claim 5"]) == []

    def test_any_registry_source_is_checked_not_just_clinvar(self):
        claim = "TRPC6 CRISPR dependency (DepMap): fitness effect in 2/1178 cell lines (0.2%)."
        assert self._found("DepMap shows 2 of 1178 lines.", [claim]) == []
        assert len(self._found("DepMap shows 12 dependent lines.", [claim])) == 1


class TestNumericGroundingContext:
    def test_figure_quoted_from_a_context_line_naming_the_source_is_grounded(self):
        """The safety lens quoted FAERS rates from its context block, not from a claim."""
        context = "FAERS — SPARSENTAN: 5,866 total reports. serious_rate=16.6%. death_rate=0.5%."
        text = "FAERS shows a serious rate of 16.6% and a death rate of 0.5% across 5866 reports."
        found = numeric_grounding_violations(text, [], named_sources(), context)
        assert found == []

    def test_context_lines_for_other_sources_do_not_ground(self):
        context = "DepMap: 16.6 something unrelated"
        text = "FAERS shows a serious rate of 16.6%."
        claims = [_claim("SPARSENTAN FAERS: 5866 adverse-event reports, 971 serious")]
        assert len(numeric_grounding_violations(text, claims, named_sources(), context)) == 1

    def test_rows_of_a_block_headed_by_the_source_are_grounded(self):
        """A pre-rendered table names its source only in its first line."""
        context = (
            "DepMap CRISPR dependency: 13/1178 lines dependent\n"
            "  Top lineages by dependency fraction:\n"
            "    • Lineage A: 10/53 dependent, mean -0.170\n\n"
            "Another block: 7 items"
        )
        text = "DepMap shows lineage A is selectively dependent (10/53 lines)."
        assert numeric_grounding_violations(text, [], named_sources(), context) == []
        text = "DepMap shows 7 lineages are dependent."
        assert len(numeric_grounding_violations(text, [], named_sources(), context)) == 1


class TestReaggregate:
    def test_post_scoring_axis_change_updates_the_lens_number(self):
        from agents.interpretation._lens_confidence import reaggregate

        c = _claim("x")
        scored = score_verdict(_verdict([_axis([c])], confidence=0.9), [c], _flat(1.0))
        assert scored.confidence == pytest.approx(0.5)
        clamped = scored.model_copy(
            update={"axes": [scored.axes[0].model_copy(update={"confidence": 0.9})]}
        )
        out = reaggregate(clamped)
        assert out.confidence == pytest.approx(0.9)
        assert out.model_confidence == 0.9


class TestFactChecks:
    def _found(self, text: str, claims: list[str], disease: str = "FSGS") -> list:
        pool = [_claim(t, EvidenceType.REGULATORY) for t in claims]
        return fact_check_violations(text, pool, fact_checks(), "TRPC6", disease)

    def test_denied_boxed_warning_is_flagged(self):
        """The regulatory lens wrote 'no black-box warnings' for a label that has one."""
        claims = [
            "SPARSENTAN label, of a drug acting on TRPC6, carries a boxed warning: "
            "WARNING: HEPATOTOXICITY"
        ]
        found = self._found("No black-box warnings are present for SPARSENTAN.", claims)
        assert [c.id for c, _ in found] == ["boxed_warning_denied"]

    def test_denying_a_competitors_boxed_warning_is_not_flagged(self):
        """A boxed label of a drug acting on another target does not make "no boxed
        warning on in-class drugs" false."""
        claims = ["DRUGY label carries a boxed warning: WARNING: SERIOUS INFECTIONS"]
        assert self._found("No black-box warnings on in-class drugs.", claims) == []

    def test_denial_is_fine_when_the_label_has_none(self):
        claims = ["DRUGX label carries no boxed warning"]
        assert self._found("There are no boxed warnings.", claims) == []

    def test_denied_approval_in_the_indication_is_flagged(self):
        claims = [
            "SPARSENTAN is FDA-approved in this indication (Focal Segmental "
            "Glomerulosclerosis): To reduce proteinuria ..."
        ]
        text = "There are approved drugs for a related indication (IgAN)."
        found = self._found(text, claims, disease="Focal Segmental Glomerulosclerosis")
        assert [c.id for c, _ in found] == ["indication_approval_denied"]
        text = "No approved drugs for Focal Segmental Glomerulosclerosis exist."
        assert self._found(text, claims, disease="Focal Segmental Glomerulosclerosis")

    def test_target_level_statement_is_not_an_indication_denial(self):
        claims = ["SPARSENTAN is FDA-approved in this indication (FSGS): To reduce proteinuria"]
        assert self._found("No approved drugs target TRPC6.", claims) == []


class TestScoreVerdictChecks:
    def test_grounding_violation_is_annotated_not_rewritten(self):
        clinvar = _claim(_CLINVAR)
        axis = _axis([clinvar], rationale="ClinVar shows 30 pathogenic variants.")
        out = score_verdict(_verdict([axis]), [clinvar], _flat(1.0))
        assert out.axes[0].rationale.startswith("ClinVar shows 30 pathogenic variants.")
        assert "NUMBER CHECK" in out.axes[0].rationale
        assert any(f.rule_id == "numeric_grounding" for f in out.validation_flags)

    def test_fact_check_flag_is_recorded(self):
        label = _claim(
            "DRUGX label, of a drug acting on TRPC6, carries a boxed warning: WARNING: X",
            EvidenceType.REGULATORY,
        )
        axis = _axis([label], axis="label_safety", rationale="No boxed warning applies.")
        out = score_verdict(_verdict([axis], lens="regulatory"), [label], _flat(0.8))
        assert any(f.rule_id == "fact_check.boxed_warning_denied" for f in out.validation_flags)
        assert "FACT CHECK" in out.axes[0].rationale

    def test_verdict_contradicting_all_axes_is_flagged(self):
        """The commercial lens said 'support' while both scored axes were unfavourable."""
        claims = [_claim("a"), _claim("b")]
        axes = [
            _axis([claims[0]], verdict=False, axis="ip"),
            _axis([claims[1]], verdict=False, axis="competition"),
        ]
        out = score_verdict(_verdict(axes, lens="commercial"), claims, _flat(0.8))
        assert out.confidence == 0.0
        assert any(f.rule_id == "verdict_axis_conflict" for f in out.validation_flags)

    def test_regulatory_approval_in_indication_sets_a_floor(self):
        label = _claim(
            "SPARSENTAN is FDA-approved in this indication (FSGS): To reduce proteinuria",
            EvidenceType.REGULATORY,
        )
        axis = _axis([label], axis="approval_precedent")
        out = score_verdict(_verdict([axis], lens="regulatory"), [label], _flat(0.8))
        assert out.axes[0].confidence == pytest.approx(0.85)


class TestAxisEvidenceAttachment:
    def test_structured_records_the_axis_is_about_are_attached(self):
        """The biology lens cited UniProt only; ChEMBL and DGIdb were in its input."""
        uniprot = _claim(
            "UniProt Q9Y210: TRPC6. Localization: Cell membrane.", EvidenceType.DRUGGABILITY
        )
        chembl = _claim("ChEMBL CHEMBL2417347: 761 bioactivities.", EvidenceType.DRUGGABILITY)
        paper = _claim("TRPC6 inhibitors exist.", EvidenceType.ARTICLE)
        axis = _axis([uniprot], axis="druggability")
        out = score_verdict(_verdict([axis], lens="biology"), [uniprot, chembl, paper], _flat(0.8))
        assert out.axes[0].supporting_claim_ids == [
            str(uniprot.evidence_id),
            str(chembl.evidence_id),
        ]
        assert out.axes[0].added_claim_ids == [str(chembl.evidence_id)]

    def test_literature_is_never_attached_by_config(self):
        paper = _claim("TRPC6 inhibitors exist.", EvidenceType.ARTICLE)
        axis = _axis([], axis="druggability")
        out = score_verdict(_verdict([axis], lens="biology"), [paper], _flat(0.8))
        assert out.axes[0].supporting_claim_ids == []

    def test_excluded_phrases_are_not_attached(self):
        """An off-indication GWAS breadth row is not support for causality."""
        gwas = _claim("TRPC6 locus: 37 distinct GWAS traits found (0 matched FSGS).")
        gencc = _claim("GenCC gene-disease validity for TRPC6: FSGS2 (Strong, Ambry Genetics)")
        out = score_verdict(_verdict([_axis([], axis="causality")]), [gwas, gencc], _flat(1.0))
        assert out.axes[0].supporting_claim_ids == [str(gencc.evidence_id)]

    def test_unmapped_axis_gets_nothing(self):
        claim = _claim("GenCC ... (Strong, Lab)")
        out = score_verdict(_verdict([_axis([], axis="custom")]), [claim], _flat(1.0))
        assert out.axes[0].supporting_claim_ids == []


class TestReviewedLiterature:
    def test_reviewed_axis_counts_only_picked_literature(self):
        """Model-cited papers vary run to run; only the citation step's picks score."""
        cited_paper = _claim("cited by the lens model", EvidenceType.ARTICLE)
        picked_paper = _claim("picked by the citation step", EvidenceType.ARTICLE)
        axis = _axis([cited_paper, picked_paper], axis="custom").model_copy(
            update={
                "literature_reviewed": True,
                "literature_picks": [str(picked_paper.evidence_id)],
            }
        )
        out = score_verdict(_verdict([axis]), [cited_paper, picked_paper], _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.5)  # one source, not two
        assert str(cited_paper.evidence_id) in out.axes[0].supporting_claim_ids  # still listed

    def test_unreviewed_axis_counts_model_citations(self):
        papers = [_claim("a", EvidenceType.ARTICLE), _claim("b", EvidenceType.ARTICLE)]
        out = score_verdict(_verdict([_axis(papers, axis="custom")]), papers, _flat(1.0))
        assert out.axes[0].confidence == pytest.approx(0.75)

    def test_structured_evidence_unaffected_by_review(self):
        record = _claim("GenCC ... (Strong, Lab)")
        axis = _axis([record], axis="custom").model_copy(update={"literature_reviewed": True})
        out = score_verdict(_verdict([axis]), [record], _flat(1.0))
        assert out.axes[0].confidence >= 0.5


class TestAxisScopedRules:
    def test_validation_attaches_only_trial_results(self):
        results = _claim(
            "NCT1 (PHASE2 COMPLETED) primary outcome — UPCR: Drug -38; p=0.002",
            EvidenceType.CLINICAL_TRIAL,
        )
        recruiting = _claim(
            "PHASE3 RECRUITING clinical trial NCT2: Drug in FSGS", EvidenceType.CLINICAL_TRIAL
        )
        axis = _axis([], axis="clinical_validation")
        out = score_verdict(_verdict([axis], lens="clinical"), [results, recruiting], _flat(0.9))
        assert out.axes[0].supporting_claim_ids == [str(results.evidence_id)]

    def test_phase3_trial_settles_precedent_but_not_validation(self):
        """Axis-scoped floor: a recruiting phase 3 is precedent, not validation."""
        trial = _claim(
            "PHASE3 RECRUITING clinical trial NCT2: Drug in FSGS", EvidenceType.CLINICAL_TRIAL
        )
        precedent = _axis([trial], axis="clinical_precedent")
        validation = _axis([trial], axis="clinical_validation")
        out = score_verdict(_verdict([precedent, validation], lens="clinical"), [trial], _flat(0.9))
        assert out.axes[0].confidence == pytest.approx(0.85)
        assert out.axes[1].confidence == pytest.approx(0.45)


class TestNumericFloor:
    _SIG = "NCT1 (PHASE2 COMPLETED) primary outcome — UPCR: Drug vs Placebo: Relative difference -39.82, p=0.0024"
    _NS = "NCT1 (PHASE2 COMPLETED) primary outcome — UPCR: Drug vs Placebo: Relative difference -2.1, p=0.79"

    def test_significant_primary_outcome_settles_a_favourable_validation_axis(self):
        """One positive randomised trial is one source; the floor gives it its due."""
        trial = _claim(self._SIG, EvidenceType.CLINICAL_TRIAL)
        out = score_verdict(
            _verdict([_axis([trial], axis="clinical_validation")], lens="clinical"),
            [trial],
            _flat(0.9),
        )
        assert out.axes[0].confidence == pytest.approx(0.8)

    def test_non_significant_outcome_gets_no_floor(self):
        trial = _claim(self._NS, EvidenceType.CLINICAL_TRIAL)
        out = score_verdict(
            _verdict([_axis([trial], axis="clinical_validation")], lens="clinical"),
            [trial],
            _flat(0.9),
        )
        assert out.axes[0].confidence == pytest.approx(0.45)

    def test_significant_outcome_does_not_raise_an_unfavourable_axis(self):
        trial = _claim(self._SIG, EvidenceType.CLINICAL_TRIAL)
        axis = _axis([trial], verdict=False, axis="clinical_validation")
        out = score_verdict(
            _verdict([axis], lens="clinical", overall="oppose"), [trial], _flat(0.9)
        )
        assert out.axes[0].confidence == pytest.approx(0.45)


class TestTargetFactChecks:
    def _found(self, text, claims, gene="TRPC6"):
        pool = [_claim(t, EvidenceType.REGULATORY) for t in claims]
        return [c.id for c, _ in fact_check_violations(text, pool, fact_checks(), gene, "FSGS")]

    _OFF = (
        "SPARSENTAN's labelled mechanism of action does not name TRPC6; its label safety "
        "findings are drug effects, not TRPC6 effects"
    )

    def test_off_target_boxed_warning_blamed_on_the_target(self):
        """The regulatory lens called sparsentan's warning 'directly relevant to the target'."""
        text = "SPARSENTAN carries a black-box warning for hepatotoxicity, which is directly relevant to the target biology."
        assert self._found(text, [self._OFF]) == ["off_target_label_safety"]

    def test_on_target_label_does_not_fire(self):
        text = "DRUGX carries a boxed warning for hepatotoxicity, which is relevant to the target."
        assert self._found(text, ["DRUGX's labelled mechanism of action names TRPC6"]) == []

    def test_denied_clinical_stage_program(self):
        """The commercial lens wrote 'no approved or clinical-stage drugs targeting TRPC6'."""
        trial = "PHASE3 RECRUITING clinical trial NCT07220083: BI 764198, Placebo in FSGS (n=286)"
        text = "The competitive field is underserved, with no approved or clinical-stage drugs targeting TRPC6 in FSGS."
        assert self._found(text, [trial]) == ["target_pipeline_denied"]
        assert self._found(text, ["PHASE1 COMPLETED clinical trial NCT1: X"]) == []

    @pytest.mark.parametrize(
        "text",
        [
            "The label carries a boxed warning for hepatotoxicity, which may raise regulatory "
            "scrutiny for new drugs targeting this pathway.",
            "While there is approval for the indication, the presence of a mechanism-relevant "
            "boxed warning raises regulatory risk.",
            "SPARSENTAN carries a black-box warning for hepatotoxicity, which may pose "
            "regulatory challenges for new drugs in this class.",
        ],
    )
    def test_off_target_warning_framed_as_class_risk(self, text):
        """TRPC6 regulatory lens: after the axes were corrected, the prose still framed
        sparsentan's warning as a risk for drugs targeting the pathway."""
        assert self._found(text, [self._OFF]) == ["off_target_label_safety"]

    @pytest.mark.parametrize(
        "text",
        [
            "The label carries a boxed warning for hepatotoxicity, which is relevant to the "
            "drug sparsentan, not directly to the safety profile of the target gene TRPC6.",
            "SPARSENTAN carries a boxed warning for hepatotoxicity; this is a drug effect, "
            "not a TRPC6 effect.",
            # TRPC6 regulatory lens: a negated sentence was flagged.
            "SPARSENTAN carries a boxed warning for hepatotoxicity and embryo-fetal toxicity, "
            "which are not directly related to TRPC6 inhibition.",
        ],
    )
    def test_off_target_warning_scoped_to_the_drug_passes(self, text):
        assert self._found(text, [self._OFF]) == []

    def test_no_known_drugs_denies_a_clinical_stage_program(self):
        """TRPC6 investigator: 'No known drugs were found to target TRPC6' beside a phase 3."""
        trial = "PHASE3 RECRUITING clinical trial NCT07220083: BI 764198, Placebo in FSGS (n=286)"
        text = "No known drugs were found to target TRPC6 in the context of FSGS."
        assert self._found(text, [trial]) == ["target_pipeline_denied"]
        assert self._found("No approved drugs target TRPC6.", [trial]) == []
