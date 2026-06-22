# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _enrich_uncertain_with_full_text (pass-2 PMC full-text enrichment)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from capabilities.target_validation import workflow
from mcp_servers.pubmed.tools import PubMedFullText
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _make_ev(run_id, provenance: Provenance, *, source: str, verdict: str, scope: str, extra: dict | None = None) -> Evidence:
    base_extra = {"title": "Test title", "abstract": "Test abstract.", "screening_verdict": {"verdict": verdict}}
    base_extra.update(extra or {})
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        target_gene="TRPC6",
        disease="FSGS",
        evidence_type=EvidenceType.ARTICLE,
        scope=scope,
        source=source,
        source_link=f"https://pubmed.ncbi.nlm.nih.gov/{source[5:]}/" if source.startswith("PMID:") else "https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=provenance,
        extra=base_extra,
    )


@pytest.fixture()
def mock_fetch(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(workflow, "fetch_pmc_record", mock)
    return mock


async def test_enrich_skips_non_uncertain_items(run_id, sample_provenance, mock_fetch):
    ev = _make_ev(run_id, sample_provenance, source="PMID:11111", verdict="keep", scope="abstract")
    out = await workflow._enrich_uncertain_with_full_text([ev], force=False)
    assert out == [ev]
    mock_fetch.assert_not_awaited()


async def test_enrich_skips_uncertain_without_pmid(run_id, sample_provenance, mock_fetch):
    ev = _make_ev(run_id, sample_provenance, source="NCT05213624", verdict="uncertain", scope="abstract")
    out = await workflow._enrich_uncertain_with_full_text([ev], force=False)
    assert out == [ev]
    mock_fetch.assert_not_awaited()


async def test_enrich_upgrades_scope_and_extra_on_successful_fetch(run_id, sample_provenance, mock_fetch):
    mock_fetch.return_value = PubMedFullText(
        pmid="11111",
        pmc_id="9999999",
        full_text_url="https://pmc.ncbi.nlm.nih.gov/9999999",
        available=True,
        full_text="Detailed body prose about TRPC6.",
    )
    ev = _make_ev(run_id, sample_provenance, source="PMID:11111", verdict="uncertain", scope="abstract")
    out = await workflow._enrich_uncertain_with_full_text([ev], force=False)

    mock_fetch.assert_awaited_once_with("11111", with_content=True)
    assert len(out) == 1
    updated = out[0]
    assert updated.scope == "full_text"
    assert updated.artifact_uri == "https://pmc.ncbi.nlm.nih.gov/9999999"
    assert updated.extra["full_text"] == "Detailed body prose about TRPC6."
    assert updated.extra["full_text_url"] == "https://pmc.ncbi.nlm.nih.gov/9999999"
    # Original screening_verdict and other extra fields are preserved, not clobbered.
    assert updated.extra["screening_verdict"]["verdict"] == "uncertain"
    assert updated.extra["title"] == "Test title"


async def test_enrich_leaves_unchanged_when_fetch_returns_no_content(run_id, sample_provenance, mock_fetch):
    mock_fetch.return_value = PubMedFullText(pmid="11111", available=False)
    ev = _make_ev(run_id, sample_provenance, source="PMID:11111", verdict="uncertain", scope="abstract")
    out = await workflow._enrich_uncertain_with_full_text([ev], force=False)

    assert len(out) == 1
    assert out[0].scope == "abstract"
    assert out[0].extra.get("full_text") is None


async def test_enrich_leaves_unchanged_on_fetch_exception(run_id, sample_provenance, mock_fetch):
    mock_fetch.side_effect = RuntimeError("network boom")
    ev = _make_ev(run_id, sample_provenance, source="PMID:11111", verdict="uncertain", scope="abstract")
    out = await workflow._enrich_uncertain_with_full_text([ev], force=False)

    assert len(out) == 1
    assert out[0].scope == "abstract"


async def test_enrich_skips_refetch_when_already_full_text_unless_forced(run_id, sample_provenance, mock_fetch):
    ev = _make_ev(
        run_id,
        sample_provenance,
        source="PMID:11111",
        verdict="uncertain",
        scope="full_text",
        extra={"full_text": "Already fetched body."},
    )

    out = await workflow._enrich_uncertain_with_full_text([ev], force=False)
    assert out == [ev]
    mock_fetch.assert_not_awaited()

    mock_fetch.return_value = PubMedFullText(
        pmid="11111", available=True, full_text="Refetched body.", full_text_url="https://pmc.ncbi.nlm.nih.gov/x"
    )
    out_forced = await workflow._enrich_uncertain_with_full_text([ev], force=True)
    mock_fetch.assert_awaited_once_with("11111", with_content=True)
    assert out_forced[0].extra["full_text"] == "Refetched body."
