# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ReviewerAgent.

Generates per-stage gap reports for each pipeline stage (literature, genetics,
clinical, screening, extraction, lenses, experiment). Each report:
{stage, missing_aspects, completeness_score}.

Deterministic: the gaps are computed from ``stage_facts`` (retrieved/kept counts,
trials with posted results, lens outcomes and open checks) assembled by the workflow
node. An LLM given only item counts invented gaps the data contradicted ("No clinical
trials" with four trials retrieved), so no model is involved.

Does NOT assess source quality — that is the Critic's domain.
"""

from __future__ import annotations

import uuid
from typing import Any

from agents.challenge.reviewer.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage

_STAGES = [
    "sources",
    "literature",
    "genetics",
    "clinical",
    "screening",
    "extraction",
    "lenses",
    "experiment",
]
_EXPECTED_SOURCES = 9  # acquisition nodes (workflow._ACQUISITION_NODE_NAMES)

# Fewer kept literature sources than this is reported as sparse coverage.
_SPARSE_LITERATURE = 5


def _gap(stage: str, missing: list[str], score: int) -> dict[str, Any]:
    return {"stage": stage, "missing_aspects": missing, "completeness_score": score}


def _kept_gap(stage: str, what: str, retrieved: int, kept: int) -> dict[str, Any]:
    if retrieved == 0:
        return _gap(stage, [f"No {what} retrieved"], 0)
    if kept == 0:
        return _gap(stage, [f"None of the {retrieved} retrieved {what} passed screening"], 0)
    return _gap(stage, [], 100)


def review_gaps(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-stage gap reports from the run's structured facts (see module docstring)."""
    gaps: list[dict[str, Any]] = []

    # A data source that failed leaves a hole every downstream stage inherits; it must
    # never read as "complete" (a run that lost its whole literature node did).
    failed = list(facts.get("failed_sources") or [])
    gaps.append(
        _gap(
            "sources",
            [f"{s} retrieval failed — its evidence is absent from this report" for s in failed],
            max(0, round(100 * (1 - len(failed) / _EXPECTED_SOURCES))),
        )
    )

    lit = facts.get("literature") or {}
    lit_gap = _kept_gap("literature", "literature", lit.get("retrieved", 0), lit.get("kept", 0))
    kept_lit = lit.get("kept", 0)
    if 0 < kept_lit < _SPARSE_LITERATURE:
        lit_gap = _gap("literature", [f"Only {kept_lit} literature source(s) passed screening"], 50)
    gaps.append(lit_gap)

    gen = facts.get("genetics") or {}
    gaps.append(
        _kept_gap(
            "genetics", "genetics or omics evidence", gen.get("retrieved", 0), gen.get("kept", 0)
        )
    )

    clin = facts.get("clinical") or {}
    clin_gap = _kept_gap(
        "clinical", "registry trials", clin.get("retrieved", 0), clin.get("kept", 0)
    )
    if clin.get("kept", 0) and not clin.get("with_results", 0):
        clin_gap = _gap("clinical", ["No kept trial has posted results"], 50)
    approved = list(facts.get("approved_drugs") or [])
    if approved and not facts.get("late_phase_trials_kept", 0):
        clin_gap = _gap(
            "clinical",
            [
                *clin_gap["missing_aspects"],
                f"Approved drug(s) on the target ({', '.join(approved[:4])}) but no "
                "phase 3/4 trial of them was kept — the outcome trials behind the "
                "approvals are missing",
            ],
            min(clin_gap["completeness_score"], 50),
        )
    gaps.append(clin_gap)

    total = facts.get("screened", 0)
    gaps.append(
        _gap("screening", [] if total else ["No evidence reached screening"], 100 if total else 0)
    )

    claims = facts.get("claims", 0)
    gaps.append(
        _gap("extraction", [] if claims else ["No claims were extracted"], 100 if claims else 0)
    )

    lenses: dict[str, dict[str, Any]] = facts.get("lenses") or {}
    expected = facts.get("expected_lenses") or list(lenses)
    missing: list[str] = []
    usable = 0
    for name in expected:
        lens = lenses.get(name)
        if lens is None:
            missing.append(f"{name} lens failed to produce a verdict")
            continue
        if lens.get("verdict") == "insufficient_evidence":
            missing.append(f"{name} lens: insufficient evidence")
        else:
            usable += 1
        if lens.get("open_checks"):
            missing.append(f"{name} lens: open check(s) {', '.join(lens['open_checks'])}")
    lens_score = round(100 * usable / len(expected)) if expected else 0
    gaps.append(_gap("lenses", missing, lens_score))

    experiments = facts.get("experiments", 0)
    gaps.append(
        _gap(
            "experiment",
            [] if experiments else ["No experiments were scored"],
            100 if experiments else 0,
        )
    )
    return gaps


class ReviewerAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"review_gaps": review_gaps(spec.get("stage_facts") or {})},
            trace_id=msg.trace_id,
        )
