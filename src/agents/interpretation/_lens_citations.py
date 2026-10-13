# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens citation step: the literature behind each axis verdict.

A small lens model writes its verdict and cites two or three claim ids, so an axis
backed by dozens of strong papers scores as if it had two. Structured records are
attached by config (axis_evidence.yaml); literature needs a judgment, made here as a
narrow task a small model does well: for each axis with a verdict, the literature
claims most similar to the axis (embedding cosine, top CANDIDATES) are shown and the
model picks at most MAX_PICKS that directly support the verdict.

Reproducibility: the lens model's wording varies between runs even at temperature 0,
while its categorical verdicts do not. So the step's input is built only from stable
parts — the axis's fixed description (config/axis_evidence.yaml ``describe``), its
verdict and the claim pool — never the lens's rationale, and for a reviewed axis
(``literature_reviewed``) these picks are the only literature its score counts.

Picks only ever add support; nothing here lowers a score, because "contradicts"
judgments from a small model proved unreliable (it read LoF tolerance as evidence
against an inhibition hypothesis). Any failure — no embedder, an unparseable reply —
leaves the verdict as the lens produced it.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from core.json_utils import loads_recovering, strip_json_fence
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, EvidenceType
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.axis_evidence import axis_evidence_map

logger = logging.getLogger(__name__)

SKILL = "lens_citation"
CANDIDATES = 15
MAX_PICKS = 5

_LITERATURE_TYPES = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _parse_picks(raw: str, n_candidates: int) -> list[int]:
    try:
        data = loads_recovering(strip_json_fence(raw))
    except (ValueError, TypeError):
        return []
    items: Any = data.get("supports") if isinstance(data, dict) else data
    picks: list[int] = []
    for item in items or []:
        value: Any = item.get("n") if isinstance(item, dict) else item
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= n_candidates and n - 1 not in picks:
            picks.append(n - 1)
    return picks[:MAX_PICKS]


def _embedder(ctx: RunContext) -> Any:
    # Embeddings are always local (as in knowledge extraction), never routed.
    return ctx.router.get_provider("ollama")


async def add_literature_support(
    verdict: LensVerdict,
    shown: list[CoreClaim],
    ctx: RunContext,
    classification: DataClass,
    task: str,
) -> LensVerdict:
    """Add the literature claims that support each axis verdict (see module docstring)."""
    literature = [c for c in shown if c.evidence_type in _LITERATURE_TYPES]
    axes_to_cite = [a for a in verdict.axes if a.verdict is not None]
    if not literature or not axes_to_cite:
        return verdict
    evidence_map = axis_evidence_map()
    descriptions = {a.axis: evidence_map.describe(verdict.lens, a.axis) for a in axes_to_cite}
    try:
        embedder = _embedder(ctx)
        claim_vecs = await embedder.embed([c.claim_text for c in literature])
        axis_vecs = await embedder.embed(
            [f"{a.axis.replace('_', ' ')}: {descriptions[a.axis]}" for a in axes_to_cite]
        )
        provider, model = ctx.select_model(classification, task)
        system = ctx.load_skill(SKILL)
    except Exception as exc:  # degrade: no embedder / no provider → lens citations only
        logger.warning("lens citation step skipped for %s: %s", verdict.lens, exc)
        return verdict

    picked: dict[str, list[str]] = {}
    for axis, axis_vec in zip(axes_to_cite, axis_vecs, strict=True):
        similarity = [_cosine(axis_vec, vec) for vec in claim_vecs]
        ranked = sorted(range(len(literature)), key=lambda i: -similarity[i])
        candidates = [literature[i] for i in ranked[:CANDIDATES]]
        listing = "\n".join(f"{n}. {c.claim_text[:220]}" for n, c in enumerate(candidates, 1))
        verdict_word = "FAVOURABLE" if axis.verdict else "UNFAVOURABLE"
        user = (
            f"Target: {verdict.target_gene}. Disease: {verdict.disease}. Lens: {verdict.lens}.\n"
            f"Axis: {axis.axis} — {descriptions[axis.axis]}\nVerdict: {verdict_word}\n\n"
            f"Candidates:\n{listing}"
        )
        try:
            completion = await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": user}],
                    system=system,
                    classification=classification,
                    task=task,
                    max_tokens=400,
                    model_override=model,
                )
            )
        except Exception as exc:
            logger.warning("lens citation call failed for %s/%s: %s", verdict.lens, axis.axis, exc)
            continue
        ids = [
            str(candidates[i].evidence_id)
            for i in _parse_picks(completion.content, len(candidates))
        ]
        picked[axis.axis] = ids

    if not picked:
        return verdict
    axes: list[AxisVerdict] = []
    for axis in verdict.axes:
        if axis.axis not in picked:  # call failed: the axis was not reviewed
            axes.append(axis)
            continue
        picks = picked[axis.axis]
        new = [i for i in picks if i not in axis.supporting_claim_ids]
        axes.append(
            axis.model_copy(
                update={
                    "supporting_claim_ids": [*axis.supporting_claim_ids, *new],
                    "added_claim_ids": [*axis.added_claim_ids, *new],
                    "literature_reviewed": True,
                    # Including picks the lens model also cited: these are what scores.
                    # Picks a deterministic rule made before this step are kept.
                    "literature_picks": [
                        *axis.literature_picks,
                        *(p for p in picks if p not in axis.literature_picks),
                    ],
                }
            )
        )
    return verdict.model_copy(update={"axes": axes})
