# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for KnowledgeExtractionAgent (MP-37)."""

from __future__ import annotations

import uuid
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.screening.knowledge_extraction.agent import (
    KnowledgeExtractionAgent,
    _chunk,
    _needs_rescreen,
)
from mcp_servers.pubmed.tools import PubMedAbstract, PubMedFullText
from schemas.evidence import DataClass, Evidence
from tests.agents.conftest import make_evidence, make_task_msg

# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_chunk_splits_long_text():
    text = "A" * 5000
    chunks = _chunk(text)
    assert len(chunks) == 3  # ceil(5000/2000) = 3
    assert all(len(c) <= 2000 for c in chunks)


def test_chunk_returns_single_for_short_text():
    assert len(_chunk("short")) == 1


def test_needs_rescreen_detects_retracted():
    assert _needs_rescreen("This paper was retracted due to data manipulation.")


def test_needs_rescreen_false_for_clean_text():
    assert not _needs_rescreen("This is a high-quality clinical trial on BRCA1.")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embed_provider():
    p = MagicMock()
    p.embed = AsyncMock(return_value=[[0.1] * 768])
    return p


@pytest.fixture()
def ke_ctx(run_id, trace_id, embed_provider):
    router = MagicMock()
    router.select.return_value = embed_provider
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router)


