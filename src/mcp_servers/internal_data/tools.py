# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Internal data MCP tools.

All outputs from this server are automatically classified SENSITIVE.
The server name is registered as "internal_data" so the classifier in
src/core/routing/classify.py catches it without extra configuration.

Field-level redaction is a no-op by default; enable via INTERNAL_DATA_REDACT=true.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from core.exceptions import MCPToolError
from core.persistence.db import get_session
from schemas.evidence import DataClass

_log = logging.getLogger(__name__)

_REDACT = os.environ.get("INTERNAL_DATA_REDACT", "false").lower() == "true"

# Fields to redact when INTERNAL_DATA_REDACT=true
_REDACTED_FIELDS = {"patient_id", "subject_id", "sample_id", "dob", "mrn"}


def _redact(row: dict[str, Any]) -> dict[str, Any]:
    if not _REDACT:
        return row
    return {k: ("***REDACTED***" if k in _REDACTED_FIELDS else v) for k, v in row.items()}


def _tag_sensitive(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inject _classification into every row so consumers know this is SENSITIVE."""
    return [{**_redact(r), "_classification": DataClass.SENSITIVE.value} for r in rows]


async def query_internal_db(sql: str) -> list[dict[str, Any]]:
    """Execute a read-only SQL query against the internal Postgres database.

    All results are tagged SENSITIVE.  Only SELECT statements are accepted;
    any mutation attempt raises MCPToolError.
    """
    normalised = sql.strip().upper()
    if not normalised.startswith("SELECT"):
        raise MCPToolError(
            "Only SELECT queries are allowed via the internal_data MCP server. "
            f"Received: {sql[:80]!r}"
        )

    try:
        async with get_session() as session:
            result = await session.execute(__import__("sqlalchemy").text(sql))
            rows = [dict(row._mapping) for row in result]
    except Exception as exc:
        # Internal DB may be absent in dev / smoke-test environments.
        # Return empty rather than crashing the pipeline — callers treat
        # an empty list as "no internal evidence available".
        _log.warning("internal_data query unavailable (%s): %s", type(exc).__name__, exc)
        return []

    return _tag_sensitive(rows)
