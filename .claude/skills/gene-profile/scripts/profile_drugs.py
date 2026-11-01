# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""One list of a target's drugs, from Open Targets known drugs.

Open Targets lists a drug once per indication and under salt forms; this merges them into
one row per drug, with its modality and one stage by a fixed rule: "Approved" when Open
Targets marks it approved, otherwise its highest clinical phase. FDA labels of the drug
add only its brand names (used to find its FAERS reports and to recognise it in prose).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_SALTS = {
    "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM", "HYDROCHLORIDE", "HCL", "HYDROBROMIDE", "BROMIDE",
    "CHLORIDE", "ANHYDROUS", "MONOHYDRATE", "DIHYDRATE",
}  # fmt: skip
_BIOLOGIC_SUFFIX_RE = re.compile(r"-[A-Z]{4}$")  # FDA's four-letter suffix: "NAME-LIGA"
_WORD_RE = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"
_MAX_ROWS = 25

STAGE_APPROVED = "Approved"
STAGE_NONE = "Stage not recorded"


def drug_key(name: str) -> str:
    """The name sources share: upper case, without a salt or ester or a biologic suffix."""
    words = str(name).upper().split()
    while len(words) > 1 and (words[-1] in _SALTS or words[-1].endswith("ATE")):
        words.pop()
    return _BIOLOGIC_SUFFIX_RE.sub("", " ".join(words))


def on_target(label: Mapping[str, Any], symbols: Iterable[str]) -> bool:
    """A label whose mechanism of action names the gene. Labels found by indication treat the
    disease, whatever their target."""
    text = str(label.get("mechanism_of_action") or "")
    return any(re.search(_WORD_RE.format(re.escape(s)), text, re.IGNORECASE) for s in symbols if s)


def candidates(
    known: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    symbols: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """The Open Targets known drugs of the target, merged across indications and salts; an
    on-target label adds its brand names to the drug it names."""
    out: dict[str, dict[str, Any]] = {}
    for d in known:
        if not d.get("drug_name"):
            continue
        e = out.setdefault(
            drug_key(d["drug_name"]),
            {"name": drug_key(d["drug_name"]), "modality": "", "max_phase": None,
             "approved": False, "brands": []},
        )  # fmt: skip
        e["modality"] = e["modality"] or str(d.get("drug_type") or "")
        phase = d.get("max_phase")
        if isinstance(phase, int | float) and (e["max_phase"] is None or phase > e["max_phase"]):
            e["max_phase"] = phase
        e["approved"] = e["approved"] or bool(d.get("is_approved"))
    symbols = list(symbols)
    for label in labels:
        key = drug_key(label.get("drug_name") or "")
        if key in out and on_target(label, symbols):
            out[key]["brands"] += [str(b).upper() for b in label.get("brand_names") or []]
    return out


def _stage(e: Mapping[str, Any]) -> str:
    if e.get("approved"):
        return STAGE_APPROVED
    phase = e.get("max_phase")
    return f"Phase {phase:g}" if isinstance(phase, int | float) and phase > 0 else STAGE_NONE


def rows(entries: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per drug, approved first, then by phase and name."""
    out = [
        {
            "name": e["name"],
            "brands": sorted(set(e.get("brands") or [])),
            "modality": e.get("modality") or "",
            "stage": _stage(e),
            "approved": bool(e.get("approved")),
            "max_phase": e.get("max_phase"),
        }
        for e in entries.values()
    ]
    return sorted(out, key=lambda r: (not r["approved"], -(r.get("max_phase") or 0), r["name"]))


def approved_names(data: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """(name, brands) of the approved drugs."""
    return [(r["name"], r["brands"]) for r in data.get("drugs") or [] if r.get("approved")]


def named(row: Mapping[str, Any], text: str) -> bool:
    """Whether the text names the drug, by its name or a brand."""
    names = [row.get("name"), *(row.get("brands") or [])]
    return any(n and re.search(_WORD_RE.format(re.escape(n)), text, re.IGNORECASE) for n in names)


def _display(row: Mapping[str, Any]) -> str:
    brands = ", ".join(b.title() for b in row.get("brands") or [])
    return f"{row['name'].title()} ({brands})" if brands else row["name"].title()


def landscape_md(data: Mapping[str, Any], cite: str) -> str:
    """Every drug of the target with its modality and stage, from the records."""
    drugs = list(data.get("drugs") or [])
    if not drugs:
        return ""
    out = ["| Drug | Modality | Stage | Refs |", "|---|---|---|---|"]
    for r in drugs[:_MAX_ROWS]:
        cells = [_display(r), r.get("modality") or "—", r["stage"]]
        out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + f" | {cite} |")
    if len(drugs) > _MAX_ROWS:
        out += ["", f"Top {_MAX_ROWS} of {len(drugs)} drugs, most advanced first. {cite}"]
    out += [
        "",
        "Stages from Open Targets known drugs: 'Approved' when Open Targets marks the drug "
        f"approved, otherwise its highest clinical phase. {cite}",
    ]
    return "\n".join(out)
