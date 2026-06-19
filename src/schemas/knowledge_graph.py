# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Canonical biomedical knowledge graph schema.

Nodes and edges here are the single source of truth for the KG layer.
Distinct from orchestration graphs (capabilities/*/workflow.py) — this
models gene/disease/pathway/drug relationships, not agent pipelines.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from schemas.evidence import Provenance

schema_version: str = "1.0"


class NodeType(StrEnum):
    GENE = "GENE"
    DISEASE = "DISEASE"
    VARIANT = "VARIANT"
    PATHWAY = "PATHWAY"
    DRUG = "DRUG"
    CELL_TYPE = "CELL_TYPE"
    BIOMARKER = "BIOMARKER"
    PHENOTYPE = "PHENOTYPE"
    PUBLICATION = "PUBLICATION"


class EdgeType(StrEnum):
    CAUSES = "CAUSES"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    EXPRESSED_IN = "EXPRESSED_IN"
    ACTIVATES = "ACTIVATES"
    INHIBITS = "INHIBITS"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"


class GraphNode(BaseModel):
    """A typed node in the biomedical knowledge graph."""

    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(..., description="Canonical identifier, e.g. ENSG00000139618, MONDO:0007254")
    node_type: NodeType
    label: str = Field(..., description="Human-readable canonical name")
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Additional namespace → id mappings, e.g. {'hgnc': '1100', 'omim': '600185'}",
    )


class GraphEdge(BaseModel):
    """A typed, directed, provenance-carrying edge in the biomedical knowledge graph."""

    schema_version: Literal["1.0"] = "1.0"
    subject_id: str = Field(..., description="Source node id")
    predicate: EdgeType
    object_id: str = Field(..., description="Target node id")
    direction: Literal["positive", "negative", "neutral", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: Provenance
