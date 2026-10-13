# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalLensAgent — clinical precedent + clinical validation axes."""

from __future__ import annotations

import re
from collections.abc import Callable

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    build_guard_facts,
    run_lens,
)
from agents.interpretation.clinical_lens.contract import CONTRACT
from core.evidence_text import mentions_term
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import CoreClaim, EvidenceType
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.clinical_trial_interpret import (
    TrialFact,
    key_trials_prompt,
    outcome_terms_in,
    target_drugs,
    trial_count_prompt,
    trial_outcome_prompt,
)
from services.evidence.disease_class_rules import build_disease_class_note

_LITERATURE_TYPES = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)
# What a paper about a registry trial's drug reports, per axis.
_AXIS_TERMS = {
    "clinical_validation": re.compile(
        r"\b(?:respon\w*|reduc\w*|efficac\w*|improv\w*|benefit\w*|endpoint\w*|outcome\w*|"
        r"remission\w*|lower\w*|decreas\w*)",
        re.IGNORECASE,
    ),
    "clinical_precedent": re.compile(
        r"\b(?:phase|trials?|tolerat\w*|pharmacokinetic\w*|first[- ]in[- ]human|"
        r"healthy\s+volunteers?)\b",
        re.IGNORECASE,
    ),
}


def _trial_literature(
    facts: list[TrialFact],
) -> Callable[[LensVerdict, list[CoreClaim]], LensVerdict]:
    """Pre-scoring rule: literature reporting on a retrieved trial's drug supports the
    favourable clinical axes by rule, not by the citation model's pick (TRPC6: the phase 2
    BI 764198 paper lost to a review and a mouse study)."""
    drugs = target_drugs(facts)

    def attach(verdict: LensVerdict, claims: list[CoreClaim]) -> LensVerdict:
        if not drugs:
            return verdict
        papers = [
            c
            for c in claims
            if c.evidence_type in _LITERATURE_TYPES
            and any(mentions_term(c.claim_text, d) for d in drugs)
        ]
        axes: list[AxisVerdict] = []
        for ax in verdict.axes:
            terms = _AXIS_TERMS.get(ax.axis)
            ids = [
                str(c.evidence_id)
                for c in papers
                if terms is not None and ax.verdict is True and terms.search(c.claim_text)
            ]
            new = [i for i in ids if i not in ax.supporting_claim_ids]
            if ids:
                ax = ax.model_copy(
                    update={
                        "supporting_claim_ids": [*ax.supporting_claim_ids, *new],
                        "added_claim_ids": [*ax.added_claim_ids, *new],
                        "literature_picks": [*ax.literature_picks, *ids],
                    }
                )
            axes.append(ax)
        return verdict.model_copy(update={"axes": axes})

    return attach


class ClinicalLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        parts: list[str] = []

        disease_class_note = build_disease_class_note(spec.get("disease_classes") or (), "clinical")
        if disease_class_note:
            parts.append(disease_class_note)

        # Surface published trial-result literature that matched a registry ID
        # (derived summary injected by the workflow node — does not reclassify evidence type)
        published_trial_results = spec.get("published_trial_results") or ""
        if published_trial_results:
            parts.append(
                f"Published trial results (derived from literature evidence matching a "
                f"registry ID or 'phase N trial'):\n{published_trial_results}"
            )

        # Exact per-phase registry counts, so the prose quotes numbers instead of
        # "multiple"/"several" — the same facts the quantity guard checks afterwards.
        guard_facts = build_guard_facts(spec)
        # Outcome terms the data report, so prose naming any other outcome is flagged.
        claim_texts = [str(c.get("claim_text") or "") for c in spec.get("extracted_claims") or []]
        guard_facts = guard_facts.model_copy(
            update={"reported_outcomes": outcome_terms_in(claim_texts)}
        )
        trial_counts = trial_count_prompt(guard_facts.trial_facts)
        if trial_counts:
            parts.append(trial_counts)
        key_trials = key_trials_prompt(guard_facts.trial_facts)
        if key_trials:
            parts.append(key_trials)
        # Which arms reached significance on each posted primary outcome — the facts
        # the trial-outcome guard checks afterwards.
        trial_outcomes = trial_outcome_prompt(guard_facts.trial_facts)
        if trial_outcomes:
            parts.append(trial_outcomes)

        extra = "\n".join(parts) + "\n" if parts else ""
        return await run_lens(
            msg,
            ctx,
            lens="clinical",
            evidence_types=LENS_EVIDENCE_TYPES["clinical"],
            skill_name="clinical_lens",
            extra_context=extra,
            # Post-LLM safety net (repaired once, then annotated): text that misstates a
            # trial's phase or recruitment status — e.g. conflating two distinct-phase
            # trials into "two Phase 3 trials" — or calls a single-trial phase
            # "multiple"/"several" trials — or misstates which arms reached significance.
            guards=(
                guard_facts,
                (
                    "clinical_trial",
                    "trial_outcome",
                    "outcome_terms",
                    "quantity",
                    "trial_readout",
                    "drug_approval",
                    "modality",
                    "trial_attribution",
                ),
            ),
            adjust_verdict=_trial_literature(guard_facts.trial_facts),
        )
