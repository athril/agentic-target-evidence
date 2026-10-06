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
    TrialFact,
    check_clinical_phase_guard,
    trial_phase_counts,
)
from services.evidence.commercial_interpret import check_commercial_guards
from services.evidence.constraint_interpret import (
    MENDELIAN_GWAS_ABSENCE_PHRASE,
    ConstraintReading,
    check_constraint_guards,
    check_depmap_relevance,
    check_mendelian_floor,
)
from services.evidence.disease_tissue import check_tissue_relevance
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
    "tissue_relevance",
    "dependency_relevance",
    "quantity",
]


class GuardFacts(BaseModel):
    """The deterministic facts lens prose is checked against, for one gene × disease run."""

    model_config = ConfigDict(frozen=True)

    gene: str = ""
    disease: str = ""
    constraint: ConstraintReading | None = None
    mendelian_grade: bool = False
    trial_facts: list[TrialFact] = []
    known_drugs_count: int = 0
    approved_count: int = 0
    indication_approved_drug_count: int = 0
    indication_active_trial_count: int = 0
    top_tissues: list[str] = []
    disease_relevant_tissues: list[str] = []
    dependency: DependencyReading | None = None
    is_oncology: bool = False

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
            "TRPC6 is relatively LoF-tolerant (NOT haploinsufficient).",
            "LOEUF=0.759 indicates the gene is relatively LoF-tolerant (NOT haploinsufficient).",
            "TRPC6 is LoF-tolerant with no meaningful missense constraint.",
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
            "causality negative for a Mendelian-grade gene-disease pair; annotated rather "
            "than silently rewritten."
        ),
        check=lambda text, f: check_mendelian_floor(text) if f.mendelian_grade else [],
        is_armed=lambda f: f.mendelian_grade,
        missing_inputs="gene-disease pair is not Mendelian-grade (rule does not apply)",
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
        ),
        is_armed=lambda f: True,
        missing_inputs="",
        recommended_phrasings=(
            "There is no approved TRPC6-targeted therapy for FSGS.",
            "No approved or clinical-stage therapy targeting TRPC6 was found in the retrieved "
            "evidence.",
            "The competitive field is underserved at the target level, but the indication is "
            "contested by other mechanisms.",
            "TRPC6-targeted therapies remain underserved; the indication itself is crowded.",
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
            "BI 764198 is in one Phase 3 trial (NCT07220083, recruiting) after a completed "
            "Phase 2 trial (NCT05213624).",
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
            "TRPC6 is not a cell-line dependency — fitness effect in 2/512 Project Score "
            "cancer cell lines (0.4%).",
            "BI 764198 has a single Phase 3 trial.",
        ),
    ),
}


def run_family(family: GuardFamily, text: str, facts: GuardFacts) -> list[Violation]:
    """Run one guard family over ``text`` (empty when the family is not armed)."""
    rule = GUARD_RULES[family]
    if not text or not rule.is_armed(facts):
        return []
    return rule.check(text, facts)
