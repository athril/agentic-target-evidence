# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Evidence-type hierarchy — deterministic, disease-class-conditional weights
for structured evidence claims (config/evidence_hierarchy.yaml).

Without this, a DepMap dependency claim and a Mendelian ClinVar variant would
rank the same at truncation. This module provides the deterministic weight
function used both to rank/truncate claims before they reach the LLM and to
build the prompt-level evidence-strength ledger (see docs/lenses.md).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from schemas.evidence import EvidenceType
from services.evidence.disease_class import DiseaseClass

_CONFIG_PATH = Path("config/evidence_hierarchy.yaml")
# Evidence type present in EvidenceType but absent from the config (should not
# happen in practice — every structured type used by a lens is configured).
_DEFAULT_WEIGHT = 0.5
_DEFAULT_LLM_PRIOR_WEIGHT = 0.05


@functools.lru_cache(maxsize=1)
def _load_config(path_str: str) -> dict[str, Any]:
    import yaml

    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def reload_evidence_hierarchy() -> None:
    """Drop the cached config so the next evidence_weight() call re-reads disk."""
    _load_config.cache_clear()


def _classes_to_strs(disease_classes: Iterable[DiseaseClass | str] | None) -> set[str]:
    return {c.value if isinstance(c, DiseaseClass) else str(c) for c in (disease_classes or ())}


def infer_evidence_subtype(
    evidence_type: EvidenceType, claim_text: str, *, path: Path | None = None
) -> str | None:
    """Best-effort subtype sniff from claim text against `subtypes[*].tokens`.

    CoreClaim carries no native subtype field — this keyword match is the only
    signal available at claim-ranking time without a join back to source Evidence.
    Returns None when no configured token matches (the bare evidence_type weight
    applies).
    """
    data = _load_config(str(path or _CONFIG_PATH))
    subtypes: dict[str, Any] = (data.get(evidence_type.value) or {}).get("subtypes") or {}
    text_lc = (claim_text or "").lower()
    for subtype, cfg in subtypes.items():
        if any(tok in text_lc for tok in (cfg.get("tokens") or [])):
            return str(subtype)
    return None


def evidence_weight(
    evidence_type: EvidenceType,
    subtype: str | None,
    disease_classes: Iterable[DiseaseClass | str] | None,
    *,
    path: Path | None = None,
) -> float:
    """Deterministic 0-1 weight for a structured-evidence claim.

    Disease-class-conditional: a `by_class` override fires when any of
    `disease_classes` matches, e.g. DepMap weighs 1.0 for oncology vs. 0.25
    elsewhere. Falls back to the subtype's (or evidence_type's) flat `default`.
    """
    data = _load_config(str(path or _CONFIG_PATH))
    type_cfg: dict[str, Any] = data.get(evidence_type.value) or {}
    classes = _classes_to_strs(disease_classes)

    cfg = type_cfg
    if subtype:
        sub_cfg = (type_cfg.get("subtypes") or {}).get(subtype)
        if sub_cfg:
            cfg = sub_cfg

    by_class: dict[str, Any] = cfg.get("by_class") or {}
    for cls in classes:
        if cls in by_class:
            return float(by_class[cls])
    return float(cfg.get("default", _DEFAULT_WEIGHT))


def llm_prior_weight(*, path: Path | None = None) -> float:
    """Floor weight for uncited model prior knowledge (the evidence-strength
    ledger's explicit "cannot raise confidence" rule)."""
    data = _load_config(str(path or _CONFIG_PATH))
    return float(data.get("llm_prior_weight", _DEFAULT_LLM_PRIOR_WEIGHT))
