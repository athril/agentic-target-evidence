# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Evidence graph builder — deterministic adjacency structure.

Builds a lightweight in-memory evidence graph from a ClaimCluster. Each node
is a (gene_id, evidence_type) pair; edges connect nodes whose claims share an
overlapping direction. A graph DB (deferred for now) is only added if
conflict detection demands it (Phase 6 decision).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schemas.evidence import Direction
from services.evidence.claim_clustering import ClaimCluster


@dataclass
class EvidenceNode:
    gene_id: str
    disease_id: str
    evidence_type: str
    claim_count: int
    directions: set[str] = field(default_factory=set)
    mean_confidence: float | None = None


@dataclass
class EvidenceGraph:
    nodes: list[EvidenceNode] = field(default_factory=list)
    # adjacency: node index → list of related node indices (shared direction)
    adjacency: dict[int, list[int]] = field(default_factory=dict)

    def conflicts(self) -> list[tuple[int, int]]:
        """Return pairs of node indices whose direction sets are contradictory.

        Contradiction: one node has only INHIBIT and another has only ACTIVATE.
        """
        pairs = []
        for i, a in enumerate(self.nodes):
            for j, b in enumerate(self.nodes[i + 1 :], start=i + 1):
                a_dirs = a.directions - {Direction.UNSPECIFIED.value}
                b_dirs = b.directions - {Direction.UNSPECIFIED.value}
                if (
                    a_dirs
                    and b_dirs
                    and a_dirs.isdisjoint(b_dirs)
                    and (
                        (Direction.INHIBIT.value in a_dirs and Direction.ACTIVATE.value in b_dirs)
                        or (
                            Direction.ACTIVATE.value in a_dirs and Direction.INHIBIT.value in b_dirs
                        )
                    )
                ):
                    pairs.append((i, j))
        return pairs


def build_evidence_graph(clusters: ClaimCluster) -> EvidenceGraph:
    """Build an EvidenceGraph from a pre-clustered set of claims."""
    nodes: list[EvidenceNode] = []
    for (gene_id, disease_id, et), claims in clusters.items():
        confidences = [c.confidence for c in claims if c.confidence is not None]
        mean_conf = sum(confidences) / len(confidences) if confidences else None
        node = EvidenceNode(
            gene_id=gene_id,
            disease_id=disease_id,
            evidence_type=et,
            claim_count=len(claims),
            directions={c.direction.value for c in claims},
            mean_confidence=round(mean_conf, 4) if mean_conf is not None else None,
        )
        nodes.append(node)

    # Build adjacency: nodes with overlapping (non-unspecified) direction share an edge.
    adjacency: dict[int, list[int]] = {i: [] for i in range(len(nodes))}
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes[i + 1 :], start=i + 1):
            shared = (a.directions & b.directions) - {Direction.UNSPECIFIED.value}
            if shared:
                adjacency[i].append(j)
                adjacency[j].append(i)

    return EvidenceGraph(nodes=nodes, adjacency=adjacency)
