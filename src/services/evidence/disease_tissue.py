# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disease -> affected-tissue/cell-type resolver + deterministic tissue-relevance framing.

Corrects the TRPC6 x FSGS report error where the biology/safety lenses inferred
"relevant tissue" from raw bulk-GTEx TPM rank (Lung 23.2, Esophagus 19.0 ranked
above Kidney_Cortex 1.2) instead of the actual disease-affected tissue/cell type.
Bulk TPM rank is not a proxy for disease relevance — this module supplies the
curated ground truth (config/disease_tissue.yaml) so the lens LLM is told which
tissue matters before it reasons, rather than being left to rank-order TPM itself.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

_CONFIG_PATH = Path("config/disease_tissue.yaml")


class DiseaseTissueInfo(BaseModel):
    disease_id: str
    disease_name: str = ""
    gtex_tissues: list[str] = []
    cell_types: list[str] = []
    note: str = ""


@functools.lru_cache(maxsize=1)
def _load_config(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def reload_disease_tissue_map() -> None:
    """Drop the cached config so the next resolve_disease_tissue() re-reads disk."""
    _load_config.cache_clear()


def resolve_disease_tissue(
    disease_id: str | None, path: Path | None = None
) -> DiseaseTissueInfo | None:
    """Look up the curated affected-tissue/cell-type mapping for a disease.

    Returns ``None`` when ``disease_id`` is missing or not in the curated config.
    Callers MUST treat ``None`` as "mapping unknown" and fall back to deriving
    tissue relevance from claims/literature — never as "no tissue is relevant".
    """
    if not disease_id:
        return None
    data = _load_config(str(path or _CONFIG_PATH))
    entry = data.get(disease_id)
    if not entry:
        return None
    return DiseaseTissueInfo(disease_id=disease_id, **entry)


def extract_tissue_tpm(
    gtex_expressions: list[dict], tissue: str
) -> tuple[float | None, int | None, int | None]:
    """Return (median_tpm, 1-indexed rank by descending TPM, total tissue count) for ``tissue``.

    ``gtex_expressions`` is the raw list of ``{"tissue": ..., "median_tpm": ...}`` dicts
    from the GTEx/HPA archive blob's ``extra["gtex_expressions"]``. Returns
    ``(None, None, total)`` if ``tissue`` is not present in the list.
    """
    if not gtex_expressions:
        return None, None, None
    ordered = sorted(gtex_expressions, key=lambda t: t.get("median_tpm", 0.0), reverse=True)
    total = len(ordered)
    for i, t in enumerate(ordered):
        if t.get("tissue") == tissue:
            return t.get("median_tpm"), i + 1, total
    return None, None, total


def build_disease_tissue_expression_note(
    gtex_expressions: list[dict],
    disease_tissue_info: DiseaseTissueInfo | None,
    disease: str,
) -> str:
    """Deterministic grounding line: disease-relevant tissue TPM vs. top-TPM-rank tissues.

    Prevents the lens LLM from inferring disease-relevance from bulk-TPM rank alone.
    When the disease-tissue mapping is unknown, says so explicitly rather than
    silently letting the model fall back to TPM ranking.
    """
    if not gtex_expressions:
        return ""
    ordered = sorted(gtex_expressions, key=lambda t: t.get("median_tpm", 0.0), reverse=True)
    total = len(ordered)
    top3 = ", ".join(f"{t['tissue']}={t.get('median_tpm', 0.0):.1f}" for t in ordered[:3])

    if disease_tissue_info is None or not disease_tissue_info.gtex_tissues:
        return (
            f"Top-TPM tissues by bulk GTEx rank: {top3}. "
            f"DISEASE-TISSUE MAPPING UNKNOWN for '{disease}'. Do NOT infer disease "
            "relevance from this TPM ranking — bulk tissue rank is not a proxy for "
            "disease relevance. Derive the affected tissue/cell type from the disease "
            "biology described in the literature claims instead, and state that "
            "limitation if the claims don't resolve it either."
        )

    lines = [f"Top-TPM tissues by bulk GTEx rank: {top3}."]
    for tissue in disease_tissue_info.gtex_tissues:
        tpm, rank, _ = extract_tissue_tpm(gtex_expressions, tissue)
        if tpm is None:
            continue
        level = "LOW" if tpm < 5.0 else ("MODERATE" if tpm < 20.0 else "HIGH")
        lines.append(f"Disease-relevant tissue {tissue}: {tpm:.2f} TPM ({level}; rank {rank}/{total}).")

    cell_types = ", ".join(disease_tissue_info.cell_types)
    if cell_types:
        lines.append(f"Disease cell type(s): {cell_types}.")
    if disease_tissue_info.note:
        lines.append(disease_tissue_info.note.strip())
    lines.append(
        f"The tissues ranked highest by bulk TPM above are NOT necessarily relevant to "
        f"{disease} — only the disease-relevant tissue/cell type named above is. Do NOT "
        "describe a high-bulk-TPM tissue as 'relevant' to this disease unless it is named above."
    )
    return " ".join(lines)


def top_tpm_tissues(gtex_expressions: list[dict], n: int = 3) -> list[str]:
    """Return the names of the ``n`` highest-bulk-TPM tissues, descending."""
    if not gtex_expressions:
        return []
    ordered = sorted(gtex_expressions, key=lambda t: t.get("median_tpm", 0.0), reverse=True)
    return [t["tissue"] for t in ordered[:n] if t.get("tissue")]


# Phrases that assert disease relevance for a tissue. If one of these co-occurs
# with a high-bulk-TPM, non-disease tissue in the same sentence, the lens is
# (mis)using bulk-TPM rank as a proxy for disease relevance.
_TISSUE_RELEVANCE_PATTERN = re.compile(
    r"\b(disease[- ]relevant|relevant to (?:the )?disease|relevant tissue|target tissue|"
    r"affected tissue|disease tissue|primary (?:site|tissue|target)|site of disease|"
    r"key tissue|tissue of interest|disease-affected|where (?:the )?disease|"
    r"appropriately expressed|expressed where it matters)\b",
    re.IGNORECASE,
)


def apply_tissue_relevance_guard(
    text: str,
    top_tissues: list[str],
    disease_relevant_tissues: list[str],
    disease: str,
) -> str:
    """Annotate narrative/rationale that treats a high-bulk-TPM tissue as disease-relevant.

    Bulk GTEx TPM rank is NOT a proxy for disease relevance (the TRPC6×FSGS error:
    Lung/Esophagus rank above Kidney_Cortex by bulk TPM but are not relevant to FSGS).
    The pre-computed grounding note already tells the LLM which tissue matters; this is
    the post-LLM safety net that fires when the model still asserts a top-ranked,
    non-disease tissue is disease-relevant.

    Only fires when the curated disease-tissue mapping is known
    (``disease_relevant_tissues`` non-empty) — without ground truth we cannot assert a
    named tissue is irrelevant, so we defer to the prompt-level note instead.
    Annotates rather than silently rewriting, matching ``apply_constraint_guards``.
    """
    if not text or not top_tissues or not disease_relevant_tissues:
        return text

    relevant_lc = {t.lower() for t in disease_relevant_tissues}
    misused = [t for t in top_tissues if t.lower() not in relevant_lc]
    if not misused:
        return text

    flagged: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not _TISSUE_RELEVANCE_PATTERN.search(sentence):
            continue
        for tissue in misused:
            # GTEx names use underscores (e.g. "Adipose_Subcutaneous"); also match the
            # space-separated form a narrative is more likely to use.
            variants = {tissue, tissue.replace("_", " ")}
            if any(re.search(rf"\b{re.escape(v)}\b", sentence, re.IGNORECASE) for v in variants):
                if tissue not in flagged:
                    flagged.append(tissue)
                break

    if not flagged:
        return text

    names = ", ".join(flagged)
    rel = ", ".join(disease_relevant_tissues)
    return text + (
        f"\n[⚠ TISSUE RELEVANCE GUARD: The text ties high-bulk-TPM tissue(s) ({names}) "
        f"to relevance for {disease}, but bulk GTEx TPM rank is NOT a proxy for disease "
        f"relevance. The disease-relevant tissue(s)/cell type(s) here are: {rel}. A tissue "
        "ranking high by bulk TPM is not thereby disease-relevant — re-state relevance in "
        "terms of the disease-affected tissue/cell type above.]"
    )
