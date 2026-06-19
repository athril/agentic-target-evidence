# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claim clustering service — deterministic grouping.

Groups CoreClaims by (gene_id, disease_id, evidence_type) key to form evidence
clusters that lenses can reason over. Semantic deduplication (embedding-based)
is deferred to when the bench/eval workstream lands.
"""

from __future__ import annotations

from collections import defaultdict

from schemas.evidence import CoreClaim

ClusterKey = tuple[str, str, str]  # (gene_id, disease_id, evidence_type)
ClaimCluster = dict[ClusterKey, list[CoreClaim]]


def cluster_claims(claims: list[CoreClaim]) -> ClaimCluster:
    """Partition claims by (gene_id, disease_id, evidence_type).

    Returns a dict keyed by the 3-tuple so that each lens can fetch exactly
    the evidence category it owns.
    """
    clusters: dict[ClusterKey, list[CoreClaim]] = defaultdict(list)
    for claim in claims:
        key: ClusterKey = (
            claim.gene_id or claim.gene,
            claim.disease_id or claim.disease,
            claim.evidence_type.value,
        )
        clusters[key].append(claim)
    return dict(clusters)
