#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Search PubMed for each entry of claim_checks.json, for papers that bear on that claim.

Usage:
    run claim_search.py <run_dir>

Reads the claims and their terms from ``claim_checks.json``, runs each search once, registers
the papers with their abstracts, records the searches in ``claim_search.json`` (the checker
reads it) and writes ``claim_search.md`` to read. Search terms are not findings: the abstracts
decide each claim's outcome.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_claims import (  # noqa: E402
    CHECK_FILE,
    SEARCH_FILE,
    SEARCH_REPORT,
    gene_terms,
    load_searches,
    search_query,
)
from profile_schema import load_raw, load_sources, save_sources  # noqa: E402
from profile_writer import load_writer_file  # noqa: E402
from register_source import cache_abstracts, pmid_sources  # noqa: E402

from mcp_servers.pubmed import tools as pubmed  # noqa: E402

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_PAPERS_PER_SEARCH = 8
_ABSTRACT_CHARS = 1500


async def _search(query: str) -> dict[str, Any]:
    d = await pubmed._rate_limited_get(
        f"{_EUTILS}/esearch.fcgi",
        {"db": "pubmed", "term": query, "sort": "relevance", "retmax": _PAPERS_PER_SEARCH},
    )
    result = d.get("esearchresult", {})
    return {
        "query": query,
        "total": int(result.get("count") or 0),
        "pmids": result.get("idlist", []),
    }


def report(run_dir: Path, entries: list[dict[str, Any]], searches: dict[str, Any]) -> str:
    symbols = gene_terms(run_dir)
    rec = load_raw(run_dir, "pubmed_abstracts")
    papers = (rec.data or {}).get("papers", {}) if rec else {}
    sources = load_sources(run_dir)
    lines = ["# What the claim searches found", ""]
    for e in entries:
        search = searches.get(search_query(symbols, e.get("terms") or []))
        if not search:
            continue
        lines += [f"## {e.get('claim')}", "", f"`{search['query']}` ({search['total']} papers)", ""]
        for pmid in search["pmids"]:
            source = sources.get(f"pmid:{pmid}")
            title = f"{source.title} ({source.year})" if source else ""
            abstract = str((papers.get(pmid) or {}).get("abstract") or "no abstract")
            lines += [f"- **pmid:{pmid}** {title}", f"  {abstract[:_ABSTRACT_CHARS]}", ""]
    return "\n".join(lines)


async def amain(run_dir: Path) -> None:
    entries = load_writer_file(run_dir, CHECK_FILE)
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"write {CHECK_FILE} first: each claim with its search terms")
    symbols = gene_terms(run_dir)
    searches = load_searches(run_dir)
    for e in entries:
        terms = [t for t in e.get("terms") or [] if str(t).strip()]
        query = search_query(symbols, terms)
        if terms and query not in searches:
            searches[query] = await _search(query)
    (run_dir / SEARCH_FILE).write_text(json.dumps(list(searches.values()), indent=2))

    sources = load_sources(run_dir)
    pmids = list(dict.fromkeys(p for s in searches.values() for p in s["pmids"]))
    new = [p for p in pmids if f"pmid:{p}" not in sources]
    if new:
        for s in await pmid_sources(new):
            sources[s.key] = s
        save_sources(run_dir, sources)
    await cache_abstracts(run_dir, pmids)
    (run_dir / SEARCH_REPORT).write_text(report(run_dir, entries, searches))
    print(f"{len(searches)} searches · {len(new)} new papers · read {run_dir / SEARCH_REPORT}")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    asyncio.run(amain(p.parse_args().run_dir))


if __name__ == "__main__":
    main()
