# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""DepMap CRISPR dependency tools via the Broad DepMap public API.

Two data paths:

  * **Portal API** (`depmap.org/portal/api/download/...`): the per-gene summary
    (dependent-line counts, Common Essential, Strongly Selective) plus the bulk-file
    listing of the current release.
  * **figshare release mirror** (fallback): DepMap publishes each public release on
    figshare (CC BY 4.0). Since 2026-10 the portal API answers non-browser clients with
    an interactive Cloudflare Turnstile page, and that page itself asks bulk users to
    take the release downloads rather than scrape the portal. When the portal fails,
    the newest official "DepMap <YY>Q<N> Public" release on figshare supplies
    `CRISPRGeneEffect.csv`, `Model.csv` and `CRISPRInferredCommonEssentials.csv`; line
    counts are computed from the gene-effect matrix with DepMap's own −0.5 threshold.
    The release used is named in the evidence text, because the mirror can lag the portal.
"""

from __future__ import annotations

import contextlib
import csv
import io
import re
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import looks_like_html, require_data_payload

_GENE_DEP_SUMMARY_URL = "https://depmap.org/portal/api/download/gene_dep_summary"
_DOWNLOADS_API_URL = "https://depmap.org/portal/api/download/files"
_CHRONOS_DATASET = "DependencyEnum.Chronos_Combined"

# Files we want from the bulk download (required), plus the optional common-essential
# list used when the per-gene summary is unavailable.
_BULK_FILES = ("CRISPRGeneEffect.csv", "Model.csv")
_COMMON_ESSENTIALS_FILE = "CRISPRInferredCommonEssentials.csv"
_RELEASE_FILES = (*_BULK_FILES, _COMMON_ESSENTIALS_FILE)

_PORTAL_RELEASE = "DepMap portal (current public release)"

# figshare mirror of the official DepMap public releases.
_FIGSHARE_SEARCH_URL = "https://api.figshare.com/v2/articles/search"
_FIGSHARE_ARTICLE_URL = "https://api.figshare.com/v2/articles/{article_id}"
_FIGSHARE_RELEASE_TITLE = re.compile(r"^DepMap (\d{2})Q([1-4]) Public$")
# Newest release confirmed on figshare (DOI 10.25452/figshare.plus.27993248.v1) — used
# when release discovery itself fails.
_FIGSHARE_PINNED_RELEASE_ID = 27993248

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
    release: str = ""  # data release the scores came from, e.g. "DepMap 24Q4 Public (figshare)"
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
    require_data_payload(resp, "DepMap downloads API")

    reader = csv.DictReader(io.StringIO(resp.text))
    urls: dict[str, str] = {}
    for row in reader:
        release = row.get("release", "")
        if not release.startswith("DepMap"):
            continue
        name = row.get("filename", "")
        url = row.get("url", "")
        if name in _RELEASE_FILES and url and name not in urls:
            urls[name] = url
        if len(urls) == len(_RELEASE_FILES):
            break

    return urls


async def _figshare_release_files() -> tuple[str, dict[str, str]]:
    """(release title, {filename: download URL}) of the newest DepMap release on figshare.

    Discovery searches figshare for official "DepMap <YY>Q<N> Public" titles and takes
    the latest; if the search fails or finds none, the pinned release is used.
    """
    article_id = _FIGSHARE_PINNED_RELEASE_ID
    async with httpx.AsyncClient(timeout=30.0) as client:
        with contextlib.suppress(httpx.HTTPError, ValueError, KeyError, TypeError):
            search = await client.post(
                _FIGSHARE_SEARCH_URL,
                json={"search_for": "DepMap Public", "page_size": 100},
            )
            if search.status_code == 200:
                releases = [
                    (int(m.group(1)), int(m.group(2)), a["id"])
                    for a in search.json()
                    if (m := _FIGSHARE_RELEASE_TITLE.match(a.get("title", "")))
                ]
                if releases:
                    article_id = max(releases)[2]
        resp = await client.get(_FIGSHARE_ARTICLE_URL.format(article_id=article_id))
    if resp.status_code != 200:
        raise MCPToolError(f"figshare article {article_id} returned HTTP {resp.status_code}")
    require_data_payload(resp, "figshare article API")
    article = resp.json()
    urls = {
        f["name"]: f["download_url"]
        for f in article.get("files", [])
        if f.get("name") in _RELEASE_FILES and f.get("download_url")
    }
    if not all(name in urls for name in _BULK_FILES):
        raise MCPToolError(f"figshare release {article_id} lacks {', '.join(_BULK_FILES)}")
    return f"{article.get('title', 'DepMap release')} (figshare)", urls


async def _resolve_release_files() -> tuple[str, dict[str, str]]:
    """Bulk-file URLs from the portal, falling back to the figshare release mirror."""
    try:
        urls = await _get_bulk_file_urls()
        if all(name in urls for name in _BULK_FILES):
            return _PORTAL_RELEASE, urls
    except (MCPToolError, httpx.HTTPError):
        pass
    return await _figshare_release_files()


def _cache_path(filename: str, release: str = "") -> Path:
    # One cache directory per release, so portal and figshare copies never mix.
    directory = (
        _CACHE_DIR / re.sub(r"[^A-Za-z0-9]+", "_", release).strip("_") if release else _CACHE_DIR
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def _cache_valid(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached(filename: str, urls: dict[str, str], release: str = "") -> Path:
    path = _cache_path(filename, release)
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
        if looks_like_html(resp.headers.get("content-type", ""), b""):
            raise MCPToolError(f"Download of {filename} returned an HTML page instead of data")
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


def _load_common_essentials(csv_path: Path) -> set[str]:
    """Gene symbols in a release's CRISPRInferredCommonEssentials.csv ("GENE (ENTREZ)" rows)."""
    with open(csv_path, newline="") as f:
        return {
            row[0].split(" (")[0].strip()
            for row in csv.reader(f)
            if row and row[0].strip() and row[0].strip().lower() != "essentials"
        }


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


