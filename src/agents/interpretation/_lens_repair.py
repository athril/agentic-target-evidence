# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens repair step: one bounded rewrite of prose a deterministic check flagged.

The guards, numeric grounding and fact checks annotate sentences that contradict the
data. Left there, the first thing a reader sees can be a false sentence followed by a
warning ("strong functional genomics data indicating dependency across cancer cell
lines" — for a gene that is a dependency in 2/1178 lines). This step sends only the
flagged sentences back to the lens model with the check's note and the data lines
the sentence relies on, and asks for a corrected sentence.

Bounded and verifiable:
- only sentences with a non-terminology violation are sent; nothing else changes;
- one model call per lens, no retries;
- a rewrite must stay on the sentence's topic (share enough of its content words):
  a sentence about a boxed warning must not come back as one about an indication;
- a rewrite must not bring in a missing-value placeholder ("n/a", "null") the original
  did not have: copied from an empty data field, it replaced real figures with "p-values
  of n/a";
- each field's rewrite is re-checked by the same checks and kept only if it has
  fewer violations and no new kind of violation — otherwise the original stays;
- every accepted rewrite records a low-severity `prose_repaired` flag carrying the
  original sentence, so the correction is visible in the report and to the critic.

The checks themselves then run on the final prose (run_lens), so remaining flags
describe the text as published. Disable with LENS_PROSE_REPAIR=0.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from typing import Any

from core.json_utils import loads_concatenated, loads_recovering, strip_json_fence
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass
from schemas.verdicts import LensVerdict, ValidationFlag
from services.evidence.guard_core import Violation, strip_annotations
from services.evidence.source_registry import named_sources

logger = logging.getLogger(__name__)

SKILL = "lens_repair"
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MAX_DATA_LINES = 3
# A rewrite keeps at least this many of the original's content words, and this share.
_MIN_SHARED_WORDS = 2
_MIN_SHARED_SHARE = 0.2
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "that",
        "this",
        "these",
        "those",
        "there",
        "their",
        "which",
        "with",
        "from",
        "into",
        "than",
        "then",
        "have",
        "been",
        "were",
        "also",
        "only",
        "more",
        "most",
        "such",
        "very",
        "does",
        "show",
        "shows",
        "shown",
        "data",
    }
)


def repair_enabled() -> bool:
    return os.environ.get("LENS_PROSE_REPAIR", "1").strip().lower() not in {"0", "false", "no"}


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text.strip()) if s]


def _blocking(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.kind != "terminology"]


def _hits(sentence: str, violation: Violation) -> bool:
    excerpt = strip_annotations(violation.excerpt).strip()
    if not excerpt:
        return False
    return excerpt in sentence or sentence in excerpt or excerpt[:60] in sentence


def _data_lines(sentence: str, pool: list[CoreClaim], context: str) -> list[str]:
    """Claims and context lines naming a source the sentence names (the data it relies on)."""
    named = [s for s in named_sources() if s.mentioned_in(sentence)]
    if not named:
        return []
    lines = [c.claim_text for c in pool if any(s.mentioned_in(c.claim_text) for s in named)]
    lines += [
        ln for ln in context.splitlines() if ln.strip() and any(s.mentioned_in(ln) for s in named)
    ]
    return [ln[:300] for ln in dict.fromkeys(lines)][:_MAX_DATA_LINES]


def _content_words(text: str) -> set[str]:
    return {
        w for w in _WORD_RE.findall(strip_annotations(text).lower()) if len(w) >= 4
    } - _STOPWORDS


def _on_topic(original: str, rewrite: str) -> bool:
    """The rewrite keeps enough of the original's content words to be the same sentence."""
    words = _content_words(original)
    if not words:
        return True
    shared = len(words & _content_words(rewrite))
    return shared >= min(_MIN_SHARED_WORDS, len(words)) and shared / len(words) >= _MIN_SHARED_SHARE


# Missing-value placeholders a data line can carry for an empty field.
_PLACEHOLDER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[nN]/[aA]|NaN|null|None|undefined)(?![A-Za-z0-9])"
)


def _adds_placeholder(original: str, rewrite: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(rewrite)) and not _PLACEHOLDER_RE.search(original)


def _parse(raw: str) -> dict[str, str]:
    text = strip_json_fence(raw)
    try:
        data: Any = loads_recovering(text)
    except (ValueError, TypeError):
        # One object per sentence ({"S1": …}\n{"S2": …}) instead of one object: merge them.
        data = {}
        for part in loads_concatenated(text):
            if isinstance(part, dict):
                data.update(part)
    if not isinstance(data, dict):
        return {}
    return {
        str(k).upper(): str(v).strip() for k, v in data.items() if isinstance(v, str) and v.strip()
    }


