# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Registry of lens-output guard families over one shared fact sheet.

Pure module — no I/O, fully unit-testable.

    GuardFacts (deterministic facts for one run)
        │
        ▼
    GUARD_RULES[family].check(text, facts) ──▶ list[Violation]
        ▲
        └── GuardRule.is_armed(facts) — the inputs the family needs are present;
            an unarmed family is reported as "not evaluated", never silently skipped.

Every family also declares the wording it *recommends* — the phrasing its prompt
framing and its own annotation steer the LLM toward. The invariant suite
(tests/services/evidence/test_guard_invariants.py) runs every family over every
family's recommended phrasings and fails if any guard flags compliant text — the
failure mode behind the Mendelian-floor, Orphanet, target-scope and tissue false
positives found in historical runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from services.evidence.clinical_trial_interpret import (
    ATTRIBUTION_MARKER,
    TrialFact,
    check_clinical_phase_guard,
    check_outcome_terms,
    check_trial_attribution,
    check_trial_outcome_guard,
    trial_phase_counts,
)
from services.evidence.commercial_interpret import check_commercial_guards
from services.evidence.constraint_interpret import (
    MENDELIAN_GWAS_ABSENCE_PHRASE,
    ConstraintReading,
    check_constraint_guards,
    check_depmap_relevance,
    check_gwas_absence_claim,
    check_mendelian_floor,
    missense_tolerant_phrase,
)
from services.evidence.disease_tissue import check_tissue_relevance
from services.evidence.grounding_guards import (
    check_cell_type_claims,
    check_drug_approval,
    check_modality,
    check_phenotype_grounding,
    check_phenotype_omission,
    check_stopped_trial_validation,
    check_tissue_claims,
    check_trial_readout,
)
from services.evidence.guard_core import Violation
from services.evidence.readings import (
    DependencyReading,
    QuantReading,
    check_pooled_phase_wording,
    check_quantity_wording,
    dependency_is_uninformative,
    trial_count_readings,
)

GuardFamily = Literal[
    "constraint",
    "mendelian_floor",
    "commercial",
    "clinical_trial",
    "trial_outcome",
    "outcome_terms",
    "tissue_relevance",
    "dependency_relevance",
    "quantity",
    "phenotype_grounding",
    "tissue_claim",
    "drug_approval",
    "trial_readout",
    "modality",
    "phenotype_omission",
    "cell_type_claim",
    "trial_attribution",
]


class GuardFacts(BaseModel):
    """The deterministic facts lens prose is checked against, for one gene × disease run."""

    model_config = ConfigDict(frozen=True)

    gene: str = ""
    disease: str = ""
    constraint: ConstraintReading | None = None
    mendelian_grade: bool = False
    # GWAS Catalog traits matched to the run's disease (0 = none matched or not retrieved).
    gwas_traits_in_scope: int = 0
    trial_facts: list[TrialFact] = []
    # Registry id → every number the trial's own sources report (registry record, claims
    # naming the id, claims from publications linked to it); None = not computed.
    trial_values: dict[str, tuple[float, ...]] | None = None
    # Outcome-term labels the claims shown to the lens report; None = not computed.
    reported_outcomes: frozenset[str] | None = None
    known_drugs_count: int = 0
    approved_count: int = 0
    indication_approved_drug_count: int = 0
    indication_active_trial_count: int = 0
    # Whether any prevalence record (Orphanet or GBD) names the run's indication.
    indication_prevalence_found: bool = True
    top_tissues: list[str] = []
    disease_relevant_tissues: list[str] = []
    dependency: DependencyReading | None = None
    is_oncology: bool = False
    # Mouse KO phenotype labels for this gene (Open Targets / MGI / IMPC); None = not
    # retrieved, [] = retrieved and empty.
    mouse_phenotypes: list[str] | None = None
    # GTEx tissue → 1-based rank by median TPM (empty = no GTEx data).
    tissue_ranks: dict[str, int] = {}
    hpa_specificity: str = ""
    # The disease's cell types (disease-tissue map) and those some claim names.
    cell_types: list[str] = []
    cell_types_reported: frozenset[str] | None = None
    # Known drug → approved anywhere (Open Targets known drugs; salt forms merged).
    drug_approval: dict[str, bool] = {}

    def quant_readings(self) -> list[QuantReading]:
        """Every banded quantity derivable from these facts (for the quantity family)."""
        readings: list[QuantReading] = []
        if self.dependency is not None:
            readings.append(self.dependency.reading)
        if self.trial_facts:
            readings.extend(
                trial_count_readings(
                    trial_phase_counts(self.trial_facts), self.trial_ids_by_phase()
                )
            )
        return readings

    def trial_ids_by_phase(self) -> dict[int, list[str]]:
        """NCT ids of the retrieved trials per phase (a multi-phase trial is in each)."""
        ids: dict[int, list[str]] = {}
        for f in self.trial_facts:
            if not f.is_therapeutic:
                continue
            for n in f.phase_numbers:
                ids.setdefault(n, []).append(f.nct_id)
        return ids


