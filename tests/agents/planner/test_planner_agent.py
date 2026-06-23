# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Planner REST API.

Targets the live app in ``agents.planner.main`` (the former ``agent.create_app``
factory was removed as dead code — see docs/new/dead_code_report.md A9). The app
reads module-level globals populated during lifespan startup; the ``planner_app``
fixture injects mocks for those globals directly instead of running the lifespan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import agents.planner.agent as agent_module
import agents.planner.main as main_module
from agents.planner.agent import RunRequest, _make_initial_state
from core.exceptions import MCPToolError
from mcp_servers.ontology.tools import HGNCResult, MondoResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_ontology_resolution():
    """Unit tests must not hit the live HGNC/EBI OLS APIs.

    ``_resolve_ontology_context`` lives in ``agents.planner.agent`` and calls these
    names from that module's namespace, so they are patched there. Defaults to
    failure (a resolution miss) so resolved_context stays empty unless a test
    overrides them to exercise the success path.
    """
    with (
        patch.object(
            agent_module,
            "resolve_hgnc_symbol",
            new=AsyncMock(side_effect=MCPToolError("not mocked")),
        ),
        patch.object(
            agent_module,
            "resolve_mondo_term",
            new=AsyncMock(side_effect=MCPToolError("not mocked")),
        ),
    ):
        yield


@pytest.fixture()
def mock_graph():
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=None)
    graph.aget_state = AsyncMock(return_value=None)
    graph.aupdate_state = AsyncMock(return_value=None)
    return graph


@pytest.fixture()
def mock_run_repo():
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.update_status = AsyncMock()
    repo.get = AsyncMock()
    repo.increment_rerun_count = AsyncMock()
    return repo


@pytest.fixture()
def planner_app(mock_graph, mock_run_repo, monkeypatch):
    """The live ``main.app`` with its lifespan-populated globals replaced by mocks.

    httpx's ASGITransport does not run the lifespan, so the globals would be None;
    we inject them here. ``resolve_gene``/``resolve_disease`` are imported into the
    ``main`` namespace, so they default to a miss unless a test overrides them.
    """
    monkeypatch.setattr(main_module, "_graph", mock_graph)
    monkeypatch.setattr(main_module, "_run_repo", mock_run_repo)
    monkeypatch.setattr(main_module, "_default_model_fp", "")
    monkeypatch.setattr(
        main_module, "resolve_gene", AsyncMock(side_effect=Exception("no hit"))
    )
    monkeypatch.setattr(
        main_module, "resolve_disease", AsyncMock(side_effect=Exception("no hit"))
    )
    return main_module.app


def _make_run(run_id, status="running"):
    return SimpleNamespace(
        id=run_id,
        status=status,
        step_budget_consumed=0,
        rerun_count=0,
        created_at=datetime(2026, 6, 11, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------


async def test_create_run_returns_202_with_run_id(planner_app, mock_run_repo):
    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/runs",
            json={"target_gene": "BRCA1", "disease": "breast cancer"},
        )

    assert response.status_code == 202
    data = response.json()
    assert "run_id" in data
    assert data["status"] == "pending"
    uuid.UUID(data["run_id"])  # must be valid UUID


async def test_create_run_calls_repo_create(planner_app, mock_run_repo):
    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        await client.post("/runs", json={"target_gene": "BRCA1", "disease": "breast cancer"})

    mock_run_repo.create.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------


async def test_get_run_returns_status(planner_app, mock_run_repo):
    run_id = uuid.uuid4()
    mock_run_repo.get = AsyncMock(return_value=_make_run(run_id, status="running"))

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["run_id"] == str(run_id)


async def test_get_run_returns_404_for_unknown(planner_app, mock_run_repo):
    mock_run_repo.get = AsyncMock(return_value=None)
    run_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/runs/{run_id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/hitl
# ---------------------------------------------------------------------------


async def test_get_hitl_returns_screened_evidence(planner_app, mock_graph, run_id, trace_id):
    from tests.agents.conftest import make_evidence

    ev = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "keep"}})

    snapshot = SimpleNamespace(values={"screened_evidence": [ev]}, next=["hitl_gate"])
    mock_graph.aget_state = AsyncMock(return_value=snapshot)

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/runs/{run_id}/hitl")

    assert response.status_code == 200
    data = response.json()
    assert len(data["screened_evidence"]) == 1
    assert str(ev.evidence_id) in data["verdicts"]


