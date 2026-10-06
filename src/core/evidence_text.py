# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helper for building LLM-facing text from Evidence objects.

Clinical trials are special: the gene/target name often appears only in
eligibility_criteria, not in brief_summary. This module builds a
target-first concatenation for CLINICAL_TRIAL evidence so screeners
and claim-extractors can see the gene reference even when it is absent
from the summary field.
"""

from __future__ import annotations

import re

from schemas.evidence import Evidence, EvidenceType

# Eligibility criteria are long; the screener sees the first _ELIGIBILITY_CHARS, plus a window
# around the first target-gene mention when that falls later (e.g. "...gain-of-function
# mutation in the TRPC6 gene" as inclusion criterion 4, ~600 chars in).
_ELIGIBILITY_CHARS = 600
_MENTION_WINDOW = 200

# Drug-development phases as ClinicalTrials.gov reports them (``EARLY_PHASE1`` … ``PHASE4``,
# possibly combined like ``PHASE2/PHASE3``). Observational studies have none; device and
# behavioural trials report "NA".
_DRUG_PHASE_RE = re.compile(r"PHASE[1-4]")


def mentions_term(text: str, term: str) -> bool:
    """Case-insensitive whole-token match: "ANTI-PCSK9" mentions PCSK9, "PCSK90" does not."""
    term = term.strip()
    if not term or not text:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _eligibility(ev: Evidence) -> str:
    pc = ev.extra.get("participation_criteria") or {}
    return str((pc.get("eligibility_criteria") if isinstance(pc, dict) else None) or "")


def _trial_record_text(ev: Evidence) -> str:
    """Every free-text field of a trial record, untruncated — for deterministic gene matching."""
    fields = [
        ev.extra.get("title") or "",
        " ".join(str(i) for i in ev.extra.get("interventions") or []),
        " ".join(str(c) for c in ev.extra.get("conditions") or []),
        _eligibility(ev),
        ev.extra.get("brief_summary") or "",
        str(ev.extra.get("design_details") or ""),
    ]
    return "\n".join(fields)


def is_target_drug_trial(ev: Evidence) -> bool:
    """A phased (drug-development) clinical trial whose record names the target gene.

    Such a trial tests a modality aimed at the target in humans — the strongest clinical
    evidence the pipeline can retrieve — so it is kept deterministically rather than left to
    LLM screening, which can miss a drug-code-only trial (e.g. "BI 764198") whose gene mention
    sits deep in the eligibility criteria.
    """
    if ev.evidence_type != EvidenceType.CLINICAL_TRIAL or not ev.gene:
        return False
    if not _DRUG_PHASE_RE.search(str(ev.extra.get("phase") or "").upper()):
        return False
    return mentions_term(_trial_record_text(ev), ev.gene)


def screenable_text(ev: Evidence) -> str:
    """Return the best available LLM-facing text for an Evidence item.

    For CLINICAL_TRIAL: target-first ordering (interventions → conditions →
    eligibility snippet → brief_summary → design_details) so gene mentions
    buried in eligibility criteria reach the model.

    For all other types: standard fallback chain identical to the previous
    _screenable_text helper in the screening agent.
    """
    if ev.evidence_type == EvidenceType.CLINICAL_TRIAL:
        return _clinical_text(ev)
    return (
        ev.extra.get("abstract")
        or ev.extra.get("brief_summary")
        or ev.extra.get("assoc_text")
        or ev.extra.get("tract_text")
        or ev.extra.get("description")
        or ""
    )


def _clinical_text(ev: Evidence) -> str:
    parts: list[str] = []

    interventions = ev.extra.get("interventions") or []
    if interventions:
        parts.append("Interventions: " + ", ".join(str(i) for i in interventions))

    conditions = ev.extra.get("conditions") or []
    if conditions:
        parts.append("Conditions: " + ", ".join(str(c) for c in conditions))

    # participation_criteria may be a nested dict (as stored by fetch_trials)
    eligibility = _eligibility(ev)
    if eligibility:
        snippet = eligibility[:_ELIGIBILITY_CHARS]
        late = _late_gene_mention(eligibility, ev.gene)
        if late:
            snippet += " … " + late
        parts.append("Eligibility: " + snippet)

    brief = ev.extra.get("brief_summary") or ""
    if brief:
        parts.append(brief)

    design = ev.extra.get("design_details") or ""
    if design:
        parts.append(str(design))

    return "\n\n".join(parts)


def _late_gene_mention(eligibility: str, gene: str) -> str:
    """Window around the first gene mention past the eligibility cut, or "" if none."""
    if not gene or mentions_term(eligibility[:_ELIGIBILITY_CHARS], gene):
        return ""
    pattern = r"(?<![A-Za-z0-9])" + re.escape(gene) + r"(?![A-Za-z0-9])"
    m = re.search(pattern, eligibility, re.IGNORECASE)
    if not m:
        return ""
    start = max(_ELIGIBILITY_CHARS, m.start() - _MENTION_WINDOW)
    return eligibility[start : m.end() + _MENTION_WINDOW]
