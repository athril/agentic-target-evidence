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
    # 0.0–1.0, computed from the cited claims' evidence weights (_lens_confidence.py)
    confidence: float = 0.0
    model_confidence: float | None = None  # the LLM's self-reported value, kept for audit
    evidence_sources: int = 0  # independent sources behind `confidence`
    rationale: str = ""  # 1–3 sentence explanation
    supporting_claim_ids: list[str] = []  # str-serialised UUIDs from CoreClaim.evidence_id
    # The subset of supporting_claim_ids added after the verdict — structured records
    # by config (axis_evidence.yaml) and literature by the lens citation step — as
    # opposed to cited by the lens LLM itself.
    added_claim_ids: list[str] = []
    # True when the lens citation step reviewed this axis's literature; its picks
    # (literature_picks) are then the only literature the axis's confidence counts.
    literature_reviewed: bool = False
    literature_picks: list[str] = []


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


class GuardCheck(BaseModel):
    """Outcome of one deterministic guard family on a lens verdict.

    Recorded for every family a lens runs — including the ones that could not run —
    so a missing input (e.g. DepMap unavailable) is visible in the report instead of
    the check silently passing.
    """

    model_config = ConfigDict(frozen=True)

    family: str  # guard family, e.g. "constraint", "dependency_relevance"
    status: Literal["passed", "flagged", "not_evaluated"]
    detail: str = ""  # fired rule ids, or why the family could not be evaluated


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
    confidence: float = 0.0  # 0.0–1.0 overall lens confidence, computed from the axes
    model_confidence: float | None = None  # the LLM's self-reported value, kept for audit
    axes: list[AxisVerdict] = []
    rationale: str = ""  # 1–3 sentence summary for the dossier
    narrative: str = ""  # 2–4 paragraph prose analysis for the report discovery section
    validation_flags: list[ValidationFlag] = []  # deterministic reasoning-check flags (1.3+)
    guard_checks: list[GuardCheck] = []  # per-family guard outcomes, incl. not-evaluated
    # Deterministic direction-of-effect summary this lens reasoned with, for the report
    # (services/evidence/direction_of_effect); empty when the lens had none.
    direction_of_effect: str = ""
    # What a split lens asked and was told, one entry per question (id, facts, answer, shown
    # and dropped claim ids, attempts, corrections); empty for a single-call lens, so
    # verdicts cached before the split still load.
    question_results: list[dict[str, Any]] = []
    # How many claims of each evidence kind (config/lens_inputs.yaml) the lens was shown,
    # so a run's record says whether the decisive kind of evidence reached the lens.
    shown_inputs: dict[str, int] = {}

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
