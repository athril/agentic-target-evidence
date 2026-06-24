# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenFDA FastMCP server — drug labels (SPL) and adverse event reports (FAERS)."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import AdverseEventBundle, DrugLabelRecord, IndicationDrugLandscape
from .tools import count_indication_drugs as _count_indication_drugs
from .tools import search_adverse_events as _search_adverse_events
from .tools import search_drug_labels as _search_drug_labels

mcp = FastMCP("openfda")


@mcp.tool(name="openfda_search_drug_labels")
async def search_drug_labels(gene_symbol: str, indication: str) -> list[DrugLabelRecord]:
    """Search FDA drug labels for drugs mentioning the gene in mechanism of action
    or approved for the given indication.

    Returns up to 20 deduplicated label records (NON_SENSITIVE, public API).
    """
    return await _search_drug_labels(gene_symbol, indication)


@mcp.tool(name="openfda_search_adverse_events")
async def search_adverse_events(drug_name: str) -> AdverseEventBundle:
    """Fetch FAERS adverse event summary for a drug name.

    Returns total, serious, and death report counts plus the top 25 reactions
    by frequency. FAERS has significant noise — treat as signal-generating, not
    ground truth (underreporting, confounders, duplicate submissions).
    """
    return await _search_adverse_events(drug_name)


@mcp.tool(name="openfda_count_indication_drugs")
async def count_indication_drugs(indication: str) -> IndicationDrugLandscape:
    """Count FDA-approved drugs for an indication, regardless of target gene.

    Target-agnostic disease-level competitive landscape (contrast with
    `openfda_search_drug_labels`, which is gene-keyed). Returns the approved-drug
    count, drug names, a few mechanism-of-action examples, and which query path
    matched (`phrase` / `broad` / `none`).
    """
    return await _count_indication_drugs(indication)


if __name__ == "__main__":
    mcp.run()
