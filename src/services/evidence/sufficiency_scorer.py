# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sufficiency scoring service — aggregate evidence coverage check.

Determines whether each evidence category has enough high-quality claims for a
lens to render a meaningful verdict. The thresholds here are provisional; they
will be fit to the known-outcome benchmark in the bench/eval workstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schemas.evidence import CoreClaim, EvidenceType

# Minimum number of quality-scored claims (confidence >= threshold) per category
# for that category to be considered "sufficient" for lens reasoning.
_MIN_CLAIMS: dict[str, int] = {
    EvidenceType.GENETICS.value: 1,
    EvidenceType.FUNCTIONAL_GENOMICS.value: 1,
    EvidenceType.CLINICAL_TRIAL.value: 1,
    EvidenceType.ARTICLE.value: 2,
    EvidenceType.PATENT.value: 1,
    EvidenceType.DRUGGABILITY.value: 1,
}
_QUALITY_THRESHOLD = 0.4  # claims below this confidence are excluded


@dataclass
class SufficiencyReport:
    """Aggregate evidence coverage across categories."""

    category_counts: dict[str, int] = field(default_factory=dict)
    sufficient_categories: list[str] = field(default_factory=list)
    insufficient_categories: list[str] = field(default_factory=list)
    overall_sufficient: bool = False


def score_sufficiency(claims: list[CoreClaim]) -> SufficiencyReport:
    """Compute evidence sufficiency across all evidence categories.

    Claims with confidence < _QUALITY_THRESHOLD (or no score) are excluded.
    A category is sufficient if it meets the _MIN_CLAIMS threshold.
    """
    by_type: dict[str, list[CoreClaim]] = {}
    for claim in claims:
        et = claim.evidence_type.value
        if claim.confidence is None or claim.confidence >= _QUALITY_THRESHOLD:
            by_type.setdefault(et, []).append(claim)

    category_counts = {et: len(cls) for et, cls in by_type.items()}
    sufficient: list[str] = []
    insufficient: list[str] = []
    for et, min_n in _MIN_CLAIMS.items():
        count = category_counts.get(et, 0)
        if count >= min_n:
            sufficient.append(et)
        else:
            insufficient.append(et)

    return SufficiencyReport(
        category_counts=category_counts,
        sufficient_categories=sufficient,
        insufficient_categories=insufficient,
        overall_sufficient=len(insufficient) == 0,
    )