@pytest.fixture()
def mock_db():
    """Return (mock_session, mock_repo) with async context manager wired up."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock()
    repo.upsert = AsyncMock()
    repo.update_embedding = AsyncMock()
    return session, repo


_ABSTRACT = PubMedAbstract(
    pmid="99999",
    title="BRCA1 and breast cancer risk",
    abstract="This study examines BRCA1 loss-of-function variants in breast cancer.",
)

_ABSTRACT_NO_FT = PubMedAbstract(
    pmid="88888",
    title="EGFR signalling in NSCLC",
    abstract="EGFR activating mutations drive tumour growth.",
)

_FT_AVAILABLE = PubMedFullText(
    pmid="99999", pmc_id="PMC1", full_text_url="https://pmc.example/99999", available=True
)
_FT_UNAVAILABLE = PubMedFullText(pmid="88888", available=False)


# ---------------------------------------------------------------------------
# KnowledgeExtractionAgent integration tests
# ---------------------------------------------------------------------------


async def test_ke_agent_embeds_abstract_and_upgrades_scope_when_pmc_available(
    run_id, trace_id, ke_ctx, embed_provider, mock_db
):
    """PMID source with PMC full text: embeds the abstract, upgrades scope."""
    mock_session, mock_repo = mock_db
    ev = make_evidence(
        run_id,
        trace_id,
        source="PMID:99999",
        extra={"screening_verdict": {"verdict": "keep"}},
    )
    msg = make_task_msg(
        "knowledge_extraction",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    with (
        patch(
            "agents.screening.knowledge_extraction.agent.fetch_abstract",
            AsyncMock(return_value=_ABSTRACT),
        ),
        patch(
            "agents.screening.knowledge_extraction.agent.fetch_full_text",
            AsyncMock(return_value=_FT_AVAILABLE),
        ),
        patch("agents.screening.knowledge_extraction.agent.get_session", return_value=mock_session),
        patch(
            "agents.screening.knowledge_extraction.agent.EvidenceRepository", return_value=mock_repo
        ),
    ):
        agent = KnowledgeExtractionAgent(embed_provider=embed_provider)
        result = await agent.run(msg, ke_ctx)

    updated = result.payload[0]
    assert updated.scope == "full_text"
    assert updated.artifact_uri == "https://pmc.example/99999"
    mock_repo.upsert.assert_awaited_once()
    mock_repo.update_embedding.assert_awaited_once()
    # embed() must receive actual text, not a URL placeholder
    call_args = embed_provider.embed.call_args[0][0]
    assert "BRCA1" in call_args[0]


async def test_ke_agent_embeds_abstract_when_no_pmc_full_text(
    run_id, trace_id, ke_ctx, embed_provider, mock_db
):
    """PMID source without PMC full text: still embeds the abstract; scope stays 'abstract'."""
    mock_session, mock_repo = mock_db
    ev = make_evidence(
        run_id,
        trace_id,
        source="PMID:88888",
        extra={"screening_verdict": {"verdict": "keep"}},
    )
    msg = make_task_msg(
        "knowledge_extraction",
        {"target_gene": "EGFR", "disease": "NSCLC"},
        run_id,
        trace_id,
        payload=[ev],
    )

    with (
        patch(
            "agents.screening.knowledge_extraction.agent.fetch_abstract",
            AsyncMock(return_value=_ABSTRACT_NO_FT),
        ),
        patch(
            "agents.screening.knowledge_extraction.agent.fetch_full_text",
            AsyncMock(return_value=_FT_UNAVAILABLE),
        ),
        patch("agents.screening.knowledge_extraction.agent.get_session", return_value=mock_session),
        patch(
            "agents.screening.knowledge_extraction.agent.EvidenceRepository", return_value=mock_repo
        ),
    ):
        agent = KnowledgeExtractionAgent(embed_provider=embed_provider)
        result = await agent.run(msg, ke_ctx)

    updated = result.payload[0]
    assert updated.scope == "abstract"  # not upgraded
    assert updated.artifact_uri is None  # no PMC URL stored
    mock_repo.upsert.assert_awaited_once()  # embedding still written
    mock_repo.update_embedding.assert_awaited_once()


async def test_ke_agent_embeds_claim_text_for_non_pmid_source(
    run_id, trace_id, ke_ctx, embed_provider, mock_db
):
    """Non-PMID source with claim_text: embeds claim_text directly."""
    from datetime import datetime

    from schemas.evidence import EvidenceType, Provenance

    mock_session, mock_repo = mock_db
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        target_gene="EGFR",
        disease="NSCLC",
        evidence_type=EvidenceType.PATENT,
        scope="abstract",
        source="US10000001",
        source_link="https://patents.google.com/patent/US10000001",
        classification=DataClass.NON_SENSITIVE,
        claim_text="EGFR inhibitor compound with improved selectivity.",
        provenance=Provenance(
            agent_name="test",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            trace_id=trace_id,
        ),
        extra={"screening_verdict": {"verdict": "keep"}},
    )
    msg = make_task_msg(
        "knowledge_extraction",
        {"target_gene": "EGFR", "disease": "NSCLC"},
        run_id,
        trace_id,
        payload=[ev],
    )

    with (
        patch("agents.screening.knowledge_extraction.agent.get_session", return_value=mock_session),
        patch(
            "agents.screening.knowledge_extraction.agent.EvidenceRepository", return_value=mock_repo
        ),
    ):
        agent = KnowledgeExtractionAgent(embed_provider=embed_provider)
        await agent.run(msg, ke_ctx)

    mock_repo.upsert.assert_awaited_once()
    mock_repo.update_embedding.assert_awaited_once()
    call_args = embed_provider.embed.call_args[0][0]
    assert "EGFR inhibitor" in call_args[0]


async def test_ke_agent_skips_non_pmid_without_claim_text(
    run_id, trace_id, ke_ctx, embed_provider, mock_db
):
    """Non-PMID source with no claim_text: skipped (nothing to embed)."""
    mock_session, mock_repo = mock_db
    ev = make_evidence(
        run_id,
        trace_id,
        source="NCT00000001",
        extra={"screening_verdict": {"verdict": "keep"}},
    )
    # make_evidence leaves claim_text="" by default
    assert ev.claim_text == ""

    msg = make_task_msg(
        "knowledge_extraction",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    with (
        patch("agents.screening.knowledge_extraction.agent.get_session", return_value=mock_session),
        patch(
            "agents.screening.knowledge_extraction.agent.EvidenceRepository", return_value=mock_repo
        ),
    ):
        agent = KnowledgeExtractionAgent(embed_provider=embed_provider)
        result = await agent.run(msg, ke_ctx)

    mock_repo.upsert.assert_not_awaited()
    assert result.payload[0].scope == "abstract"  # unchanged


async def test_ke_agent_flags_retracted_as_uncertain(
    run_id, trace_id, ke_ctx, embed_provider, mock_db
):
    mock_session, mock_repo = mock_db
    ev = make_evidence(
        run_id,
        trace_id,
        source="PMID:77777",
        extra={"screening_verdict": {"verdict": "keep"}},
    )
    msg = make_task_msg(
        "knowledge_extraction",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    retracted_abstract = PubMedAbstract(
        pmid="77777",
        title="Retracted: BRCA1 study",
        abstract="This article has been retracted by the authors.",
    )

    with (
        patch(
            "agents.screening.knowledge_extraction.agent.fetch_abstract",
            AsyncMock(return_value=retracted_abstract),
        ),
        patch(
            "agents.screening.knowledge_extraction.agent.fetch_full_text",
            AsyncMock(return_value=PubMedFullText(pmid="77777", available=False)),
        ),
        patch("agents.screening.knowledge_extraction.agent.get_session", return_value=mock_session),
        patch(
            "agents.screening.knowledge_extraction.agent.EvidenceRepository", return_value=mock_repo
        ),
    ):
        agent = KnowledgeExtractionAgent(embed_provider=embed_provider)
        result = await agent.run(msg, ke_ctx)

    updated = result.payload[0]
    assert updated.extra["screening_verdict"]["verdict"] == "uncertain"


async def test_ke_agent_passthrough_when_payload_not_list(run_id, trace_id, ke_ctx, embed_provider):
    msg = make_task_msg(
        "knowledge_extraction",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=None,
    )
    agent = KnowledgeExtractionAgent(embed_provider=embed_provider)
    result = await agent.run(msg, ke_ctx)
    assert result.intent == "result"
    assert result.payload is None
