# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalTrials.gov FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ConditionTrialLandscape, TrialRecord
from .tools import count_condition_trials as _count_condition_trials
from .tools import search_trials as _search_trials

mcp = FastMCP("clinicaltrials")


@mcp.tool(name="clinicaltrials_search_trials")
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


@mcp.tool(name="clinicaltrials_count_condition_trials")
async def count_condition_trials(condition: str) -> ConditionTrialLandscape:
    """Count trials for a disease/condition regardless of target gene or intervention.

    Target-agnostic disease-level trial landscape (contrast with
    `clinicaltrials_search_trials`, which is gene-keyed). Uses count-only queries
    so a broad condition search never pages thousands of full study records.
    """
    return await _count_condition_trials(condition)


if __name__ == "__main__":
    mcp.run()
