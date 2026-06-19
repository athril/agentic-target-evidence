# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Quality scoring service — heuristic per-claim scoring.

Assigns a quality score to each CoreClaim based on:
  - source tier (peer-reviewed > preprint > patent > clinical > functional)
  - temporal recency (< 3 yrs = high, 3-7 yrs = medium, > 7 yrs = low)
  - confidence (from extraction, if present)

LLM-based quality assessment (SJR tier lookup) stays with CriticAgent for now
and will be consolidated here when critic verdict-QA lands.
"""

from __future__ import annotations

from datetime import date

from schemas.evidence import CoreClaim, EvidenceType

# Source tier scores (heuristic; tuned once bench/eval lands)
_TIER: dict[EvidenceType, float] = {
    EvidenceType.ARTICLE: 1.0,
    EvidenceType.BOOK: 0.9,
    EvidenceType.CONFERENCE: 0.8,
    EvidenceType.GENETICS: 0.85,
    EvidenceType.FUNCTIONAL_GENOMICS: 0.8,
    EvidenceType.EXPRESSION: 0.75,
    EvidenceType.OMICS: 0.75,
    EvidenceType.CLINICAL_TRIAL: 0.7,
    EvidenceType.ABSTRACT: 0.6,
    EvidenceType.PATENT: 0.5,
    EvidenceType.CONSTRAINT: 0.85,
    EvidenceType.DRUGGABILITY: 0.8,
    EvidenceType.REGULATORY_ELEMENT: 0.75,
}

_TODAY = date.today


def _recency_score(avail: date | None) -> float:
    if avail is None:
        return 0.5  # unknown date → neutral
    age_years = (date.today() - avail).days / 365.25
    if age_years < 3:
        return 1.0
    if age_years < 7:
        return 0.7
    return 0.4


def score_quality(claim: CoreClaim) -> CoreClaim:
    """Return a new claim with `confidence` updated to a composite quality score.

    If the claim already carries extractor confidence, that is blended 50/50
    with the heuristic score. Otherwise the heuristic score stands alone.
    """
    tier = _TIER.get(claim.evidence_type, 0.5)
    recency = _recency_score(claim.availability_date)
    heuristic = (tier * 0.6) + (recency * 0.4)

    composite = (heuristic + claim.confidence) / 2 if claim.confidence is not None else heuristic

    return claim.model_copy(update={"confidence": round(composite, 4)})


def score_quality_batch(claims: list[CoreClaim]) -> list[CoreClaim]:
    """Apply quality scoring to a list of claims."""
    return [score_quality(c) for c in claims]
