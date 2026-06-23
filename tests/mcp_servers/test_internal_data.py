# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for internal_data MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.exceptions import MCPToolError
from mcp_servers.internal_data.tools import _tag_sensitive, query_internal_db
from schemas.evidence import DataClass


def test_tag_sensitive_injects_classification() -> None:
    rows = [{"gene": "BRCA1", "score": 0.9}]
    tagged = _tag_sensitive(rows)
    assert tagged[0]["_classification"] == DataClass.SENSITIVE.value


def test_tag_sensitive_preserves_original_fields() -> None:
    rows = [{"gene": "BRCA1", "expression_tpm": 42.5}]
    tagged = _tag_sensitive(rows)
    assert tagged[0]["gene"] == "BRCA1"
    assert tagged[0]["expression_tpm"] == 42.5


async def test_query_internal_db_rejects_non_select() -> None:
    with pytest.raises(MCPToolError, match="Only SELECT"):
        await query_internal_db("DELETE FROM runs")


async def test_query_internal_db_rejects_insert() -> None:
    with pytest.raises(MCPToolError):
        await query_internal_db("INSERT INTO runs VALUES (1)")


async def test_query_internal_db_returns_sensitive_rows() -> None:
    fake_row = MagicMock()
    fake_row._mapping = {"gene": "BRCA1", "gwas_pval": 1e-9}

    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([fake_row]))

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("mcp_servers.internal_data.tools.get_session", return_value=mock_session):
        rows = await query_internal_db("SELECT gene, gwas_pval FROM gwas_hits")

    assert len(rows) == 1
    assert rows[0]["_classification"] == DataClass.SENSITIVE.value
    assert rows[0]["gene"] == "BRCA1"


async def test_query_internal_db_redacts_pii_when_enabled(monkeypatch) -> None:
    import mcp_servers.internal_data.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_REDACT", True)

    fake_row = MagicMock()
    fake_row._mapping = {"gene": "BRCA1", "patient_id": "P001", "gwas_pval": 1e-9}

    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([fake_row]))

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("mcp_servers.internal_data.tools.get_session", return_value=mock_session):
        rows = await query_internal_db("SELECT gene, patient_id FROM gwas_hits")

    assert rows[0]["patient_id"] == "***REDACTED***"
    assert rows[0]["gene"] == "BRCA1"
