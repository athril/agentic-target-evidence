# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A view of the field around the gene, from the indexing records of its grounded papers.

Deterministic, no LLM. Every count is over the grounded set (gene2pubmed or a PubTator gene,
ortholog or variant tag: profile_literature), never a keyword count.

* field map: papers by year × MeSH branch of their major headings (top-level tree branch,
  e.g. C14 Cardiovascular Diseases); papers not yet MeSH-indexed are counted apart with the
  diseases and chemicals PubTator tags in them;
* groups: the last authors of the gene's papers of the last five years, by paper count —
  names exactly as the records give them; a last author approximates a group;
* methods: the MeSH Investigative Techniques (E05) headings of the gene's papers;
* public data: GEO series matching the gene and perturbation words (a keyword query, linked),
  GWAS studies with full summary statistics, and what was not checked.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

MIN_INDEXED = 20
TOP_BRANCHES = 12
RECENT_YEARS = 5
TOP_GROUPS = 10
TOP_METHODS = 12
METHODS_BRANCH = "E05"


def _cite(keys: Sequence[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ").strip()


def field_map(data: Mapping[str, Any], this_year: int) -> dict[str, Any]:
    """Papers per (MeSH branch, year) from major headings; each paper counts once per
    branch. Branches ranked by papers; each gets its share from the last five years."""
    trees = data.get("mesh_trees") or {}
    labels = data.get("branch_labels") or {}
    grid: dict[str, Counter[int]] = {}
    indexed = unindexed = 0
    unindexed_tags: Counter[str] = Counter()
    for v in (data.get("papers") or {}).values():
        if not v.get("indexed"):
            unindexed += 1
            unindexed_tags.update(v.get("pubtator") or [])
            continue
        indexed += 1
        year = v.get("year")
        branches = {
            t[:3] for d, ui, major in v.get("mesh") or [] if major for t in trees.get(ui, [])
        }
        for b in branches:
            grid.setdefault(b, Counter())[year or 0] += 1
    ranked = sorted(grid, key=lambda b: (-sum(grid[b].values()), b))[:TOP_BRANCHES]
    rows = []
    for b in ranked:
        total = sum(grid[b].values())
        recent = sum(n for y, n in grid[b].items() if y and y > this_year - RECENT_YEARS)
        rows.append(
            {"branch": b, "label": labels.get(b, b), "papers": total,
             "recent_share": recent / total if total else 0.0, "by_year": dict(grid[b])}
        )  # fmt: skip
    years = sorted({y for b in ranked for y in grid[b] if y})
    return {
        "indexed": indexed,
        "unindexed": unindexed,
        "unindexed_tags": unindexed_tags.most_common(8),
        "rows": rows,
        "years": years,
    }


def trend(share: float, overall: float) -> str:
    """Rising / steady / quiet: a branch's share of papers from the last five years against
    the share across all branches (±50%)."""
    if overall <= 0:
        return "steady"
    if share >= 1.5 * overall:
        return "rising"
    if share <= 0.5 * overall:
        return "quiet"
    return "steady"


def field_md(fm: Mapping[str, Any], available: set[str]) -> str:
    """The field map as a table: branch, papers, share since five years ago, trend; with
    the unindexed papers apart."""
    if not fm["rows"]:
        return ""
    total = sum(r["papers"] for r in fm["rows"])
    overall = sum(r["recent_share"] * r["papers"] for r in fm["rows"]) / total if total else 0
    ref = _cite(["field_papers"], available)
    out = [
        "| MeSH branch (major headings) | Papers | Last 5 years | Trend | Refs |",
        "|---|---|---|---|---|",
    ]
    out += [
        f"| {_cell(r['label'])} ({r['branch']}) | {r['papers']} | {r['recent_share'] * 100:.0f}% "
        f"| {trend(r['recent_share'], overall)} | {ref} |"
        for r in fm["rows"]
    ]
    tags = ", ".join(f"{t} ({n})" for t, n in fm["unindexed_tags"]) or "none tagged"
    out += [
        "",
        f"{fm['indexed']} MeSH-indexed grounded papers; {fm['unindexed']} not yet indexed (their "
        f"PubTator diseases and chemicals: {tags}). A paper counts once per branch of its major "
        "headings; trend compares a branch's share of papers from the last five years with the "
        f"share across branches {ref}.",
    ]
    return "\n".join(out)


def groups(data: Mapping[str, Any], this_year: int) -> list[dict[str, Any]]:
    """Last authors of the grounded papers of the last five years, by paper count, with
    their most recent papers."""
    found: dict[str, list[tuple[int, str]]] = {}
    for pmid, v in (data.get("papers") or {}).items():
        year = v.get("year") or 0
        if v.get("last_author") and year > this_year - RECENT_YEARS:
            found.setdefault(v["last_author"], []).append((year, pmid))
    rows = [
        {"name": name, "papers": len(ps), "latest": max(y for y, _ in ps),
         "pmids": [p for _, p in sorted(ps, reverse=True)[:2]]}
        for name, ps in found.items()
    ]  # fmt: skip
    return sorted(rows, key=lambda r: (-r["papers"], -r["latest"], r["name"]))[:TOP_GROUPS]


MIN_GROUP_PAPERS = 2


def groups_md(data: Mapping[str, Any], this_year: int, available: set[str]) -> str:
    rows = [r for r in groups(data, this_year) if r["papers"] >= MIN_GROUP_PAPERS]
    if not rows:
        return ""
    out = ["| Last author | Papers, last 5 years | Most recent | Refs |", "|---|---|---|---|"]
    out += [
        f"| {_cell(r['name'])} | {r['papers']} | {r['latest']} "
        f"| {_cite([f'pmid:{p}' for p in r['pmids']], available) or _cite(['field_papers'], available)} |"
        for r in rows
    ]
    out += [
        "",
        f"Last authors of the gene's grounded papers since {this_year - RECENT_YEARS + 1}, with at "
        f"least {MIN_GROUP_PAPERS} papers; names as the records give them (a last author "
        "approximates a group; namesakes are not told apart) "
        + _cite(["field_papers"], available)
        + ".",
    ]
    return "\n".join(out)


def methods(data: Mapping[str, Any], this_year: int) -> list[dict[str, Any]]:
    """MeSH Investigative Techniques (E05) headings across the grounded papers, by count."""
    trees = data.get("mesh_trees") or {}
    total: Counter[str] = Counter()
    recent: Counter[str] = Counter()
    for v in (data.get("papers") or {}).values():
        names = {d for d, ui, _major in v.get("mesh") or []
                 if any(t.startswith(METHODS_BRANCH) for t in trees.get(ui, []))}  # fmt: skip
        total.update(names)
        if (v.get("year") or 0) > this_year - RECENT_YEARS:
            recent.update(names)
    return [
        {"method": m, "papers": n, "recent": recent[m]} for m, n in total.most_common(TOP_METHODS)
    ]


def methods_md(data: Mapping[str, Any], this_year: int, available: set[str]) -> str:
    rows = methods(data, this_year)
    if not rows:
        return ""
    ref = _cite(["field_papers"], available)
    out = ["| Method (MeSH E05) | Papers | Last 5 years | Refs |", "|---|---|---|---|"]
    out += [f"| {_cell(r['method'])} | {r['papers']} | {r['recent']} | {ref} |" for r in rows]
    out += ["", f"MeSH Investigative Techniques headings of the gene's grounded papers {ref}."]
    return "\n".join(out)


MAX_SERIES = 8


def public_data_md(
    geo: Mapping[str, Any],
    gwas: Mapping[str, Any],
    hpa: Mapping[str, Any],
    available: set[str],
    geo_url: str = "",
    genebass: Mapping[str, Any] | None = None,
    orcs: Mapping[str, Any] | None = None,
) -> str:
    """Data a student can reuse: GEO series matching the query (shown as such, linked),
    GWAS studies with full summary statistics, HPA single-cell data, Genebass burden
    results and the CRISPR screens that scored the gene."""
    rows: list[tuple[str, str, str]] = []
    series = geo.get("series") or []
    query = f"[query]({geo_url})" if geo_url else "query"
    if series:
        shown = "; ".join(
            f"{s['accession']} ({s.get('organism', '')}, {s.get('samples', '?')} samples): {s.get('title', '')[:70]}"
            for s in series[:MAX_SERIES]
        )
        rows.append(
            (
                f"GEO series matching the {query}",
                f"{geo.get('total', len(series))} series; newest: {shown}",
                _cite(["geo_perturbation"], available),
            )
        )
    elif geo:
        rows.append(
            (
                f"GEO series matching the {query}",
                "No GEO series found by this query",
                _cite(["geo_perturbation"], available),
            )
        )
    studies = {}
    for h in gwas.get("hits") or []:
        if h.get("summary_statistics") and h.get("study_accession"):
            studies.setdefault(h["study_accession"], h.get("trait", ""))
    rows.append((
        "GWAS summary statistics (GWAS Catalog)",
        f"{len(studies)} of the associated studies: " + "; ".join(f"{k} ({v})" for k, v in list(studies.items())[:6])
        if studies else "None of the associated studies has full summary statistics in the catalogue",
        _cite(["gwas"], available),
    ))  # fmt: skip
    if hpa.get("RNA single cell type specific nCPM") or hpa.get("RNA single cell type specificity"):
        rows.append(
            (
                "Single-cell RNA (HPA)",
                "HPA single-cell type data for the gene",
                _cite(["hpa"], available),
            )
        )
    masks = (genebass or {}).get("masks") or []
    tested = max((m.get("phenotypes_tested") or 0 for m in masks), default=0)
    rows.append(
        (
            "Rare-variant burden, UK Biobank exomes (Genebass)",
            f"Gene-level results for {tested:,} phenotypes; variant-level on its gene page"
            if masks
            else "not checked",
            _cite(["genebass"], available),
        )
    )
    screens = (orcs or {}).get("screens") or []
    rows.append(
        (
            "Published CRISPR screens (BioGRID ORCS)",
            f"{len(screens)} human screens score the gene; per-screen scores downloadable"
            if screens
            else "not checked"
            if not orcs
            else "No human screen scores the gene",
            _cite(["biogrid_orcs"], available),
        )
    )
    out = ["| Resource | What is there | Refs |", "|---|---|---|"]
    out += [f"| {a} | {_cell(b)} | {c} |" for a, b, c in rows]
    out += [
        "",
        "GEO series are a keyword match (the gene symbol and generic perturbation words), not "
        "a curated list of perturbations of this gene; open the query to check each.",
    ]
    return "\n".join(out)
