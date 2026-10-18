# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Audit what each lens is shown, on saved panel runs (read-only, no model calls).

Replays the current lens input selection (config/lens_inputs.yaml and the literature
ranking) on each run's LangGraph checkpoint and reports, per run and lens: the claim pool
and the shown claims per evidence kind, distinct papers, evidence classes of the shown
papers, their in-scope and review shares, whether the selection is the same with quality
scores removed, the share of literature with a quality score, and — for a pair present in
several panels — how much the shown papers overlap between its runs.

    uv run python scripts/audit_lens_inputs.py results/panel/<ts> [results/panel/<ts> ...]
        [--refresh-metadata] [--out results/evals/lens_inputs.md]

--refresh-metadata fills in PubMed publication types and MeSH, and iCite metrics, for
saved runs retrieved before they were kept (network: NCBI and iCite), as a new run would.
Needs DATABASE_URL (the checkpoint store), e.g. `set -a; . ./.env; set +a`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.interpretation._lens_base import (  # noqa: E402
    _LITERATURE_TYPES,
    LENS_EVIDENCE_TYPES,
    _deserialise_claims,
    _filter_claims,
    _shown_claims,
)
from core.checkpoint import get_checkpointer  # noqa: E402
from schemas.evidence import CoreClaim, Evidence  # noqa: E402
from services.evidence.lens_selection import kind_counts, lens_slots  # noqa: E402
from services.evidence.literature_selection import LiteratureIndex, paper_class  # noqa: E402

_LENSES = ("genetics", "biology", "safety", "clinical", "commercial", "regulatory")
_META_KEYS = ("publication_types", "mesh_headings", "retracted", "mesh_indexed")


async def _refresh(state: dict[str, Any]) -> dict[str, Any]:
    """The state with PubMed metadata and iCite metrics filled in, as a new run has them."""
    import httpx

    import capabilities.target_validation.workflow as wf
    from agents.retrieval.literature.agent import with_article_metadata
    from agents.screening.source_quality.agent import _attach_article_metrics

    literature, _ = await with_article_metadata(list(state.get("literature_evidence") or []))
    by_id = {str(ev.evidence_id): ev for ev in literature}

    def merged(ev: Evidence) -> Evidence:
        fresh = by_id.get(str(ev.evidence_id))
        if fresh is None:
            return ev
        extra = {**(ev.extra or {}), **{k: (fresh.extra or {}).get(k) for k in _META_KEYS}}
        return ev.model_copy(update={"extra": extra})

    state = {
        **state,
        "screened_evidence": [merged(ev) for ev in state.get("screened_evidence") or []],
    }
    papers = [ev for ev in wf._keep_evidence(state) if ev.evidence_type in _LITERATURE_TYPES]
    quality = {k: dict(v) for k, v in (state.get("source_quality") or {}).items()}
    for ev in papers:
        quality.setdefault(str(ev.evidence_id), {})
    async with httpx.AsyncClient():
        await _attach_article_metrics(papers, quality)
    return {**state, "source_quality": quality}


def _lens_row(
    lens: str,
    claims: list[CoreClaim],
    quality: dict[str, Any],
    index: LiteratureIndex | None,
    pmid_of: dict[str, str],
) -> tuple[str, set[str], set[str]]:
    kinds = lens_slots(lens)
    pool = _filter_claims(claims, LENS_EVIDENCE_TYPES[lens], lens)  # type: ignore[index]
    shown = _shown_claims(pool, quality, frozenset(), lens, None, index)  # type: ignore[arg-type]
    unscored = _shown_claims(pool, {}, frozenset(), lens, None, index)  # type: ignore[arg-type]
    invariant = kind_counts(shown, kinds) == kind_counts(unscored, kinds)
    pooled, counted = kind_counts(pool, kinds), kind_counts(shown, kinds)
    per_kind = ", ".join(f"{k} {counted.get(k, 0)}/{n}" for k, n in pooled.items())
    papers = list(
        dict.fromkeys(
            str(c.source_evidence_id) for c in shown if c.evidence_type in _LITERATURE_TYPES
        )
    )
    records = [index.papers[p] for p in papers if index and p in index.papers]
    classes = (
        Counter(paper_class(r, lens) for r in records)
        if lens in ("genetics", "biology", "safety", "clinical")
        else Counter()
    )
    in_scope = sum(r.scope == "in_scope" for r in records)
    reviews = sum(r.review for r in records)
    class_text = "/".join(str(classes.get(c, 0)) for c in range(4)) if classes else "—"
    row = (
        f"| {lens} | {len(pool)} | {len(shown)} | {per_kind} | {len(papers)} | {class_text} | "
        f"{in_scope}/{len(records) or '—'} | {reviews} | {'yes' if invariant else 'NO'} |"
    )
    pooled_papers = {
        pmid_of.get(str(c.source_evidence_id), str(c.source_evidence_id))
        for c in pool
        if c.evidence_type in _LITERATURE_TYPES
    }
    return row, {pmid_of.get(p, p) for p in papers}, pooled_papers


