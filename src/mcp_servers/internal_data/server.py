# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Internal data FastMCP server.

Registered as "internal_data" — the classifier in classify.py uses this
name to auto-tag all outputs SENSITIVE without inspecting the payload.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .tools import query_internal_db as _query_internal_db

mcp = FastMCP("internal_data")


@mcp.tool()
async def query_internal_db(sql: str) -> list[dict[str, Any]]:
    """Execute a SELECT query against the internal Postgres database.

    All results are automatically tagged _classification=SENSITIVE.
    Only read-only SELECT statements are accepted.
    """
    return await _query_internal_db(sql)


if __name__ == "__main__":
    mcp.run()
