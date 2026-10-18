# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Source-quality cache keyed by source identity (services/evidence/quality_cache.py).

The failure these prevent: a cached quality map keyed by an earlier run's evidence UUIDs
matched no document of a repeat run, so every repeat run's lenses saw literature with no
quality score at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from services.evidence.quality_cache import (
    QUALITY_CACHE_VERSION,
    from_cache,
    quality_sources,
    to_cache,
)


def _doc(source: str) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="DISEASE-A",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source=source,
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
        ),
    )


@pytest.fixture(autouse=True)
def _default_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "false")
    monkeypatch.setenv("OPENALEX_ENABLED", "true")


def test_repeat_run_with_new_uuids_reuses_every_score() -> None:
    first = [_doc("PMID:1"), _doc("PMID:2")]
    scores = {
        str(first[0].evidence_id): {"evidence_id": str(first[0].evidence_id), "sjr_score": 0.85},
        str(first[1].evidence_id): {"sjr_score": 0.4},
    }
    payload = to_cache(scores, first)

    repeat = [_doc("PMID:1"), _doc("PMID:2")]  # same sources, new run UUIDs
    reused, missing = from_cache(payload, repeat)

    assert missing == []
    assert reused == {
        str(repeat[0].evidence_id): {"sjr_score": 0.85},
        str(repeat[1].evidence_id): {"sjr_score": 0.4},
    }


def test_only_documents_the_cache_lacks_are_left_to_score() -> None:
    first = [_doc("PMID:1")]
    payload = to_cache({str(first[0].evidence_id): {"sjr_score": 0.65}}, first)

    repeat = [_doc("PMID:1"), _doc("PMID:9")]
    reused, missing = from_cache(payload, repeat)

    assert list(reused) == [str(repeat[0].evidence_id)]
    assert [ev.source for ev in missing] == ["PMID:9"]


def test_a_map_keyed_by_an_earlier_runs_uuids_covers_nothing() -> None:
    docs = [_doc("PMID:1")]
    legacy = {str(uuid.uuid4()): {"sjr_score": 0.85}}  # the format cached before the fix

    reused, missing = from_cache(legacy, docs)

    assert reused == {}
    assert missing == docs


def test_scores_cached_with_sjr_on_are_not_reused_with_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = [_doc("PMID:1")]
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "true")
    payload = to_cache({str(docs[0].evidence_id): {"sjr_score": 1.0}}, docs)

    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "false")
    reused, missing = from_cache(payload, docs)

    assert reused == {}
    assert missing == docs


def test_scoring_new_documents_keeps_the_older_cached_ones() -> None:
    old = [_doc("PMID:1")]
    previous = to_cache({str(old[0].evidence_id): {"sjr_score": 0.65}}, old)

    new = [_doc("PMID:2")]
    payload = to_cache({str(new[0].evidence_id): {"sjr_score": 0.2}}, new, previous)

    assert payload["version"] == QUALITY_CACHE_VERSION
    assert payload["quality_sources"] == quality_sources()
    assert set(payload["by_source"]) == {"PMID:1", "PMID:2"}


def test_a_document_without_a_source_is_never_cached_or_matched() -> None:
    nameless = _doc("")
    payload = to_cache({str(nameless.evidence_id): {"sjr_score": 0.4}}, [nameless])

    assert payload["by_source"] == {}
    _, missing = from_cache(payload, [nameless])
    assert missing == [nameless]


def test_entries_cached_without_icite_are_rescored_once_it_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = [_doc("PMID:1")]
    monkeypatch.setenv("ICITE_ENABLED", "false")
    payload = to_cache({str(docs[0].evidence_id): {"sjr_score": 0.4}}, docs)

    monkeypatch.setenv("ICITE_ENABLED", "true")
    _, missing = from_cache(payload, docs)

    assert missing == docs