@dataclass(frozen=True)
class GuardRule:
    family: GuardFamily
    marker: str  # the "<MARKER>" in "[⚠ <MARKER>: …]" annotations
    flag_rule_id: str  # ValidationFlag.rule_id (stable across releases — audit consumers key on it)
    flag_message: str
    check: Callable[[str, GuardFacts], list[Violation]]
    is_armed: Callable[[GuardFacts], bool]
    missing_inputs: str  # what "not evaluated" means for this family
    recommended_phrasings: tuple[str, ...] = ()
    # Axes that lose confidence when this family fires: prose that omits or invents a
    # fact the axis rests on should not carry full weight into the lens aggregate.
    penalised_axes: tuple[str, ...] = ()


def _dependency_detail(facts: GuardFacts) -> str:
    return facts.dependency.detail() if facts.dependency else ""


GUARD_RULES: dict[GuardFamily, GuardRule] = {
    "constraint": GuardRule(
        family="constraint",
        marker="CONSTRAINT GUARD",
        flag_rule_id="constraint_interpretation_guard",
        flag_message=(
            "Constraint guard activated: narrative/rationale contained an unsupported "
            "haploinsufficiency, missense-criticality/constraint, or mis_z-direction claim "
            "that contradicts the precomputed gnomAD constraint bands; annotated rather than "
            "silently rewritten."
        ),
        check=lambda text, f: check_constraint_guards(text, f.constraint) if f.constraint else [],
        is_armed=lambda f: f.constraint is not None,
        missing_inputs="no gnomAD constraint reading",
        recommended_phrasings=(
            "GENE-A is relatively LoF-tolerant (NOT haploinsufficient).",
            "LOEUF=0.759 indicates the gene is relatively LoF-tolerant (NOT haploinsufficient).",
            "GENE-A is LoF-tolerant with no meaningful missense constraint.",
            missense_tolerant_phrase("GENE-A", gof=True),
            missense_tolerant_phrase("GENE-A", gof=False),
            "note: higher mis_z = more missense-constrained",
            "Absence at this allele count is uninformative — not evidence of biallelic "
            "lethality; selection signal comes from LOEUF/o-e.",
        ),
    ),
    "mendelian_floor": GuardRule(
        family="mendelian_floor",
        marker="MENDELIAN FLOOR GUARD",
        flag_rule_id="mendelian_causality_floor",
        flag_message=(
            "Mendelian floor guard: verdict text treated GWAS/colocalization absence as a "
            "causality negative for a Mendelian-grade gene-disease pair, or called GWAS "
            "signal absent although GWAS traits match this disease; annotated rather than "
            "silently rewritten."
        ),
        check=lambda text, f: (
            (check_mendelian_floor(text) if f.mendelian_grade else [])
            + check_gwas_absence_claim(text, f.gwas_traits_in_scope)
        ),
        is_armed=lambda f: f.mendelian_grade or f.gwas_traits_in_scope > 0,
        missing_inputs=(
            "gene-disease pair is not Mendelian-grade and no GWAS trait matches the disease "
            "(rule does not apply)"
        ),
        recommended_phrasings=(
            MENDELIAN_GWAS_ABSENCE_PHRASE,
            "The absence of GWAS signal is expected for a rare Mendelian disease gene.",
            "No GWAS or colocalization signal was found, which is expected for a Mendelian "
            "disease gene and does not lower causality.",
        ),
    ),
    "commercial": GuardRule(
        family="commercial",
        marker="COMMERCIAL GUARD",
        flag_rule_id="commercial_overstatement_guard",
        flag_message=(
            "Commercial guard activated: verdict text asserted a blanket absence of drugs, "
            "called the indication 'underserved' on target-level evidence, or declared market "
            "size 'unknown' from Orphanet silence alone; annotated rather than silently "
            "rewritten."
        ),
        check=lambda text, f: check_commercial_guards(
            text,
            known_drugs_count=f.known_drugs_count,
            approved_count=f.approved_count,
            indication_approved_drug_count=f.indication_approved_drug_count,
            indication_active_trial_count=f.indication_active_trial_count,
            gene=f.gene,
            indication_prevalence_found=f.indication_prevalence_found,
        ),
        is_armed=lambda f: True,
        missing_inputs="",
        recommended_phrasings=(
            "There is no approved GENE-A-targeted therapy for DISEASE-A.",
            "No approved or clinical-stage therapy targeting GENE-A was found in the retrieved "
            "evidence.",
            "The competitive field is underserved at the target level, but the indication is "
            "contested by other mechanisms.",
            "GENE-A-targeted therapies remain underserved; the indication itself is crowded.",
            "The market size could not be sized from Orphanet.",
            "Prevalence is not sizeable from Orphanet/GBD; external estimates may exist.",
        ),
    ),
    "clinical_trial": GuardRule(
        family="clinical_trial",
        marker="CLINICAL TRIAL GUARD",
        flag_rule_id="clinical_trial_phase_guard",
        flag_message=(
            "Clinical trial guard activated: narrative/rationale misstated a trial's phase or "
            "recruitment status (e.g. conflating trials of different phases under one phase "
            "number, or calling a closed trial 'recruiting'); annotated rather than silently "
            "rewritten."
        ),
        check=lambda text, f: check_clinical_phase_guard(text, f.trial_facts),
        is_armed=lambda f: bool(f.trial_facts),
        missing_inputs="no registry trial facts",
        recommended_phrasings=(
            "DRUG-A is in one Phase 3 trial (NCT00000002, recruiting) after a completed "
            "Phase 2 trial (NCT00000001).",
        ),
    ),
    "trial_outcome": GuardRule(
        family="trial_outcome",
        marker="TRIAL OUTCOME GUARD",
        flag_rule_id="trial_outcome_guard",
        flag_message=(
            "Trial outcome guard activated: narrative/rationale misstated which arms of a trial "
            "reached significance on the posted primary outcome, said per-dose results were "
            "not reported, or called a non-dose-ordered effect dose-dependent; annotated rather "
            "than silently rewritten."
        ),
        check=lambda text, f: check_trial_outcome_guard(text, f.trial_facts),
        is_armed=lambda f: any(t.arm_results for t in f.trial_facts),
        missing_inputs="no posted primary-outcome comparisons",
        recommended_phrasings=(
            "In NCT00000001 only the DRUG-A 20 mg arm reached significance on the primary "
            "outcome (p=0.0024); the 40 mg and 80 mg arms did not (p=0.7906, p=0.1325).",
            "Significance does not rise with dose, so there is no dose-response.",
        ),
    ),
    "outcome_terms": GuardRule(
        family="outcome_terms",
        marker="OUTCOME TERM GUARD",
        flag_rule_id="unreported_outcome_guard",
        flag_message=(
            "Outcome term guard activated: narrative/rationale asserted a clinical outcome "
            "(e.g. stable disease, biomarker engagement) that no claim shown to the lens "
            "reports; annotated rather than silently rewritten."
        ),
        check=lambda text, f: check_outcome_terms(text, f.reported_outcomes or frozenset()),
        is_armed=lambda f: f.reported_outcomes is not None,
        missing_inputs="claim outcome terms not computed",
        recommended_phrasings=(
            "No biomarker engagement data were reported for the Phase 2 trial.",
        ),
    ),
    "tissue_relevance": GuardRule(
        family="tissue_relevance",
        marker="TISSUE RELEVANCE GUARD",
        flag_rule_id="tissue_relevance_guard",
        flag_message=(
            "Tissue relevance guard activated: verdict text tied a high-bulk-TPM, non-disease "
            "tissue to disease relevance; bulk GTEx TPM rank is not a proxy for disease "
            "relevance — annotated rather than silently rewritten."
        ),
        check=lambda text, f: check_tissue_relevance(
            text, f.top_tissues, f.disease_relevant_tissues, f.disease or "this disease"
        ),
        is_armed=lambda f: bool(f.top_tissues and f.disease_relevant_tissues),
        missing_inputs="no curated disease-tissue mapping",
        recommended_phrasings=(
            "Lung ranks highest by bulk TPM but is not disease-relevant; the disease-relevant "
            "tissue is Kidney_Cortex.",
            "Bulk expression is highest in non-disease-relevant tissues (Lung, Esophagus), and "
            "bulk GTEx TPM rank is not a proxy for disease relevance.",
        ),
    ),
    "dependency_relevance": GuardRule(
        family="dependency_relevance",
        marker="DEPMAP RELEVANCE GUARD",
        flag_rule_id="depmap_relevance_guard",
        flag_message=(
            "DepMap relevance guard activated: verdict text cited cancer-cell-line DepMap "
            "essentiality as supportive mechanism evidence for a target where it carries no "
            "signal (non-oncology, no meaningful dependency); annotated rather than silently "
            "rewritten."
        ),
        check=lambda text, f: check_depmap_relevance(
            text,
            uninformative=dependency_is_uninformative(f.dependency, is_oncology=f.is_oncology),
            data_detail=_dependency_detail(f),
        ),
        is_armed=lambda f: f.dependency is not None,
        missing_inputs="no DepMap or Project Score dependency data",
        recommended_phrasings=(
            "Cancer cell-line dependency data provide no functional support either way for "
            "this non-oncology target.",
            "DepMap data do NOT support a therapeutic window.",
        ),
    ),
    "quantity": GuardRule(
        family="quantity",
        marker="QUANTITY GUARD",
        flag_rule_id="quantity_wording_guard",
        flag_message=(
            "Quantity guard activated: verdict text described a measured quantity (cell-line "
            "dependency, trials per phase) with a vague quantifier that contradicts its "
            "pre-computed band; annotated rather than silently rewritten."
        ),
        check=lambda text, f: (
            check_quantity_wording(text, f.quant_readings())
            + check_pooled_phase_wording(text, f.trial_ids_by_phase())
        ),
        is_armed=lambda f: bool(f.quant_readings()),
        missing_inputs="no banded quantities (dependency or trial counts)",
        recommended_phrasings=(
            "GENE-A is not a cell-line dependency — fitness effect in 2/512 Project Score "
            "cancer cell lines (0.4%).",
            "DRUG-A has a single Phase 3 trial.",
        ),
    ),
    "phenotype_grounding": GuardRule(
        family="phenotype_grounding",
        marker="PHENOTYPE GROUNDING GUARD",
        flag_rule_id="phenotype_grounding_guard",
        flag_message=(
            "Phenotype grounding guard activated: verdict text named a mouse knockout "
            "phenotype that is not in this gene's mouse phenotype data; annotated rather "
            "than silently rewritten."
        ),
        check=lambda text, f: check_phenotype_grounding(text, f.mouse_phenotypes or []),
        is_armed=lambda f: f.mouse_phenotypes is not None,
        missing_inputs="mouse phenotypes not retrieved",
        penalised_axes=("toxicity", "mechanism_of_action"),
        recommended_phrasings=(
            "Each knockout phenotype in the mouse data is named as a candidate on-target "
            "liability.",
            "No mouse knockout phenotype points to lethality.",
        ),
    ),
    "tissue_claim": GuardRule(
        family="tissue_claim",
        marker="TISSUE CLAIM GUARD",
        flag_rule_id="tissue_claim_guard",
        flag_message=(
            "Tissue claim guard activated: verdict text called expression specific to, "
            "enriched in or high in a tissue that ranks low in this gene's GTEx profile; "
            "annotated rather than silently rewritten."
        ),
        check=lambda text, f: check_tissue_claims(text, f.tissue_ranks, f.hpa_specificity),
        is_armed=lambda f: bool(f.tissue_ranks),
        missing_inputs="no GTEx tissue ranks",
        penalised_axes=("tissue_specificity",),
        recommended_phrasings=(
            "Expression is highest in lung and oesophageal muscle; kidney cortex ranks low "
            "in bulk data.",
        ),
    ),
    "drug_approval": GuardRule(
        family="drug_approval",
        marker="DRUG APPROVAL GUARD",
        flag_rule_id="drug_approval_guard",
        flag_message=(
            "Drug approval guard activated: verdict text called a drug approved that the "
            "target's known-drug data mark as not approved; annotated rather than silently "
            "rewritten."
        ),
        check=lambda text, f: check_drug_approval(text, f.drug_approval),
        is_armed=lambda f: bool(f.drug_approval),
        missing_inputs="no known-drug approval data",
        penalised_axes=("clinical_precedent", "approval_precedent"),
        recommended_phrasings=("The drug is in a Phase 3 trial and is not approved.",),
    ),
    "trial_readout": GuardRule(
        family="trial_readout",
        marker="TRIAL READOUT GUARD",
        flag_rule_id="trial_readout_guard",
        flag_message=(
            "Trial readout guard activated: verdict text said a still-running trial with no "
            "posted results had shown an effect; annotated rather than silently rewritten."
        ),
        check=lambda text, f: (
            check_trial_readout(
                text, [(t.nct_id, t.status, bool(t.arm_results)) for t in f.trial_facts]
            )
            + check_stopped_trial_validation(
                text, [(t.nct_id, t.status, bool(t.arm_results)) for t in f.trial_facts]
            )
        ),
        is_armed=lambda f: any(t.status for t in f.trial_facts),
        missing_inputs="no trial statuses",
        penalised_axes=("clinical_validation",),
        recommended_phrasings=("The Phase 3 trial is recruiting and has not reported results.",),
    ),
    "modality": GuardRule(
        family="modality",
        marker="MODALITY GUARD",
        flag_rule_id="modality_guard",
        flag_message=(
            "Modality guard activated: verdict text called a biologic (antibody, "
            "oligonucleotide or fusion protein, by its INN stem) a small molecule; annotated "
            "rather than silently rewritten."
        ),
        check=lambda text, f: check_modality(text),
        is_armed=lambda f: True,
        missing_inputs="",
        recommended_phrasings=(
            "Approved inhibitors include monoclonal antibodies and an siRNA; small molecules "
            "are in development.",
        ),
    ),
    "phenotype_omission": GuardRule(
        family="phenotype_omission",
        marker="PHENOTYPE OMISSION GUARD",
        flag_rule_id="phenotype_omission_guard",
        flag_message=(
            "Phenotype omission guard activated: the mouse knockout data include an organ "
            "system the safety text never names as a candidate liability; annotated rather "
            "than silently rewritten."
        ),
        check=lambda text, f: check_phenotype_omission(text, f.mouse_phenotypes or [], f.disease),
        is_armed=lambda f: bool(f.mouse_phenotypes),
        missing_inputs="mouse phenotypes not retrieved",
        penalised_axes=("toxicity",),
        recommended_phrasings=(
            "Knockout mice show changes in vascular tone and blood pressure, and each of "
            "these is named here as a candidate on-target liability of chronic inhibition. "
            "The evidence for these effects comes from the mouse phenotype data retrieved "
            "for this gene, which are reported in full in the structured evidence. Human "
            "population data and the clinical safety record, where available, are weighed "
            "against them, and the lens states which of the effects would need monitoring "
            "in clinical development, how they would be measured, and whether they are "
            "expected to be reversible on stopping treatment. No other organ system is "
            "implicated by the knockout data retrieved in this run.",
        ),
    ),
    "cell_type_claim": GuardRule(
        family="cell_type_claim",
        marker="CELL TYPE CLAIM GUARD",
        flag_rule_id="cell_type_claim_guard",
        flag_message=(
            "Cell type claim guard activated: verdict text attributed expression to a "
            "disease cell type that no retrieved claim reports; annotated rather than "
            "silently rewritten."
        ),
        check=lambda text, f: check_cell_type_claims(
            text, f.cell_types, f.cell_types_reported or frozenset()
        ),
        is_armed=lambda f: bool(f.cell_types) and f.cell_types_reported is not None,
        missing_inputs="no disease cell types or no claims",
        penalised_axes=("mechanism_of_action", "tissue_specificity"),
        recommended_phrasings=(
            "Bulk data cannot resolve expression in the disease's cell type; no retrieved "
            "claim reports it.",
        ),
    ),
    "trial_attribution": GuardRule(
        family="trial_attribution",
        marker=ATTRIBUTION_MARKER,
        flag_rule_id="trial_attribution_guard",
        flag_message=(
            "Trial attribution guard activated: verdict text stated a result figure for a "
            "trial that neither its registry record nor any retrieved publication naming it "
            "reports (a figure from another study or endpoint attached to the trial); "
            "annotated rather than silently rewritten."
        ),
        check=lambda text, f: check_trial_attribution(text, f.trial_values or {}),
        is_armed=lambda f: bool(f.trial_values) and any((f.trial_values or {}).values()),
        missing_inputs="no reported numbers for any retrieved trial",
        penalised_axes=("clinical_validation",),
        recommended_phrasings=(
            "In NCT00000001 the DRUG-A 20 mg arm reached p=0.0024 on the primary outcome.",
            "The outcome trial's own registry record gives the hazard ratio; figures from "
            "other studies are attributed to those studies.",
        ),
    ),
}


def run_family(family: GuardFamily, text: str, facts: GuardFacts) -> list[Violation]:
    """Run one guard family over ``text`` (empty when the family is not armed)."""
    rule = GUARD_RULES[family]
    if not text or not rule.is_armed(facts):
        return []
    return rule.check(text, facts)
