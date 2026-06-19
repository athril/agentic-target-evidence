# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalTrials.gov FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import TrialRecord
from .tools import search_trials as _search_trials

mcp = FastMCP("clinicaltrials")


@mcp.tool()
async def search_trials(
    gene: str,
    disease: str,
    population: str | None = None,
) -> list[TrialRecord]:
    """Search ClinicalTrials.gov v2 API for studies involving the gene and disease.

    Records without posted results have scope='abstract'; those with results
    have scope='full_text'.
    """
    return await _search_trials(gene, disease, population)


if __name__ == "__main__":
    mcp.run()
