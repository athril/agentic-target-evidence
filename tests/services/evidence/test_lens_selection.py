# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Slots per evidence kind (services/evidence/lens_selection.py) and their wiring into
`_shown_claims`. Synthetic claims; the counts reproduce the shapes of saved runs (hundreds
of trial claims against tens of literature claims), not any one target."""

from __future__ import annotations

import random
import uuid
from collections import Counter
from datetime import UTC, datetime

import pytest

from agents.interpretation._lens_base import _shown_claims
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from services.evidence.lens_selection import (
    KindSlots,
    claim_kind,
    kind_counts,
    lens_slots,
    rotate_sources,
    select_claims,
)

_RUN = uuid.uuid4()


def _claim(
    etype: EvidenceType,
    text: str,
    *,
    source: uuid.UUID | None = None,
    confidence: float = 0.8,
    topics: list[str] | None = None,
) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=source or uuid.uuid4(),
        run_id=_RUN,
        gene="GENE-A",
        disease="disease a",
        evidence_type=etype,
        claim_text=text,
        confidence=confidence,
        topics=topics or [],
        provenance=Provenance(
            agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
    )


def _trials(n: int) -> list[CoreClaim]:
    return [
        _claim(EvidenceType.CLINICAL_TRIAL, f"NCT{i:08d} (PHASE3 COMPLETED) (n={1000 + i})")
        for i in range(n)
    ]


def _papers(n: int, lens: str) -> list[CoreClaim]:
    return [_claim(EvidenceType.ARTICLE, f"paper {i} finding", topics=[lens]) for i in range(n)]


def test_trials_cannot_crowd_published_reports_out_of_the_clinical_lens() -> None:
    claims = _trials(500) + _papers(60, "clinical")

    shown = _shown_claims(claims, {}, frozenset(), "clinical", 100)

    counts = Counter(c.evidence_type for c in shown)
    assert counts[EvidenceType.ARTICLE] >= 40
    assert counts[EvidenceType.CLINICAL_TRIAL] <= 60
    assert len(shown) == 100


def test_commercial_lens_sees_patents_and_landscape_beside_hundreds_of_trials() -> None:
    claims = (
        _trials(300)
        + [_claim(EvidenceType.PATENT, f"patent {i}") for i in range(70)]
        + [_claim(EvidenceType.COMPETITION, f"landscape {i}") for i in range(2)]
        + [_claim(EvidenceType.REGULATORY, f"label {i}") for i in range(30)]
    )

    shown = _shown_claims(claims, {}, frozenset(), "commercial", 100)

    counts = Counter(c.evidence_type for c in shown)
    assert counts[EvidenceType.PATENT] >= 30
    assert counts[EvidenceType.COMPETITION] == 2
    assert counts[EvidenceType.REGULATORY] >= 20
    assert len(shown) == 100


def test_resolved_journal_scores_do_not_push_structured_records_out_of_biology() -> None:
    structured = [_claim(EvidenceType.EXPRESSION, f"expression {i}") for i in range(20)]
    papers = _papers(200, "biology")
    quality = {str(c.source_evidence_id): {"sjr_score": 0.85} for c in papers}

    with_scores = _shown_claims(structured + papers, quality, frozenset(), "biology", 100)
    without = _shown_claims(structured + papers, {}, frozenset(), "biology", 100)

    for shown in (with_scores, without):
        assert sum(c.evidence_type == EvidenceType.EXPRESSION for c in shown) == 20
    assert Counter(c.evidence_type for c in with_scores) == Counter(
        c.evidence_type for c in without
    )


def test_one_label_cannot_fill_the_regulatory_view() -> None:
    big = uuid.uuid4()
    claims = [
        _claim(EvidenceType.REGULATORY, f"big label line {i}", source=big, confidence=0.99)
        for i in range(120)
    ] + [
        _claim(EvidenceType.REGULATORY, f"small label {j}", source=uuid.uuid4(), confidence=0.5)
        for j in range(15)
    ]

    shown = _shown_claims(claims, {}, frozenset(), "regulatory", 30)

    sources = {c.source_evidence_id for c in shown}
    assert len(sources) == 16  # every label appears before any label gets a second claim


def test_sources_take_turns_and_claims_past_the_cap_come_last() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    ranked = [
        _claim(EvidenceType.ARTICLE, "a1", source=a),
        _claim(EvidenceType.ARTICLE, "a2", source=a),
        _claim(EvidenceType.ARTICLE, "a3", source=a),
        _claim(EvidenceType.ARTICLE, "b1", source=b),
    ]

    order = [c.claim_text for c in rotate_sources(ranked, per_source=2)]

    assert order == ["a1", "b1", "a2", "a3"]
    assert [c.claim_text for c in rotate_sources(ranked, per_source=0)] == [
        "a1",
        "a2",
        "a3",
        "b1",
    ]


def test_a_split_question_budget_scales_every_kind() -> None:
    claims = _trials(200) + _papers(100, "clinical")

    shown = _shown_claims(claims, {}, frozenset(), "clinical", 25)

    counts = Counter(c.evidence_type for c in shown)
    assert len(shown) == 25
    assert counts[EvidenceType.ARTICLE] >= 10  # 40 per 100 slots, scaled to 25


def test_a_maximum_holds_while_another_kind_still_has_candidates() -> None:
    kinds = (
        KindSlots(kind="a", types=("patent",), min=10, max=20),
        KindSlots(kind="b", types=("regulatory",), min=10, max=100),
    )
    claims = [_claim(EvidenceType.PATENT, f"p{i}") for i in range(50)] + [
        _claim(EvidenceType.REGULATORY, f"r{i}") for i in range(120)
    ]

    counts = kind_counts(select_claims(claims, kinds, 100, lambda _k, items: items), kinds)

    assert counts == {"a": 20, "b": 80}  # a stops at its max while b has candidates


def test_a_maximum_never_leaves_slots_empty_that_only_its_kind_can_fill() -> None:
    kinds = (
        KindSlots(kind="a", types=("patent",), min=10, max=20),
        KindSlots(kind="b", types=("regulatory",), min=10, max=100),
    )
    claims = [_claim(EvidenceType.PATENT, f"p{i}") for i in range(50)] + [
        _claim(EvidenceType.REGULATORY, f"r{i}") for i in range(3)
    ]

    counts = kind_counts(select_claims(claims, kinds, 30, lambda _k, items: items), kinds)

    assert counts == {"a": 27, "b": 3}


def test_a_claim_of_an_unlisted_type_is_kept_as_other() -> None:
    kinds = (KindSlots(kind="a", types=("patent",), min=0, max=10),)
    claims = [_claim(EvidenceType.PATENT, "p"), _claim(EvidenceType.OMICS, "o")]

    shown = select_claims(claims, kinds, 10, lambda _k, items: items)

    assert claim_kind(claims[1], kinds) == "other"
    assert {c.claim_text for c in shown} == {"p", "o"}


def test_selection_does_not_depend_on_input_order() -> None:
    claims = _trials(150) + _papers(80, "clinical")
    shuffled = claims[:]
    random.Random(7).shuffle(shuffled)

    first = _shown_claims(claims, {}, frozenset(), "clinical", 100)
    second = _shown_claims(shuffled, {}, frozenset(), "clinical", 100)

    assert [c.evidence_id for c in first] == [c.evidence_id for c in second]


@pytest.mark.parametrize("budget", [0, 1, 7, 100, 250])
def test_any_budget_and_an_empty_pool_are_safe(budget: int) -> None:
    assert _shown_claims([], {}, frozenset(), "genetics", budget) == []
    shown = _shown_claims(
        _trials(40) + _papers(40, "clinical"), {}, frozenset(), "clinical", budget
    )
    assert len(shown) <= budget


def test_every_lens_has_configured_kinds_covering_its_evidence_types() -> None:
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES

    for lens, types in LENS_EVIDENCE_TYPES.items():
        kinds = lens_slots(lens)
        listed = {t for k in kinds for t in k.types}
        assert {t.value for t in types} <= listed, lens
