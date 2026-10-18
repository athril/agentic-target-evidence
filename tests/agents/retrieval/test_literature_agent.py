# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for LiteratureAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.retrieval.literature.agent import LiteratureAgent, _build_base_query, _widen
from mcp_servers.pubmed.tools import PubMedRecord
from schemas.evidence import DataClass, EvidenceType
from tests.agents.conftest import make_task_msg


def _make_records(n: int) -> list[PubMedRecord]:
    return [
        PubMedRecord(pmid=str(i), title=f"Title {i}", journal="Nature", pub_year=2022)
        for i in range(n)
    ]


async def test_literature_agent_returns_evidence_list(run_id, trace_id, ctx):
    msg = make_task_msg(
        "literature", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with (
        patch(
            "agents.retrieval.literature.agent.search_pubmed",
            AsyncMock(return_value=_make_records(50)),
        ),
        patch(
            "agents.retrieval.literature.agent.resolve_mesh_term",
            AsyncMock(return_value="breast neoplasms"),
        ),
    ):
        agent = LiteratureAgent()
        result = await agent.run(msg, ctx)

    assert result.intent == "result"
    assert isinstance(result.payload, list)
    assert len(result.payload) == 50
    assert all(e.evidence_type == EvidenceType.ARTICLE for e in result.payload)
    assert all(e.classification == DataClass.NON_SENSITIVE for e in result.payload)
    assert all(e.scope == "abstract" for e in result.payload)


async def test_literature_agent_narrows_when_too_many_results(run_id, trace_id, ctx):
    msg = make_task_msg(
        "literature", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    # First call: 1001 results → narrow; second call: 200 results → accept
    call_count = 0

    async def mock_search(query, max_results=500):
        nonlocal call_count
        call_count += 1
        return _make_records(1001 if call_count == 1 else 200)

    with (
        patch("agents.retrieval.literature.agent.search_pubmed", mock_search),
        patch(
            "agents.retrieval.literature.agent.resolve_mesh_term",
            AsyncMock(return_value="breast neoplasms"),
        ),
    ):
        agent = LiteratureAgent()
        result = await agent.run(msg, ctx)

    assert call_count == 2
    assert len(result.payload) == 200


async def test_literature_agent_widens_when_too_few_results(run_id, trace_id, ctx):
    msg = make_task_msg(
        "literature", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )
    call_count = 0

    async def mock_search(query, max_results=500):
        nonlocal call_count
        call_count += 1
        return _make_records(5 if call_count == 1 else 30)

    with (
        patch("agents.retrieval.literature.agent.search_pubmed", mock_search),
        patch(
            "agents.retrieval.literature.agent.resolve_mesh_term",
            AsyncMock(return_value="breast neoplasms"),
        ),
    ):
        agent = LiteratureAgent()
        result = await agent.run(msg, ctx)

    assert call_count == 2
    assert len(result.payload) == 30


async def test_literature_agent_uses_custom_query(run_id, trace_id, ctx):
    custom = "BRCA1[tiab] AND breast[tiab]"
    msg = make_task_msg(
        "literature",
        {"target_gene": "BRCA1", "disease": "breast cancer", "query": custom},
        run_id,
        trace_id,
    )
    captured_queries: list[str] = []

    async def mock_search(query, max_results=500):
        captured_queries.append(query)
        return _make_records(50)

    with patch("agents.retrieval.literature.agent.search_pubmed", mock_search):
        agent = LiteratureAgent()
        await agent.run(msg, ctx)

    assert captured_queries[0] == custom


def test_build_base_query_includes_gene_and_disease():
    q = _build_base_query("BRCA1", "breast cancer", None)
    assert '"BRCA1"[tiab]' in q
    assert "breast cancer" in q
    assert "english" in q


def test_build_base_query_uses_resolved_mesh_descriptor():
    """Regression: with a resolved MeSH descriptor the disease clause must OR the
    canonical "[MeSH Terms]" heading with the free-text "[tiab]" label. Before
    the fix the query used the unresolved label as a "[MeSH Terms]" phrase, which
    NCBI silently dropped, leaving an exact "[tiab]" phrase that matched nothing."""
    q = _build_base_query("PRMT5", "pancreatic neoplasm", None, disease_mesh="pancreatic neoplasms")
    assert '"pancreatic neoplasms"[MeSH Terms]' in q
    assert '"pancreatic neoplasm"[tiab]' in q
    # the unresolved label must never be used as a MeSH phrase
    assert '"pancreatic neoplasm"[MeSH Terms]' not in q


def test_build_base_query_falls_back_to_atm_without_mesh():
    """When no MeSH descriptor resolves, fall back to an untagged ATM term rather
    than a bare exact-phrase "[tiab]" clause (which matches almost nothing)."""
    q = _build_base_query("PRMT5", "some rare phenotype", None, disease_mesh=None)
    assert "(some rare phenotype)" in q
    assert '"some rare phenotype"[tiab]' not in q


def test_build_base_query_includes_population_mesh():
    q = _build_base_query(
        "BRCA1",
        "breast cancer",
        "child",
        disease_mesh="breast neoplasms",
        population_mesh="child",
    )
    assert '"child"[MeSH Terms]' in q


def test_widen_uses_mesh_and_drops_scope():
    q = _widen("PRMT5", "pancreatic neoplasm", disease_mesh="pancreatic neoplasms")
    assert '"PRMT5"[tiab]' in q
    assert '"pancreatic neoplasms"[MeSH Terms]' in q
    # scope (article type / date window) must be dropped so widen can escape a
    # low-result base query
    assert "journal article" not in q
    assert "pdat" not in q


async def test_literature_agent_resolves_mesh_for_query(run_id, trace_id, ctx):
    """The agent resolves the disease to a MeSH descriptor and builds the query
    around it (no custom query supplied)."""
    msg = make_task_msg(
        "literature", {"target_gene": "PRMT5", "disease": "pancreatic neoplasm"}, run_id, trace_id
    )
    captured: list[str] = []

    async def mock_search(query, max_results=500):
        captured.append(query)
        return _make_records(50)

    with (
        patch("agents.retrieval.literature.agent.search_pubmed", mock_search),
        patch(
            "agents.retrieval.literature.agent.resolve_mesh_term",
            AsyncMock(return_value="pancreatic neoplasms"),
        ),
    ):
        agent = LiteratureAgent()
        await agent.run(msg, ctx)

    assert '"pancreatic neoplasms"[MeSH Terms]' in captured[0]


@pytest.mark.smoke
async def test_literature_base_query_returns_results_live():
    """Live guard: the exact bug case (PRMT5 / pancreatic neoplasm) must return
    real PubMed hits. Excluded from the default unit run via the smoke marker."""
    from mcp_servers.pubmed.tools import resolve_mesh_term, search_pubmed

    mesh = await resolve_mesh_term("pancreatic neoplasm")
    query = _build_base_query("PRMT5", "pancreatic neoplasm", None, disease_mesh=mesh)
    records = await search_pubmed(query)
    assert len(records) > 0


def _row(pmid: str, extra: dict | None = None):
    import uuid
    from datetime import UTC, datetime

    from schemas.evidence import Evidence, Provenance

    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="DISEASE-A",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source=f"PMID:{pmid}",
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="literature", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
        ),
        extra=extra or {"pmid": pmid},
    )


