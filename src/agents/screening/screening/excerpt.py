# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The full-text excerpt the second screening pass reads.

Pass 2 re-judges an item pass 1 left uncertain, so its excerpt should show what the paper
did with the target rather than its opening pages. Within ``max_chars`` it gives, in order:

1. a one-line count of where the target is named, per section;
2. the authors' own summary: the conclusions, or else the last two discussion paragraphs;
3. the paragraphs naming the target, cut to the mention plus one sentence either side,
   ranked results, then figure and table captions, discussion, methods, other; within a
   rank, those also naming the disease first, then document order.

The introduction is left out: it reviews earlier work, often on other genes, so a target
named only there shows in the count line and nowhere else. A body without sections gets
its target-naming paragraphs in order plus its last two paragraphs.

Pure functions; every name matched comes from the run through the two callables.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
_SNIPPET_RANK = {"results": 0, "captions": 1, "discussion": 2, "methods": 3, "other": 4}
_SUMMARY_PARAGRAPHS = 2  # discussion paragraphs standing in for missing conclusions
_GAP = "…"


def _sentences(paragraph: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(paragraph.strip()) if s]


def cut_to_mentions(paragraph: str, mentions: Callable[[str], int]) -> str:
    """The sentences naming the target, each with one sentence either side; skipped runs
    of sentences are marked with an ellipsis."""
    sentences = _sentences(paragraph)
    hits = [i for i, s in enumerate(sentences) if mentions(s)]
    if not hits:
        return paragraph.strip()
    keep = sorted({j for i in hits for j in (i - 1, i, i + 1) if 0 <= j < len(sentences)})
    out: list[str] = [_GAP] if keep[0] > 0 else []
    for prev, j in zip([None, *keep], keep, strict=False):
        if prev is not None and j != prev + 1:
            out.append(_GAP)
        out.append(sentences[j])
    if keep[-1] < len(sentences) - 1:
        out.append(_GAP)
    return " ".join(out)


def _count_line(counts: Mapping[str, int]) -> str:
    named = {k: n for k, n in counts.items() if n}
    if not named:
        return "Target named: nowhere in the full text."
    parts = ", ".join(f"{k} {n}" for k, n in sorted(named.items(), key=lambda kv: -kv[1]))
    if set(named) == {"introduction"}:
        return f"Target named: {parts} (only in the introduction)."
    return f"Target named: {parts}."


def _fit(blocks: Sequence[str], max_chars: int, *, required: int = 1) -> str:
    """Blocks joined by newlines within ``max_chars``. The first ``required`` blocks (the
    count line and the summary) are cut to the room left; a later block that does not fit
    is skipped, and a shorter one after it may still go in."""
    out: list[str] = []
    used = 0
    for i, block in enumerate(blocks):
        sep = 1 if out else 0
        room = max_chars - used - sep
        if len(block) <= room:
            out.append(block)
            used += len(block) + sep
        elif i < required and room > len(_GAP):
            out.append(block[: room - len(_GAP)] + _GAP)
            used += room + sep
    return "\n".join(out)


def _flat_excerpt(text: str, mentions: Callable[[str], int]) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    total = sum(mentions(p) for p in paragraphs)
    count = (
        f"Target named: {total} times (the full text has no sections)."
        if total
        else "Target named: nowhere in the full text."
    )
    tail = paragraphs[-_SUMMARY_PARAGRAPHS:]
    named = [p for p in paragraphs[: len(paragraphs) - len(tail)] if mentions(p)]
    return [
        count,
        *(f"[paragraph] {cut_to_mentions(p, mentions)}" for p in named),
        *(f"[end of text] {p}" for p in tail),
    ]


def build_excerpt(
    sections: Sequence[Mapping[str, Any]],
    *,
    mentions: Callable[[str], int],
    names_disease: Callable[[str], bool],
    max_chars: int,
    flat_text: str = "",
) -> str:
    """The pass-2 excerpt of one paper (see module docstring), at most ``max_chars`` long.

    ``sections`` are ``FullTextSection`` dumps (kind, title, paragraphs, captions);
    ``mentions`` counts the target's names in a text and ``names_disease`` says whether a
    text names the disease. ``flat_text`` is used when there are no sections.
    """
    if max_chars <= 0:
        return ""
    if not sections:
        if not flat_text.strip():
            return ""
        return _fit(_flat_excerpt(flat_text, mentions), max_chars)

    counts: dict[str, int] = {}
    for sec in sections:
        kind = str(sec.get("kind") or "other")
        texts = [*(sec.get("paragraphs") or []), *(sec.get("captions") or [])]
        counts[kind] = counts.get(kind, 0) + sum(mentions(t) for t in texts)

    def _paragraphs(kind: str) -> list[str]:
        return [p for s in sections if s.get("kind") == kind for p in s.get("paragraphs") or []]

    conclusions = _paragraphs("conclusions")
    if conclusions:
        summarised = conclusions
        summary = [f"[conclusions] {' '.join(conclusions)}"]
    else:
        summarised = _paragraphs("discussion")[-_SUMMARY_PARAGRAPHS:]
        summary = [f"[end of discussion] {p}" for p in summarised]

    candidates: list[tuple[int, bool, int, str, str]] = []
    order = 0
    for sec in sections:
        kind = str(sec.get("kind") or "other")
        if kind in ("introduction", "conclusions"):
            continue
        texts = [(kind, p) for p in sec.get("paragraphs") or []]
        texts += [("captions", c) for c in sec.get("captions") or []]
        for label, text in texts:
            order += 1
            if text in summarised or not mentions(text):
                continue
            rank = _SNIPPET_RANK.get(label, _SNIPPET_RANK["other"])
            candidates.append((rank, not names_disease(text), order, label, text))
    candidates.sort()
    snippets = [
        f"[{'figure/table caption' if label == 'captions' else label}] "
        f"{cut_to_mentions(text, mentions)}"
        for _rank, _no_disease, _order, label, text in candidates
    ]
    return _fit([_count_line(counts), *summary, *snippets], max_chars, required=1 + len(summary))