def bundle_has_dependency_data(bundle: DependencyBundle) -> bool:
    """True when the bundle carries line counts or Chronos scores — i.e. is evidence.

    A bundle built from an empty/failed lookup has neither; it must be reported as
    "unavailable", never surfaced as a dependency finding.
    """
    return bundle.num_dependent_lines is not None or bundle.gene_effect_mean is not None


async def get_dependency(gene_symbol: str) -> DependencyBundle:
    """Fetch DepMap CRISPR dependency scores and per-lineage breakdown for a gene.

    Raises MCPToolError only when neither the portal summary nor any bulk release
    (portal or figshare mirror) yields data — the caller must report DepMap as
    unavailable, never as "no dependency".
    """
    # --- Step 1: summary CSV (fast, ~2 MB) ---
    chronos_row: dict[str, Any] | None = None
    entrez_id: str | None = None
    summary_error = ""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(_GENE_DEP_SUMMARY_URL)
        if resp.status_code != 200:
            raise MCPToolError(f"DepMap API returned HTTP {resp.status_code}")
        require_data_payload(resp, "DepMap gene dependency summary")
        for row in csv.DictReader(io.StringIO(resp.text)):
            if row.get("Gene") == gene_symbol and row.get("Dataset") == _CHRONOS_DATASET:
                chronos_row = row
                entrez_id = row.get("Entrez Id", "").strip() or None
                break
    except (MCPToolError, httpx.HTTPError) as exc:
        summary_error = str(exc) or type(exc).__name__

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
    release = ""
    bulk_error = ""
    try:
        release, urls = await _resolve_release_files()
        effect_path = await _ensure_cached("CRISPRGeneEffect.csv", urls, release=release)
        model_path = await _ensure_cached("Model.csv", urls, release=release)

        model_to_score = _extract_gene_scores(effect_path, gene_symbol, entrez_id)
        lineages = _load_model_lineages(model_path)
        lineage_breakdown = _compute_lineage_breakdown(model_to_score, lineages)
        scores_list = list(model_to_score.values())

        # No summary row: take Common Essential from the release's inferred list.
        if chronos_row is None and _COMMON_ESSENTIALS_FILE in urls:
            with contextlib.suppress(MCPToolError, httpx.HTTPError, OSError):
                ce_path = await _ensure_cached(_COMMON_ESSENTIALS_FILE, urls, release=release)
                is_common = gene_symbol in _load_common_essentials(ce_path)
    except (MCPToolError, httpx.HTTPError) as exc:
        bulk_error = str(exc) or type(exc).__name__

    if summary_error and not scores_list:
        raise MCPToolError(
            f"{summary_error}; bulk release fallback: {bulk_error or 'gene not in release'}"
        )

    # --- Step 3: compute distribution stats ---
    mean_eff = std_eff = q1 = median = q3 = None
    if len(scores_list) >= 2:
        srt = sorted(scores_list)
        mean_eff = round(statistics.mean(srt), 4)
        std_eff = round(statistics.stdev(srt), 4)
        q1 = round(statistics.quantiles(srt, n=4)[0], 4)
        median = round(statistics.median(srt), 4)
        q3 = round(statistics.quantiles(srt, n=4)[2], 4)

    # No summary row (e.g. the summary endpoint failed) but bulk scores exist: count the
    # dependent lines from the scores with DepMap's own threshold.
    if n_dep is None and scores_list:
        n_dep = sum(1 for v in scores_list if v <= _DEP_THRESHOLD)
        n_total = len(scores_list)

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
        release=release,
        source_link=f"https://depmap.org/portal/gene/{gene_symbol}",
        text=(
            f"DepMap: {gene_symbol} dependent in {dep_frac_txt} lines (Chronos_Combined"
            f"{'; scores from ' + release if release and release != _PORTAL_RELEASE else ''})."
            f"{score_txt}{common_txt}{selective_txt}{lineage_txt}"
        ),
    )