async def test_cached_rows_without_metadata_get_it_and_fresh_rows_are_untouched():
    from agents.retrieval.literature.agent import with_article_metadata
    from mcp_servers.pubmed.tools import ArticleMetadata, MeshHeading

    stale, gone = _row("1"), _row("2")
    fresh = _row("3", {"pmid": "3", "publication_types": ["Review"], "mesh_headings": []})
    fetched = {
        "1": ArticleMetadata(
            publication_types=["Meta-Analysis"],
            mesh_headings=[MeshHeading(descriptor="Disease A")],
            indexed=True,
        )
    }
    fetch = AsyncMock(return_value=fetched)
    with patch("agents.retrieval.literature.agent.fetch_article_metadata", fetch):
        rows, changed = await with_article_metadata([stale, gone, fresh])

    fetch.assert_awaited_once_with(["1", "2"])
    assert changed == 2
    assert rows[0].extra["publication_types"] == ["Meta-Analysis"]
    assert rows[0].extra["mesh_headings"][0]["descriptor"] == "Disease A"
    assert rows[0].extra["mesh_indexed"] is True
    # A PMID efetch no longer returns gets empty metadata, so it is not refetched forever.
    assert rows[1].extra["publication_types"] == []
    assert rows[2] is fresh
    assert [r.evidence_id for r in rows] == [stale.evidence_id, gone.evidence_id, fresh.evidence_id]


async def test_a_failed_metadata_fetch_leaves_cached_rows_as_they_were():
    from agents.retrieval.literature.agent import with_article_metadata

    rows_in = [_row("1")]
    with patch(
        "agents.retrieval.literature.agent.fetch_article_metadata", AsyncMock(return_value={})
    ):
        rows, changed = await with_article_metadata(rows_in)

    assert changed == 0
    assert rows == rows_in


async def test_retrieved_rows_carry_publication_types_and_mesh(run_id, trace_id, ctx):
    from mcp_servers.pubmed.tools import MeshHeading

    record = PubMedRecord(
        pmid="7",
        title="T",
        publication_types=["Randomized Controlled Trial"],
        mesh_headings=[MeshHeading(descriptor="Humans")],
        indexed=True,
    )
    msg = make_task_msg(
        "literature", {"target_gene": "GENE-A", "disease": "disease a"}, run_id, trace_id
    )
    with (
        patch(
            "agents.retrieval.literature.agent.search_pubmed",
            AsyncMock(return_value=[record] * 30),
        ),
        patch("agents.retrieval.literature.agent.resolve_mesh_term", AsyncMock(return_value=None)),
        patch("agents.retrieval.literature.agent.known_drug_names", AsyncMock(return_value=[])),
    ):
        result = await LiteratureAgent().run(msg, ctx)

    extra = result.payload[0].extra
    assert extra["publication_types"] == ["Randomized Controlled Trial"]
    assert extra["mesh_headings"][0]["descriptor"] == "Humans"
    assert extra["retracted"] is False
