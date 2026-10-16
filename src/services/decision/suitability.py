# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Target-suitability score — deterministic, config-driven (config/scoring.yaml).

Computed from the lens verdicts, the candidate on-target liabilities and the Mendelian
causality floor; the experiment agent's LLM only explains it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_DEFAULT_FLOOR = 70
_DEFAULT_CONFIG_PATH = Path("config/scoring.yaml")


# ---------------------------------------------------------------------------
# Deterministic suitability score
# ---------------------------------------------------------------------------
#
# The score was a number the experiment LLM chose, which drifted between runs with the
# same lens verdicts and ignored liabilities the report had flagged. It is now computed
# from the lens verdicts, the candidate liabilities and the Mendelian floor, with every
# weight in config/scoring.yaml; the LLM only writes the rationale around it.

_DEFAULT_LENS_WEIGHTS: dict[str, float] = {
    "genetics": 0.25,
    "clinical": 0.20,
    "safety": 0.20,
    "biology": 0.15,
    "regulatory": 0.10,
    "commercial": 0.10,
}
_DEFAULT_LIABILITY_POINTS: dict[str, float] = {"high": 4.0, "medium": 1.5, "low": 0.0}
_DEFAULT_LIABILITY_CAP = 15.0
_DEFAULT_HUMAN_EXPOSURE_FACTOR = 0.5
_DEFAULT_BANDS: list[tuple[int, str]] = [
    (75, "PROCEED"),
    (60, "PROCEED WITH CAUTION"),
    (45, "INVESTIGATE FURTHER"),
    (0, "DEPRIORITISE"),
]
_SIGN = {"support": 1.0, "oppose": -1.0}
_DEFAULT_PRECEDENT_GATES: dict[str, dict[str, float]] = {
    "validated": {"min": 75},
    "opposed": {"max": 30},
    "failed": {"max": 44},
    "stalled": {"max": 59},
}
_DEFAULT_DIRECTION_GATE: dict[str, float] = {"max": 30, "min_against": 0.5}
_GATE_REASON = {
    "validated": "a same-direction drug is approved in this indication",
    "opposed": "drugs acting in the opposite direction are the ones developed in this indication",
    "failed": "same-direction trials in this indication stopped for lack of efficacy or safety",
    "stalled": "same-direction phase 3 programmes in this indication ended without approval",
}


class ScoringConfig(BaseModel):
    lens_weights: dict[str, float] = dict(_DEFAULT_LENS_WEIGHTS)
    liability_points: dict[str, float] = dict(_DEFAULT_LIABILITY_POINTS)
    liability_penalty_cap: float = _DEFAULT_LIABILITY_CAP
    human_exposure_factor: float = _DEFAULT_HUMAN_EXPOSURE_FACTOR
    mendelian_floor: int = _DEFAULT_FLOOR
    bands: list[tuple[int, str]] = list(_DEFAULT_BANDS)
    precedent_gates: dict[str, dict[str, float]] = {
        k: dict(v) for k, v in _DEFAULT_PRECEDENT_GATES.items()
    }
    direction_gate: dict[str, float] = dict(_DEFAULT_DIRECTION_GATE)


def load_scoring_config(path: Path | None = None) -> ScoringConfig:
    path = path or _DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    cfg = ScoringConfig()
    return cfg.model_copy(
        update={
            "lens_weights": {**cfg.lens_weights, **(data.get("lens_weights") or {})},
            "liability_points": {**cfg.liability_points, **(data.get("liability_points") or {})},
            "liability_penalty_cap": float(
                data.get("liability_penalty_cap", cfg.liability_penalty_cap)
            ),
            "human_exposure_factor": float(
                data.get("human_exposure_factor", cfg.human_exposure_factor)
            ),
            "mendelian_floor": int(
                data.get("mendelian_causality_score_floor", cfg.mendelian_floor)
            ),
            "bands": sorted(((int(b["min"]), str(b["label"])) for b in data["bands"]), reverse=True)
            if data.get("bands")
            else cfg.bands,
            "precedent_gates": {
                str(k): {str(b): float(x) for b, x in (v or {}).items()}
                for k, v in (data.get("precedent_gates") or cfg.precedent_gates).items()
            },
            "direction_gate": {
                **cfg.direction_gate,
                **{str(k): float(v) for k, v in (data.get("direction_gate") or {}).items()},
            },
        }
    )


class LensContribution(BaseModel):
    lens: str
    verdict: str
    confidence: float
    weight: float
    points: float  # signed contribution to the 0–100 score, before liabilities


class SuitabilityScore(BaseModel):
    score: int
    band: str
    evidence_score: float  # 0–100 from the lens verdicts alone
    liability_penalty: float
    mendelian_floor_applied: bool
    # Therapeutic-hypothesis precedent status and the gate it applied ("" when none).
    precedent_status: str = ""
    precedent_gate: str = ""
    direction_gate: str = ""  # "cap 30" when the direction-of-effect gate applied
    lenses_with_verdict: int
    contributions: list[LensContribution]
    notes: list[str]


