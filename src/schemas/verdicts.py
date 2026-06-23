# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens verdict schemas — claim-centric interpretation layer.

Each interpretation lens produces one LensVerdict per run. Verdict-QA
reads the set of LensVerdicts and feeds the reconciler.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .evidence import Direction


class AxisVerdict(BaseModel):
    """Per-axis verdict within a single lens."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: str  # e.g. "causality", "druggability", "toxicity"
    verdict: bool | None = None  # True=favourable, False=unfavourable, None=uncertain
    confidence: float = 0.0  # 0.0–1.0
    rationale: str = ""  # 1–3 sentence explanation
    supporting_claim_ids: list[str] = []  # str-serialised UUIDs from CoreClaim.evidence_id


class ValidationFlag(BaseModel):
    """Deterministic reasoning-check flag produced by a lens validator.

    Emitted when the LLM narrative contradicts pre-computed deterministic readings.
    High-severity flags are routed to the HITL gate before report synthesis.
    """

    model_config = ConfigDict(frozen=True)

    lens: str  # which lens produced this flag
    severity: Literal["high", "medium", "low"]  # high → HITL interrupt required
    rule_id: str  # stable identifier for the violated rule
    claim_excerpt: str = ""  # relevant excerpt from the narrative/rationale
    message: str  # human-readable explanation of the violation


class LensVerdict(BaseModel):
    """Interpretation verdict produced by one lens agent.

    Six lenses (genetics, biology, safety, clinical, commercial, regulatory) each
    emit one LensVerdict per run. Verdict-QA and the reconciler consume
    the full set to detect overweight axes and cross-lens conflicts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    trace_id: str
    lens: Literal["genetics", "biology", "safety", "clinical", "commercial", "regulatory"]
    target_gene: str
    disease: str
    direction: Direction = Direction.UNSPECIFIED
    overall_verdict: Literal["support", "oppose", "neutral", "insufficient_evidence"] = (
        "insufficient_evidence"
    )
    confidence: float = 0.0  # 0.0–1.0 overall lens confidence
    axes: list[AxisVerdict] = []
    rationale: str = ""  # 1–3 sentence summary for the dossier
    narrative: str = ""  # 2–4 paragraph prose analysis for the report discovery section
    validation_flags: list[ValidationFlag] = []  # deterministic reasoning-check flags (1.3+)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LensVerdict:
        return cls.model_validate(data)


class AgreementMap(BaseModel):
    """Cross-lens consensus and conflict map produced by the reconciler.

    Summarises whether the six lens verdicts agree, which lenses dissent, and
    which claim IDs are cited by both support and oppose AxisVerdicts (shared-
    claim conflicts requiring human attention in the dossier).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    verdicts_by_lens: dict[str, str] = {}  # lens → overall_verdict
    consensus_verdict: Literal["support", "oppose", "neutral", "insufficient_evidence"] = (
        "insufficient_evidence"
    )
    consensus_confidence: float = 0.0  # mean confidence of agreeing lenses
    agreeing_lenses: list[str] = []  # lens names matching consensus
    dissenting_lenses: list[str] = []  # lens names not matching consensus
    conflicts: list[dict[str, Any]] = []  # [{lens_a, lens_b, description}]
    shared_claim_conflicts: list[str] = []  # claim IDs cited in both support + oppose

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgreementMap:
        return cls.model_validate(data)