async def main(panel_dirs: list[Path], out: Path | None, refresh: bool) -> int:
    import capabilities.target_validation.workflow as wf

    lines = [
        "# Lens input audit",
        "",
        "Classes: strong/moderate/weak/background papers among the shown literature. "
        "Invariant: the same kinds and counts with quality scores removed.",
    ]
    shown_by_pair: dict[tuple[str, str, str, str], list[tuple[str, set[str], set[str]]]] = {}
    async with get_checkpointer() as cp:
        for panel in panel_dirs:
            with (panel / "summary.tsv").open() as fh:
                rows = [r for r in csv.DictReader(fh, delimiter="\t") if r.get("thread_id")]
            for row in rows:
                tup = await cp.aget_tuple({"configurable": {"thread_id": row["thread_id"]}})
                if tup is None:
                    continue
                state = dict(tup.checkpoint["channel_values"])
                if refresh:
                    state = await _refresh(state)
                claims = _deserialise_claims(list(state.get("extracted_claims") or []))
                quality = state.get("source_quality") or {}
                index = LiteratureIndex.model_validate(await wf._literature_index(state))  # type: ignore[arg-type]
                pmid_of = {pid: r.pmid for pid, r in index.papers.items()}
                literature = [c for c in claims if c.evidence_type in _LITERATURE_TYPES]
                scored = sum(
                    1
                    for c in literature
                    if (quality.get(str(c.source_evidence_id)) or {}).get("sjr_score") is not None
                )
                lines += [
                    "",
                    f"## {panel.name} · {row['gene']} × {row['disease']} ({row['direction']})",
                    "",
                    f"Literature claims with a quality score: {scored}/{len(literature)}.",
                    "",
                    "| Lens | Pool | Shown | Shown/pool per kind | Papers | Classes | In scope | Reviews | Invariant |",
                    "|---|---|---|---|---|---|---|---|---|",
                ]
                for lens in _LENSES:
                    text, pmids, pooled = _lens_row(lens, claims, quality, index, pmid_of)
                    lines.append(text)
                    key = (row["gene"], row["disease"], row["direction"], lens)
                    shown_by_pair.setdefault(key, []).append((panel.name, pmids, pooled))
    repeats = {k: v for k, v in shown_by_pair.items() if len(v) > 1 and any(p for _, p, _ in v)}
    if repeats:
        lines += [
            "",
            "## Shown papers across runs of the same pair",
            "",
            "Jaccard of the shown papers, overall and among the papers both runs had in the "
            "lens's pool (which isolates selection from differences in retrieval).",
            "",
            "| Pair | Lens | Runs | Jaccard | Within the common pool |",
            "|---|---|---|---|---|",
        ]
        for (gene, disease, direction, lens), runs in sorted(repeats.items()):
            (a_name, a, pool_a), (b_name, b, pool_b) = runs[0], runs[-1]
            common = pool_a & pool_b
            jaccard = len(a & b) / len(a | b) if a | b else 1.0
            ca, cb = a & common, b & common
            within = len(ca & cb) / len(ca | cb) if ca | cb else 1.0
            lines.append(
                f"| {gene} × {disease} ({direction}) | {lens} | {a_name} → {b_name} | "
                f"{jaccard:.2f} | {within:.2f} |"
            )
    text = "\n".join(lines) + "\n"
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("panel_dirs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--refresh-metadata", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.panel_dirs, args.out, args.refresh_metadata)))
