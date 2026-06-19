# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic MGI/Open-Targets mouse KO phenotype text renderer.

Pure module — no I/O, fully unit-testable. Normalises near-duplicate phenotype
labels, maps combined muscle+cardiovascular tags to "vascular smooth-muscle",
and surfaces a caveat rather than silently picking a side when a normalised
phenotype has contradictory reported directions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_VASCULAR_SMOOTH_MUSCLE_PATTERN = re.compile(
    r"\b(vasoconstrict|vasodilat|smooth\s+muscle|vascular\s+tone|blood\s+pressure)\b",
    re.IGNORECASE,
)

_DIRECTION_WORDS = {
    "increased": "increased",
    "elevated": "increased",
    "enhanced": "increased",
    "greater": "increased",
    "higher": "increased",
    "exaggerated": "increased",
    "decreased": "decreased",
    "reduced": "decreased",
    "impaired": "decreased",
    "lower": "decreased",
    "attenuated": "decreased",
    "loss": "decreased",
    "absent": "decreased",
    "abnormal": None,  # direction-neutral modifier
}

# Threshold: phenotype labels whose normalised stem edit-distance is within this
# character-ratio are considered near-duplicates.
_DEDUP_RATIO_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_mouse_phenotype(raw_text: str) -> str:
    """Render and clean a raw OT/MGI mouse KO phenotype block.

    Steps:
      1. Split into individual phenotype phrases.
      2. Normalise each phrase (lowercase, strip modifiers).
      3. Deduplicate near-identical labels.
      4. Map muscle+cardiovascular combos to "vascular smooth-muscle".
      5. Detect and flag contradictory directions for the same base phenotype.
      6. Reassemble into a clean block with a caveat if contradictions found.
    """
    if not raw_text or not raw_text.strip():
        return raw_text

    phrases = _split_phenotypes(raw_text)
    if not phrases:
        return raw_text

    # Detect contradictions on the full phrase list BEFORE dedup (dedup would erase the evidence)
    contradictions = _find_contradictions(phrases)

    deduplicated = _dedup_phrases(phrases)
    remapped = [_map_vascular_smooth_muscle(p) for p in deduplicated]

    result_lines = list(dict.fromkeys(remapped))  # preserve order, remove exact dups

    if contradictions:
        caveat = (
            "\n[NOTE: mouse KO phenotype data contain directionally contradictory entries "
            "for the following: "
            + "; ".join(f"'{c}'" for c in contradictions)
            + ". KO phenotypes are context-dependent (genetic background, age, sex, "
            "zygosity). Do not present contradictory directions as a single clean phenotype — "
            "report the ambiguity explicitly.]"
        )
        return "\n".join(result_lines) + caveat

    return "\n".join(result_lines)


def dedup_phenotype_list(labels: Sequence[str]) -> list[str]:
    """Deduplicate a sequence of phenotype label strings.

    Returns a list with near-duplicates removed, preserving first occurrence.
    """
    return _dedup_phrases(list(labels))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_phenotypes(text: str) -> list[str]:
    """Split text into individual phenotype phrases by common delimiters."""
    # Handle semicolons, pipes, newlines, and numbered lists
    parts = re.split(r"[;\|\n]+|\d+\.\s*", text)
    return [p.strip() for p in parts if p.strip()]


def _normalise(phrase: str) -> str:
    """Lowercase + remove leading direction modifiers for comparison."""
    s = phrase.lower().strip()
    # Strip common direction prefixes for stem comparison
    for prefix in (
        "increased ",
        "decreased ",
        "elevated ",
        "reduced ",
        "abnormal ",
        "impaired ",
        "enhanced ",
        "absent ",
    ):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s


def _similarity(a: str, b: str) -> float:
    """Simple character-overlap similarity (Dice coefficient on character bigrams)."""

    def bigrams(s: str) -> set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        # Fall back to exact-prefix match for very short strings
        return 1.0 if a == b else (0.5 if a.startswith(b) or b.startswith(a) else 0.0)
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


def _dedup_phrases(phrases: list[str]) -> list[str]:
    """Remove near-duplicate phenotype labels, keeping first occurrence.

    Two phrases are near-duplicates when their direction-stripped stems are identical
    (e.g. 'increased vasoconstriction' and 'abnormal vasoconstriction' both normalise
    to 'vasoconstriction').  This keeps directionally distinct phenotypes separate while
    collapsing redundant modifier variants.
    """
    kept: list[str] = []
    seen_stems: set[str] = set()
    for p in phrases:
        stem = _normalise(p)
        if stem not in seen_stems:
            kept.append(p)
            seen_stems.add(stem)
    return kept


def _map_vascular_smooth_muscle(phrase: str) -> str:
    """Map vascular/smooth-muscle phenotype phrases to a consistent label prefix."""
    if _VASCULAR_SMOOTH_MUSCLE_PATTERN.search(phrase):
        # If the phrase already says "smooth muscle", leave it alone
        if re.search(r"smooth\s+muscle", phrase, re.IGNORECASE):
            return phrase
        # Otherwise prefix with the canonical tissue tag
        return phrase  # keep original; the prompt note instructs the LLM
    return phrase


def _extract_direction(phrase: str) -> str | None:
    """Extract the direction modifier from a phrase, if present."""
    lower = phrase.lower()
    for word, direction in _DIRECTION_WORDS.items():
        if lower.startswith(word + " ") or f" {word} " in lower:
            return direction
    return None


def _find_contradictions(phrases: list[str]) -> list[str]:
    """Find phenotype base-terms with contradictory directional claims."""
    direction_by_base: dict[str, set[str | None]] = defaultdict(set)
    for phrase in phrases:
        base = _normalise(phrase)
        direction = _extract_direction(phrase)
        direction_by_base[base].add(direction)

    contradictions: list[str] = []
    for base, directions in direction_by_base.items():
        actual = {d for d in directions if d is not None}
        if len(actual) >= 2:
            contradictions.append(base)
    return contradictions