async def repair_prose(
    verdict: LensVerdict,
    *,
    find: Callable[[str], list[Violation]],
    pool: list[CoreClaim],
    context: str,
    ctx: RunContext,
    classification: DataClass,
    task: str,
) -> LensVerdict:
    """Rewrite flagged sentences once and keep each field's rewrite only if it checks out.

    ``find`` returns every violation the lens's checks find in a text — the same
    checks run_lens applies afterwards.
    """
    fields: list[tuple[str, int | None, str]] = [
        ("rationale", None, verdict.rationale),
        ("narrative", None, verdict.narrative),
        *(("axis", i, a.rationale) for i, a in enumerate(verdict.axes)),
    ]
    # (field index, sentence index, sentence, violations)
    targets: list[tuple[int, int, str, list[Violation]]] = []
    for f_idx, (_, _, text) in enumerate(fields):
        violations = _blocking(find(text))
        if not violations:
            continue
        for s_idx, sentence in enumerate(_sentences(text)):
            hit = [v for v in violations if _hits(sentence, v)]
            if hit:
                targets.append((f_idx, s_idx, sentence, hit))
    if not targets:
        return verdict

    blocks: list[str] = []
    for n, (f_idx, _, sentence, hit) in enumerate(targets, 1):
        kind, axis_idx, _ = fields[f_idx]
        where = f"axis '{verdict.axes[axis_idx].axis}'" if axis_idx is not None else kind
        notes = "\n".join(f"  Problem: {v.note.strip('[]⚠ ')}" for v in hit)
        data = "\n".join(f"  Data: {ln}" for ln in _data_lines(sentence, pool, context))
        blocks.append(f"S{n} ({where}): {sentence}\n{notes}" + (f"\n{data}" if data else ""))
    user = (
        f"Target: {verdict.target_gene}. Disease: {verdict.disease}. Lens: {verdict.lens}.\n\n"
        + "\n\n".join(blocks)
    )
    try:
        provider, model = ctx.select_model(classification, task)
        completion = await provider.complete(
            CompletionRequest(
                messages=[{"role": "user", "content": user}],
                system=ctx.load_skill(SKILL),
                classification=classification,
                task=task,
                max_tokens=120 * len(targets) + 100,
                model_override=model,
            )
        )
    except Exception as exc:  # degrade: the checks annotate the original prose
        logger.warning("lens repair skipped for %s: %s", verdict.lens, exc)
        return verdict
    rewrites = _parse(completion.content)

    texts = [text for _, _, text in fields]
    flags: list[ValidationFlag] = []
    for f_idx, (_, _, original) in enumerate(fields):
        mine = [(n, t) for n, t in enumerate(targets, 1) if t[0] == f_idx]
        if not mine:
            continue
        candidate = original
        changed: list[tuple[str, str]] = []
        for n, (_, _, sentence, _) in mine:
            new = rewrites.get(f"S{n}", "")
            if (
                new
                and new != sentence
                and len(new) <= 2 * len(sentence) + 200
                and _on_topic(sentence, new)
                and not _adds_placeholder(sentence, new)
            ):
                # In place, so paragraph breaks and every other sentence stay byte-identical.
                candidate = candidate.replace(sentence, new, 1)
                changed.append((sentence, new))
        if not changed:
            continue
        before = _blocking(find(original))
        after = _blocking(find(candidate))
        if len(after) < len(before) and {v.rule_id for v in after} <= {v.rule_id for v in before}:
            texts[f_idx] = candidate
            for old, new in changed:
                flags.append(
                    ValidationFlag(
                        lens=verdict.lens,
                        severity="low",
                        rule_id="prose_repaired",
                        claim_excerpt=old[:300],
                        message=f"Rewritten to match the data: {new[:300]}",
                    )
                )
    if not flags:
        logger.info("lens repair for %s: no rewrite passed the checks", verdict.lens)
        return verdict
    logger.info("lens repair for %s: %d sentence(s) corrected", verdict.lens, len(flags))
    axes = [a.model_copy(update={"rationale": texts[2 + i]}) for i, a in enumerate(verdict.axes)]
    return verdict.model_copy(
        update={
            "rationale": texts[0],
            "narrative": texts[1],
            "axes": axes,
            "validation_flags": [*verdict.validation_flags, *flags],
        }
    )
