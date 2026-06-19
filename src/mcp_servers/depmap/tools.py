# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""DepMap CRISPR dependency tools via the Broad DepMap public API."""

from __future__ import annotations

import contextlib
import csv
import io
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_GENE_DEP_SUMMARY_URL = "https://depmap.org/portal/api/download/gene_dep_summary"
_DOWNLOADS_API_URL = "https://depmap.org/portal/api/download/files"
_CHRONOS_DATASET = "DependencyEnum.Chronos_Combined"

# Files we want from the bulk download
_BULK_FILES = ("CRISPRGeneEffect.csv", "Model.csv")

# Chronos score threshold used by DepMap to classify a line as "dependent"
_DEP_THRESHOLD = -0.5

# Cache large CSVs locally; 7-day TTL keeps data reasonably fresh
_CACHE_DIR = Path(tempfile.gettempdir()) / "depmap_cache"
_CACHE_TTL = 7 * 24 * 3600


class LineageSummary(BaseModel):
    lineage: str
    n_dependent: int
    n_total: int
    mean_effect: float | None = None


class DependencyBundle(BaseModel):
    gene_symbol: str
    gene_effect_mean: float | None = None
    gene_effect_std: float | None = None
    gene_effect_q1: float | None = None
    gene_effect_median: float | None = None
    gene_effect_q3: float | None = None
    num_dependent_lines: int | None = None
    total_lines: int | None = None
    dependency_fraction: float | None = None
    is_common_essential: bool = False
    is_strongly_selective: bool = False
    lineage_breakdown: list[LineageSummary] = []
    selective_lineages: list[str] = []
    source_link: str = ""
    text: str = ""


# ---------------------------------------------------------------------------
# Bulk-file helpers
# ---------------------------------------------------------------------------


