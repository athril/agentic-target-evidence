# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenFDA FastMCP server — drug labels (SPL) and adverse event reports (FAERS)."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import AdverseEventBundle, DrugLabelRecord
from .tools import search_adverse_events as _search_adverse_events
from .tools import search_drug_labels as _search_drug_labels

mcp = FastMCP("openfda")


@mcp.tool()
async def search_drug_labels(gene_symbol: str, indication: str) -> list[DrugLabelRecord]:
    """Search FDA drug labels for drugs mentioning the gene in mechanism of action
    or approved for the given indication.

    Returns up to 20 deduplicated label records (NON_SENSITIVE, public API).
    """
    return await _search_drug_labels(gene_symbol, indication)


@mcp.tool()
async def search_adverse_events(drug_name: str) -> AdverseEventBundle:
    """Fetch FAERS adverse event summary for a drug name.

    Returns total, serious, and death report counts plus the top 25 reactions
    by frequency. FAERS has significant noise — treat as signal-generating, not
    ground truth (underreporting, confounders, duplicate submissions).
    """
    return await _search_adverse_events(drug_name)


if __name__ == "__main__":
    mcp.run()
