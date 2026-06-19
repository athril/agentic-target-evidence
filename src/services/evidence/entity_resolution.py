# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Entity resolution service — deterministic normalization.

Normalises gene / disease / tissue strings within a CoreClaim to canonical IDs.
This propagates gene_id and disease_id from a reference Evidence when
the claim lacks them; deep NER is deferred until omics data lands.
"""

from __future__ import annotations

from schemas.evidence import CoreClaim


def resolve_entities(
    claims: list[CoreClaim],
    *,
    canonical_gene_id: str = "",
    canonical_disease_id: str = "",
) -> list[CoreClaim]:
    """Copy canonical IDs from the run context into claims that lack them.

    Any claim already carrying non-empty gene_id/disease_id is left unchanged.
    Deep NER (resolving free-text tissue / population mentions) is deferred.
    """
    result: list[CoreClaim] = []
    for claim in claims:
        gene_id = claim.gene_id or canonical_gene_id
        disease_id = claim.disease_id or canonical_disease_id
        if gene_id == claim.gene_id and disease_id == claim.disease_id:
            result.append(claim)
        else:
            result.append(claim.model_copy(update={"gene_id": gene_id, "disease_id": disease_id}))
    return result