async def _get_bulk_file_urls() -> dict[str, str]:
    """Return download URLs for the current DepMap release bulk files.

    The DepMap API returns a CSV with columns:
        release, release_date, filename, url, md5_hash
    Rows are sorted newest-first; we take the first DepMap Public release
    that contains both target files.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(_DOWNLOADS_API_URL)
    if resp.status_code != 200:
        raise MCPToolError(f"DepMap downloads API returned {resp.status_code}")

    reader = csv.DictReader(io.StringIO(resp.text))
    urls: dict[str, str] = {}
    for row in reader:
        release = row.get("release", "")
        if not release.startswith("DepMap"):
            continue
        name = row.get("filename", "")
        url = row.get("url", "")
        if name in _BULK_FILES and url and name not in urls:
            urls[name] = url
        if len(urls) == len(_BULK_FILES):
            break

    return urls


def _cache_path(filename: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / filename


def _cache_valid(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached(filename: str, urls: dict[str, str]) -> Path:
    path = _cache_path(filename)
    if _cache_valid(path):
        return path

    url = urls.get(filename)
    if not url:
        raise MCPToolError(f"No download URL found for {filename}")

    async with (
        httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client,
        client.stream("GET", url) as resp,
    ):
        if resp.status_code != 200:
            raise MCPToolError(f"Download failed for {filename}: {resp.status_code}")
        with open(path, "wb") as f:
            async for chunk in resp.aiter_bytes(65536):
                f.write(chunk)

    return path


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_gene_scores(
    csv_path: Path,
    gene_symbol: str,
    entrez_id: str | None,
) -> dict[str, float]:
    """Return {model_id: chronos_score} for the requested gene.

    CRISPRGeneEffect.csv has rows=models, cols='GENE (ENTREZ)'.
    """
    model_to_score: dict[str, float] = {}
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)

        # Locate the gene column
        gene_col: int | None = None
        for i, h in enumerate(headers):
            sym = h.split(" (")[0].strip()
            if sym != gene_symbol:
                continue
            if entrez_id is None:
                gene_col = i
                break
            h_entrez = h.split("(")[-1].rstrip(")").strip() if "(" in h else ""
            if h_entrez == str(entrez_id):
                gene_col = i
                break

        if gene_col is None:
            return {}

        for row in reader:
            if len(row) <= gene_col:
                continue
            val_str = row[gene_col].strip()
            if not val_str or val_str.lower() == "na":
                continue
            with contextlib.suppress(ValueError):
                model_to_score[row[0]] = float(val_str)

    return model_to_score


def _load_model_lineages(csv_path: Path) -> dict[str, str]:
    """Return {model_id: oncotree_lineage} from Model.csv."""
    lineages: dict[str, str] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            mid = row.get("ModelID", "")
            lin = row.get("OncotreeLineage", "") or row.get("lineage", "")
            if mid and lin:
                lineages[mid] = lin
    return lineages


def _compute_lineage_breakdown(
    model_to_score: dict[str, float],
    lineages: dict[str, str],
) -> list[LineageSummary]:
    """Per-lineage dependency counts and mean Chronos score."""
    bucket: dict[str, list[float]] = defaultdict(list)
    for model_id, score in model_to_score.items():
        bucket[lineages.get(model_id, "Unknown")].append(score)

    summaries = []
    for lineage, scores in bucket.items():
        n_dep = sum(1 for s in scores if s <= _DEP_THRESHOLD)
        summaries.append(
            LineageSummary(
                lineage=lineage,
                n_dependent=n_dep,
                n_total=len(scores),
                mean_effect=round(statistics.mean(scores), 4),
            )
        )

    summaries.sort(
        key=lambda x: x.n_dependent / x.n_total if x.n_total else 0,
        reverse=True,
    )
    return summaries


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------


async def get_dependency(gene_symbol: str) -> DependencyBundle:
    """Fetch DepMap CRISPR dependency scores and per-lineage breakdown for a gene."""
    # --- Step 1: summary CSV (fast, ~2 MB) ---
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(_GENE_DEP_SUMMARY_URL)

    if resp.status_code != 200:
        raise MCPToolError(f"DepMap API returned HTTP {resp.status_code}")

    chronos_row: dict | None = None
    entrez_id: str | None = None
    for row in csv.DictReader(io.StringIO(resp.text)):
        if row.get("Gene") == gene_symbol and row.get("Dataset") == _CHRONOS_DATASET:
            chronos_row = row
            entrez_id = row.get("Entrez Id", "").strip() or None
            break

    def _int(val: str) -> int | None:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None

    def _bool(val: str) -> bool:
        return val.strip().lower() == "true" if val else False

    n_dep: int | None = None
    n_total: int | None = None
    is_common = False
    is_selective = False
    if chronos_row:
        n_dep = _int(chronos_row.get("Dependent Cell Lines", ""))
        n_total = _int(chronos_row.get("Cell Lines with Data", ""))
        is_common = _bool(chronos_row.get("Common Essential", ""))
        is_selective = _bool(chronos_row.get("Strongly Selective", ""))

    # --- Step 2: bulk files for quantitative scores + lineage breakdown ---
    scores_list: list[float] = []
    lineage_breakdown: list[LineageSummary] = []
    try:
        urls = await _get_bulk_file_urls()
        effect_path = await _ensure_cached("CRISPRGeneEffect.csv", urls)
        model_path = await _ensure_cached("Model.csv", urls)

        model_to_score = _extract_gene_scores(effect_path, gene_symbol, entrez_id)
        lineages = _load_model_lineages(model_path)
        lineage_breakdown = _compute_lineage_breakdown(model_to_score, lineages)
        scores_list = list(model_to_score.values())
    except MCPToolError:
        pass  # fall back gracefully; summary data still available

    # --- Step 3: compute distribution stats ---
    mean_eff = std_eff = q1 = median = q3 = None
    if len(scores_list) >= 2:
        srt = sorted(scores_list)
        mean_eff = round(statistics.mean(srt), 4)
        std_eff = round(statistics.stdev(srt), 4)
        q1 = round(statistics.quantiles(srt, n=4)[0], 4)
        median = round(statistics.median(srt), 4)
        q3 = round(statistics.quantiles(srt, n=4)[2], 4)

    dep_fraction = (n_dep / n_total) if n_dep is not None and n_total else None

    # Lineages where ≥90% of lines are dependent and ≥3 lines were tested
    selective_lineages = [
        lb.lineage
        for lb in lineage_breakdown
        if lb.n_total >= 3 and lb.n_dependent / lb.n_total >= 0.9
    ]

    # --- Build human-readable summary ---
    dep_frac_txt = f"{n_dep}/{n_total}" if n_dep is not None and n_total else "unknown"
    score_txt = (
        f" Mean Chronos: {mean_eff:.3f} (SD {std_eff:.3f}, Q1/Q3 {q1:.3f}/{q3:.3f})."
        if mean_eff is not None
        else ""
    )
    common_txt = " Common essential (pan-cancer)." if is_common else ""
    # "Strongly Selective" is a distribution/skewness flag, not proof of a therapeutic window.
    # Only render it as a positive signal when there are actual high-dependency lineages;
    # otherwise emit a corrective to prevent the LLM from inventing lineage essentiality.
    meaningfully_dependent = (dep_fraction is not None and dep_fraction >= 0.05) or bool(
        selective_lineages
    )
    if is_selective and not selective_lineages and not meaningfully_dependent:
        selective_txt = (
            " Flagged 'strongly selective' by DepMap's distribution test,"
            " but no lineage reaches the dependency threshold and the gene is"
            " non-essential across all lines (not a usable selectivity signal)."
        )
    elif is_selective:
        selective_txt = " Strongly selective."
    else:
        selective_txt = ""
    lineage_txt = (
        f" High-dependency lineages (≥90%): {', '.join(selective_lineages[:5])}."
        if selective_lineages
        else ""
    )

    return DependencyBundle(
        gene_symbol=gene_symbol,
        gene_effect_mean=mean_eff,
        gene_effect_std=std_eff,
        gene_effect_q1=q1,
        gene_effect_median=median,
        gene_effect_q3=q3,
        num_dependent_lines=n_dep,
        total_lines=n_total,
        dependency_fraction=round(dep_fraction, 4) if dep_fraction is not None else None,
        is_common_essential=is_common,
        is_strongly_selective=is_selective,
        lineage_breakdown=lineage_breakdown,
        selective_lineages=selective_lineages[:10],
        source_link=f"https://depmap.org/portal/gene/{gene_symbol}",
        text=(
            f"DepMap: {gene_symbol} dependent in {dep_frac_txt} lines (Chronos_Combined)."
            f"{score_txt}{common_txt}{selective_txt}{lineage_txt}"
        ),
    )