async def test_get_hitl_returns_404_when_no_state(planner_app, mock_graph):
    mock_graph.aget_state = AsyncMock(return_value=None)
    run_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/runs/{run_id}/hitl")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/hitl/approve
# ---------------------------------------------------------------------------


async def test_approve_hitl_updates_state_and_returns_resumed(
    planner_app, mock_graph, mock_run_repo
):
    run_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/runs/{run_id}/hitl/approve",
            json={"overrides": {str(uuid.uuid4()): True}},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "resumed"
    mock_graph.aupdate_state.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/report
# ---------------------------------------------------------------------------


async def test_get_report_returns_404_when_not_ready(planner_app, mock_graph):
    snapshot = SimpleNamespace(values={"report_uri": None}, next=[])
    mock_graph.aget_state = AsyncMock(return_value=snapshot)
    run_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/runs/{run_id}/report")

    assert response.status_code == 404


async def test_get_report_returns_uri_when_ready(planner_app, mock_graph, tmp_path):
    report_file = tmp_path / "report.md"
    report_file.write_text("# Report\nContent here.")
    report_uri = str(report_file)

    snapshot = SimpleNamespace(values={"report_uri": report_uri}, next=[])
    mock_graph.aget_state = AsyncMock(return_value=snapshot)
    run_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/runs/{run_id}/report")

    assert response.status_code == 200
    data = response.json()
    assert data["report_uri"] == report_uri
    assert "# Report" in data["content_md"]


# ---------------------------------------------------------------------------
# _make_initial_state helper
# ---------------------------------------------------------------------------


def test_make_initial_state_seeds_all_fields():
    req = RunRequest(target_gene="TP53", disease="lung cancer", step_budget=150)
    run_id = uuid.uuid4()
    state = _make_initial_state(run_id, req)

    assert state["run_id"] == run_id
    assert state["target_gene"] == "TP53"
    assert state["step_budget_remaining"] == 150
    assert state["hitl_approved"] is False
    assert state["literature_evidence"] == []
    assert state["messages"] == []
    assert state["resolved_context"] == {}


# ---------------------------------------------------------------------------
# Ontology enrichment (HGNC/MONDO) — _resolve_ontology_context via /runs
# ---------------------------------------------------------------------------


async def test_create_run_populates_resolved_context_on_success(
    planner_app, mock_run_repo, mock_graph
):
    hgnc_mock = AsyncMock(
        return_value=HGNCResult(
            symbol="PRMT5",
            hgnc_id="HGNC:17353",
            ensembl_gene_id="ENSG00000100462",
            aliases=["SKB1", "IBP72"],
        )
    )
    mondo_mock = AsyncMock(
        return_value=MondoResult(
            mondo_id="MONDO:0008170",
            label="pancreatic cancer",
            xrefs={"efo": "EFO_0002618", "omim": "260350"},
        )
    )
    with (
        patch.object(agent_module, "resolve_hgnc_symbol", new=hgnc_mock),
        patch.object(agent_module, "resolve_mondo_term", new=mondo_mock),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=planner_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/runs",
                json={"target_gene": "PRMT5", "disease": "pancreatic cancer"},
            )

    assert response.status_code == 202
    hgnc_mock.assert_awaited_once_with("PRMT5")
    mondo_mock.assert_awaited_once_with("pancreatic cancer")
    initial_state = mock_graph.ainvoke.await_args.args[0]
    assert initial_state["resolved_context"]["hgnc_symbol"] == "PRMT5"
    assert initial_state["resolved_context"]["gene_aliases"] == ["SKB1", "IBP72"]
    assert initial_state["resolved_context"]["mondo_id"] == "MONDO:0008170"
    assert initial_state["resolved_context"]["efo_id"] == "EFO_0002618"
    assert initial_state["resolved_context"]["omim_xref"] == "260350"


async def test_create_run_resolved_context_empty_on_ontology_failure(
    planner_app, mock_run_repo, mock_graph
):
    """Default autouse mocks fail both lookups — resolved_context stays empty, run still succeeds."""
    async with AsyncClient(
        transport=ASGITransport(app=planner_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/runs",
            json={"target_gene": "PRMT5", "disease": "pancreatic cancer"},
        )

    assert response.status_code == 202
    initial_state = mock_graph.ainvoke.await_args.args[0]
    assert initial_state["resolved_context"] == {}
