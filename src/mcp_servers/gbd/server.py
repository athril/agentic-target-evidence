# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GBD (Global Burden of Disease, IHME) FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import GBDBundle
from .tools import get_disease_burden as _get_disease_burden

mcp = FastMCP("gbd")


@mcp.tool(name="gbd_get_disease_burden")
async def get_disease_burden(disease: str, disease_id: str = "") -> GBDBundle:
    """Fetch GBD prevalence/incidence burden for a disease (no-op if GBD_ENABLED is false)."""
    return await _get_disease_burden(disease, disease_id=disease_id)


if __name__ == "__main__":
    mcp.run()
