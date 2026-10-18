# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Choose the claims a lens is shown: slots per evidence kind, sources taking turns.

A lens used to see the strongest N of its claims on one weight scale shared by every kind
of evidence. Registry trials (0.9) then outranked unscored literature (0.2), so a clinical
lens saw no published outcome report; trials outranked patents and landscape claims, so a
commercial lens saw neither; and when journal scores did resolve, literature outranked
every structured record of the biology and safety lenses. The order across kinds depended
on whether an external quota lasted.

Here each kind is ranked on its own and given slots by ``config/lens_inputs.yaml``: every
kind's minimum first, then the remaining slots round-robin across kinds that still have
candidates, up to their maxima, and past them only when no other kind has candidates
left. Within a kind, sources (papers, trial records, labels)
take turns, so one label's thirty sentences cannot fill a lens's view. Pure module: the
caller supplies how claims of each kind are ranked.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from schemas.evidence import CoreClaim, EvidenceType

LENS_INPUTS_PATH = Path("config/lens_inputs.yaml")

LITERATURE = "literature"
_LITERATURE_TYPES = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)
_OTHER = "other"  # claims of a type no kind lists; never silently dropped


class KindSlots(BaseModel):
    kind: str
    types: tuple[str, ...]
    min: int = 0
    max: int = 100
    per_source: int = 0


Ranker = Callable[[str, list[CoreClaim]], list[CoreClaim]]


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, tuple[KindSlots, ...]]:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    return {
        lens: tuple(KindSlots.model_validate(k) for k in kinds or [])
        for lens, kinds in (raw.get("lenses") or {}).items()
    }


def lens_slots(lens: str, path: Path = LENS_INPUTS_PATH) -> tuple[KindSlots, ...]:
    """The kinds configured for ``lens`` (empty for a lens the config does not list)."""
    return _load(str(path)).get(lens, ())


def claim_kind(claim: CoreClaim, kinds: Sequence[KindSlots]) -> str:
    """The configured kind a claim belongs to, or ``other``."""
    type_name = (
        LITERATURE if claim.evidence_type in _LITERATURE_TYPES else claim.evidence_type.value
    )
    for k in kinds:
        if type_name in k.types:
            return k.kind
    return _OTHER


def source_of(claim: CoreClaim) -> str:
    """The document a claim was extracted from (itself for a document-level row)."""
    return str(claim.source_evidence_id or claim.evidence_id)


def rotate_sources(ranked: Sequence[CoreClaim], per_source: int) -> list[CoreClaim]:
    """``ranked`` reordered so sources take turns: each source's best claim, in the order
    of those best claims, before any source's second, up to ``per_source`` turns; claims
    past the cap follow in rank order. ``per_source`` 0 keeps rank order."""
    if per_source <= 0:
        return list(ranked)
    by_source: dict[str, list[CoreClaim]] = {}
    for c in ranked:
        by_source.setdefault(source_of(c), []).append(c)
    out: list[CoreClaim] = []
    for turn in range(per_source):
        out.extend(items[turn] for items in by_source.values() if len(items) > turn)
    taken = {id(c) for c in out}
    out.extend(c for c in ranked if id(c) not in taken)
    return out


def _scaled(value: int, budget: int, *, up: bool) -> int:
    exact = value * budget / 100
    return math.ceil(exact) if up else math.floor(exact)


def select_claims(
    claims: Iterable[CoreClaim],
    kinds: Sequence[KindSlots],
    budget: int,
    rank: Ranker,
) -> list[CoreClaim]:
    """At most ``budget`` claims, by kind slots; returned grouped by kind in config order.

    ``rank(kind, claims)`` orders one kind's claims best first. Claims of an unlisted type
    form an ``other`` kind with no minimum that may use leftover slots.
    """
    if budget <= 0:
        return []
    pools: dict[str, list[CoreClaim]] = {}
    for c in claims:
        pools.setdefault(claim_kind(c, kinds), []).append(c)
    configured = {k.kind: k for k in kinds}
    order = [k.kind for k in kinds if k.kind in pools]
    if _OTHER in pools:
        configured[_OTHER] = KindSlots(kind=_OTHER, types=(), min=0, max=100)
        order.append(_OTHER)

    queues = {
        name: rotate_sources(rank(name, pools[name]), configured[name].per_source) for name in order
    }
    caps = {name: _scaled(configured[name].max, budget, up=True) for name in order}
    taken: dict[str, int] = dict.fromkeys(order, 0)
    remaining = budget

    for name in order:
        want = min(_scaled(configured[name].min, budget, up=False), caps[name], len(queues[name]))
        want = min(want, remaining)
        taken[name] = want
        remaining -= want

    # Up to each kind's maximum, then — once no kind under its maximum has candidates
    # left — past it: a maximum keeps one kind from crowding the others out, it never
    # leaves slots empty that only that kind could fill.
    capped = {name: min(caps[name], len(queues[name])) for name in order}
    uncapped = {name: len(queues[name]) for name in order}
    for limits in (capped, uncapped):
        while remaining > 0:
            progressed = False
            for name in order:
                if remaining == 0:
                    break
                if taken[name] < limits[name]:
                    taken[name] += 1
                    remaining -= 1
                    progressed = True
            if not progressed:
                break

    return [c for name in order for c in queues[name][: taken[name]]]


def kind_counts(shown: Iterable[CoreClaim], kinds: Sequence[KindSlots]) -> Mapping[str, int]:
    """How many shown claims each kind got (for measurement)."""
    counts: dict[str, int] = {}
    for c in shown:
        name = claim_kind(c, kinds)
        counts[name] = counts.get(name, 0) + 1
    return counts
