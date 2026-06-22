# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""TTD FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import TtdBundle
from .tools import get_ttd_target_status as _get_ttd_target_status

mcp = FastMCP("ttd")


@mcp.tool(name="ttd_get_target_status")
async def get_ttd_target_status(gene_symbol: str) -> TtdBundle:
    """Fetch TTD target development-status + mapped drugs for a gene (no-op if TTD_ENABLED unset)."""
    return await _get_ttd_target_status(gene_symbol)


if __name__ == "__main__":
    mcp.run()