def compute_suitability(
    lens_verdicts: list[dict[str, Any]],
    *,
    liabilities: list[dict[str, Any]] | None = None,
    human_exposure: bool = False,
    mendelian_grade: bool = False,
    precedent_status: str = "",
    direction_verdict: str = "",
    direction_against: float = 0.0,
    config: ScoringConfig | None = None,
) -> SuitabilityScore:
    """Score = 50 + 50 × (weighted mean of signed lens confidences) − liability penalty.

    A lens that is missing or ``insufficient_evidence`` keeps its weight with a zero
    signal, so missing evidence pulls the score toward 50 (uncertain) instead of being
    ignored. Liabilities subtract points by severity, halved (``human_exposure_factor``)
    when on-target drugs have human clinical exposure, which supersedes animal data. The
    Mendelian floor lifts the score only when safety does not oppose and no high-severity
    liability is open. Last, ``precedent_status`` (services/evidence/drug_precedent.py)
    bounds the score by what same- and opposite-direction drugs did in the indication,
    and the direction-of-effect gate caps it when the evidence on which way of changing
    the target helps (``direction_verdict`` / ``direction_against``,
    services/evidence/direction_of_effect.py) contradicts the requested direction.
    """
    cfg = config or load_scoring_config()
    by_lens = {str(v.get("lens")): v for v in lens_verdicts if v.get("lens")}
    total_w = sum(cfg.lens_weights.values()) or 1.0
    contributions: list[LensContribution] = []
    signal = 0.0
    with_verdict = 0
    for lens, w in cfg.lens_weights.items():
        v = by_lens.get(lens) or {}
        verdict = str(v.get("overall_verdict") or "missing")
        conf = float(v.get("confidence") or 0.0)
        s = _SIGN.get(verdict, 0.0) * max(0.0, min(1.0, conf))
        if verdict in ("support", "oppose", "neutral"):
            with_verdict += 1
        signal += w * s
        contributions.append(
            LensContribution(
                lens=lens,
                verdict=verdict,
                confidence=round(conf, 3),
                weight=round(w / total_w, 3),
                points=round(50.0 * w * s / total_w, 1),
            )
        )
    evidence_score = 50.0 + 50.0 * signal / total_w

    items = liabilities or []
    raw_penalty = sum(cfg.liability_points.get(str(li.get("severity")), 0.0) for li in items)
    factor = cfg.human_exposure_factor if human_exposure else 1.0
    penalty = min(cfg.liability_penalty_cap, raw_penalty * factor)
    notes: list[str] = []
    if items:
        notes.append(
            f"{len(items)} candidate liabilit{'y' if len(items) == 1 else 'ies'} "
            f"(−{penalty:.1f})"
            + ("; halved for human clinical exposure to on-target drugs" if human_exposure else "")
        )

    score = evidence_score - penalty
    floor_applied = False
    safety = str((by_lens.get("safety") or {}).get("overall_verdict") or "")
    high_open = any(li.get("severity") == "high" for li in items) and not human_exposure
    if mendelian_grade and score < cfg.mendelian_floor:
        if safety == "oppose" or high_open:
            notes.append(
                "Mendelian floor not applied: safety opposes or a high-severity liability is open"
            )
        else:
            score = float(cfg.mendelian_floor)
            floor_applied = True
            notes.append(f"Mendelian causality floor applied ({cfg.mendelian_floor})")
    gate = cfg.precedent_gates.get(precedent_status) or {}
    gate_applied = ""
    if "min" in gate and score < gate["min"]:
        score, gate_applied = float(gate["min"]), f"floor {gate['min']:g}"
    elif "max" in gate and score > gate["max"]:
        score, gate_applied = float(gate["max"]), f"cap {gate['max']:g}"
    if gate_applied:
        notes.append(
            f"Clinical precedent {gate_applied} applied: "
            f"{_GATE_REASON.get(precedent_status, precedent_status)}"
        )
    dgate = cfg.direction_gate
    direction_applied = ""
    if (
        direction_verdict == "contradicts"
        and direction_against >= dgate.get("min_against", 0.5)
        and "max" in dgate
        and score > dgate["max"]
    ):
        score, direction_applied = float(dgate["max"]), f"cap {dgate['max']:g}"
        notes.append(
            f"Direction-of-effect {direction_applied} applied: the evidence on which way of "
            f"changing the target helps contradicts the requested direction "
            f"(strength {direction_against:.2f})"
        )
    if with_verdict < len(cfg.lens_weights):
        notes.append(
            f"{with_verdict}/{len(cfg.lens_weights)} lenses reached a verdict; "
            "missing lenses count as uncertain (50)"
        )

    final = int(round(max(0.0, min(100.0, score))))
    band = next((label for lo, label in cfg.bands if final >= lo), cfg.bands[-1][1])
    return SuitabilityScore(
        score=final,
        band=band,
        evidence_score=round(evidence_score, 1),
        liability_penalty=round(penalty, 1),
        mendelian_floor_applied=floor_applied,
        precedent_status=precedent_status,
        precedent_gate=gate_applied,
        direction_gate=direction_applied,
        lenses_with_verdict=with_verdict,
        contributions=contributions,
        notes=notes,
    )
