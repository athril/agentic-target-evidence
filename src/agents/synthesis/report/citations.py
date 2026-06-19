# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared source-citation helpers for the report writers.

Single source of truth for turning a persisted ``Evidence`` row into a
human-readable, clickable citation back to its original record (PubMed, a
clinical-trial registry, a patent office, OpenTargets, gnomAD, GTEx, DepMap).

Both the per-lens report (``lens_report.py``) and the full dossier
(``agent.py``) build their external links through these helpers so the linking
logic never diverges.
"""

from __future__ import annotations

from typing import Any


def esc(text: Any) -> str:
    """Flatten and pipe-escape a value for a markdown table cell."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def link(label: Any, url: Any) -> str:
    """Render a clickable markdown link, falling back to a bare label if no URL."""
    text = esc(label)
    url = str(url or "").strip()
    return f"[{text}]({url})" if url else text


def row_type(row: Any) -> str:
    et = getattr(row, "evidence_type", "")
    return et.value if hasattr(et, "value") else str(et)


def row_extra(row: Any) -> dict:
    return getattr(row, "extra", None) or {}


def is_opentargets(row: Any) -> bool:
    return str(getattr(row, "source", "")).startswith("opentargets:")


def evidence_url(row: Any) -> str:
    """Best external URL for an evidence row, with per-source-type fallbacks."""
    ex = row_extra(row)
    source = str(getattr(row, "source", "") or "").strip()
    src_link = str(getattr(row, "source_link", "") or "").strip()
    if is_opentargets(row):
        return str(ex.get("assoc_source_link") or src_link or "").strip()
    if row_type(row) == "patent":
        # The MCP server usually supplies a Google Patents URL; synthesise one if not.
        bare = source.replace("patent:", "").strip()
        return src_link or (f"https://patents.google.com/patent/{bare}" if bare else "")
    return src_link


def evidence_label(row: Any) -> str:
    """Short, human-readable identifier for an evidence row."""
    source = str(getattr(row, "source", "") or "").strip()
    if is_opentargets(row):
        gene = getattr(row, "gene", "") or ""
        return f"OpenTargets · {gene}".strip(" ·")
    lower = source.lower()
    if lower.startswith(("gtex_hpa:", "hpa:")):
        return "GTEx/HPA"
    if lower.startswith("gtex_v8:"):
        tissue = source.split(":", 1)[1] if ":" in source else ""
        return f"GTEx · {tissue}".rstrip(" ·") if tissue else "GTEx/HPA"
    if lower.startswith("gnomad"):
        return "gnomAD"
    if lower.startswith("depmap:"):
        return "DepMap"
    if lower.startswith("uniprot:"):
        return "UniProt"
    if lower.startswith("fda:label:"):
        return "FDA label"
    if lower.startswith("fda:faers:"):
        return "FAERS"
    return source or row_type(row)


def evidence_detail(row: Any) -> str:
    """One-line description for an evidence row (title / summary / claim text)."""
    ex = row_extra(row)
    detail = (
        ex.get("title")
        or ex.get("summary")
        or ex.get("brief_summary")
        or ex.get("assoc_text")
        or ex.get("text")
        or ex.get("description")
        or getattr(row, "claim_text", "")
        or ""
    )
    return esc(detail)


def cite(row: Any) -> str:
    """Render ``[label](url)`` for an evidence row (label only if no URL)."""
    return link(evidence_label(row), evidence_url(row))


def _is_gtex_facet(row: Any) -> bool:
    return str(getattr(row, "source", "") or "").lower().startswith(("gtex_v8:", "gtex_hpa:"))


def _gtex_collapsed_detail(group: list) -> str:
    """Tissue:TPM list for a collapsed GTEx group, bolding the highest-expressing tissues.

    "Highest-expressing" is relative to this gene's own peak tissue (>= 50% of
    the max median TPM in the group), not an absolute cutoff, so the highlight
    is meaningful regardless of the gene's overall expression level.
    """
    pairs: list[tuple[str, float]] = []
    other: list[str] = []
    for r in group:
        ex = row_extra(r)
        try:
            pairs.append((str(ex["tissue"]), float(ex["median_tpm"])))
        except (KeyError, TypeError, ValueError):
            d = evidence_detail(r)
            if d:
                other.append(d)

    if not pairs:
        return "; ".join(dict.fromkeys(other))

    pairs.sort(key=lambda p: p[1], reverse=True)
    threshold = pairs[0][1] * 0.5
    parts = [
        f"**{tissue}: {tpm:.1f} TPM**" if tpm >= threshold else f"{tissue}: {tpm:.1f} TPM"
        for tissue, tpm in pairs
    ]
    return ", ".join(parts + other)


def collapse_by_url(rows: list) -> list[tuple[str, str, Any]]:
    """Merge evidence rows that resolve to the same external URL into one row.

    Some sources emit one ``Evidence`` row per facet (one per GTEx tissue,
    repeated gnomAD constraint rows, ...) that all link back to the same
    gene-level page. Showing each as its own table row is repeated-link noise;
    folding them into a single citation keeps every underlying detail without
    the repetition.

    Returns ``(citation_markdown, detail, representative_row)`` triples — the
    representative row lets callers that render extra per-row columns (e.g. a
    Type column) pick a sensible value for the merged row.
    """
    groups: dict[str, list] = {}
    order: list[str] = []
    for r in rows:
        key = evidence_url(r) or f"__no_url_{len(order)}__"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    out: list[tuple[str, str, Any]] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            r = group[0]
            out.append((cite(r), evidence_detail(r), r))
            continue
        # The richest-detail row (usually an aggregate/summary row, if one is
        # present in the group) supplies the collapsed row's label and type.
        rep = max(group, key=lambda r: len(evidence_detail(r)))
        url = "" if key.startswith("__no_url_") else key
        label = link(f"{evidence_label(rep)} ({len(group)})", url)
        if all(_is_gtex_facet(r) for r in group):
            detail = _gtex_collapsed_detail(group)
        else:
            detail = "; ".join(dict.fromkeys(d for d in (evidence_detail(r) for r in group) if d))
        out.append((label, detail, rep))
    return out


def pub_year(row: Any) -> str:
    """Publication year for a literature row, from ``extra['pub_year']``."""
    year = row_extra(row).get("pub_year")
    return esc(year) if year else ""


def first_author(row: Any) -> str:
    """First listed author for a literature row, from ``extra['authors']``."""
    authors = row_extra(row).get("authors") or []
    return esc(authors[0]) if authors else ""


def quality_rank(quality: dict | None) -> int:
    """0-3 star rank for a source-quality assessment, or -1 if unassessed.

    ``quality`` is a per-evidence entry from the Critic's source-quality pass
    (``sjr_score`` 0-1, plus a ``predatory_flag``). -1 (rather than 0) for a
    missing assessment so "no data" sorts below confirmed low quality.
    """
    if not quality or quality.get("sjr_score") is None:
        return -1
    if quality.get("predatory_flag"):
        return 0
    score = quality["sjr_score"]
    return 3 if score >= 0.75 else 2 if score >= 0.5 else 1 if score >= 0.25 else 0


def quality_stars(quality: dict | None) -> str:
    """Render a source-quality assessment as 0-3 stars ("—" if unassessed)."""
    n = quality_rank(quality)
    return "—" if n < 0 else "★" * n + "☆" * (3 - n)
