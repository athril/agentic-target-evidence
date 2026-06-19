# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Knowledge graph query stubs.

Neighborhood traversal and subgraph queries consumed by lenses and the report agent.
"""

from __future__ import annotations

from schemas.knowledge_graph import GraphEdge, GraphNode


async def get_neighborhood(node_id: str, max_hops: int = 2) -> list[GraphNode]:
    """Return nodes within max_hops of node_id."""
    raise NotImplementedError("get_neighborhood not yet implemented")


async def get_edges(subject_id: str, object_id: str | None = None) -> list[GraphEdge]:
    """Return edges from subject_id, optionally filtered to a specific object."""
    raise NotImplementedError("get_edges not yet implemented")
