# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Does a curated gene–disease record concern the run's indication?

Curation sources (ClinGen, GenCC, Orphanet) and ClinVar list *every* disease a gene is
linked to. Treating the strongest of them as evidence for the run's indication made a
gene's Mendelian disease count for any disease it was run against — PCSK9's
"hypercholesterolemia, autosomal dominant, 3" (Definitive) was reported as Mendelian
validation for myocardial infarction, and the causality axis was clamped to favourable.

A record is in scope when its disease is the run's disease or one of its ontology
descendants (Open Targets EFO/MONDO hierarchy), or — when ids are missing — when one
disease name contains the other ("focal segmental glomerulosclerosis 2" is in scope for
"focal segmental glomerulosclerosis"). Pure module: the caller supplies the id set.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Names shorter than this never name-match (too generic: "cancer", "obesity" alone).
_MIN_NAME_CHARS = 8

# Strength order shared by ClinGen and GenCC classifications.
CLASSIFICATION_RANK: dict[str, int] = {
    "definitive": 6,
    "strong": 5,
    "moderate": 4,
    "supportive": 3,
    "limited": 2,
    "disputed": 1,
    "disputed evidence": 1,
    "refuted": 0,
    "refuted evidence": 0,
    "no known disease relationship": 0,
}


def normalize_curie(curie: str | None) -> str:
    """``MONDO:0011369`` / ``mondo_0011369`` / an OBO IRI → ``MONDO_0011369``."""
    if not curie:
        return ""
    tail = curie.rsplit("/", 1)[-1].strip()
    return tail.replace(":", "_").upper()


def _norm_name(name: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", (name or "").lower()).split())


def names_match(a: str, b: str) -> bool:
    """One disease name contains the other, word-aligned ("fsgs 2" ⊃ "fsgs")."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    if len(short) < _MIN_NAME_CHARS:
        return short == long_
    return f" {short} " in f" {long_} "


def in_scope(
    curie: str | None,
    label: str,
    *,
    scope_ids: frozenset[str] | set[str] | None,
    disease_name: str,
) -> bool:
    """Whether a curated record (``curie``/``label``) concerns the run's disease."""
    ids = {normalize_curie(i) for i in (scope_ids or ())}
    if curie and normalize_curie(curie) in ids:
        return True
    return names_match(label, disease_name)


def strongest_in_scope(
    associations: list[dict[str, object]],
    *,
    scope_ids: frozenset[str] | set[str] | None,
    disease_name: str,
) -> str | None:
    """Strongest classification among associations for the run's disease, else None.

    Accepts ClinGen (``disease_label``) and GenCC (``disease_title``) field names.
    """
    best: tuple[int, str] | None = None
    for a in associations:
        label = str(a.get("disease_label") or a.get("disease_title") or a.get("disease_name") or "")
        curie = a.get("disease_curie") or a.get("disease_id")
        if not in_scope(
            str(curie) if curie else None, label, scope_ids=scope_ids, disease_name=disease_name
        ):
            continue
        cls = str(a.get("classification") or "")
        rank = CLASSIFICATION_RANK.get(cls.lower(), -1)
        if best is None or rank > best[0]:
            best = (rank, cls)
    return best[1] if best else None
