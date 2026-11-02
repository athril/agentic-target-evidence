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
_SECTION_HEAD_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:\w+ ){0,3}mechanism of action\s*:?\s*", re.I
)
_SENTENCE_RE = re.compile(r"(?<=[.;])\s+(?=[A-Z])")
_MAX_ROWS = 25

STAGE_APPROVED = "Approved"
STAGE_NONE = "Stage not recorded"


def drug_key(name: str) -> str:
    """The name sources share: upper case, without a salt or ester or a biologic suffix."""
    words = str(name).upper().split()
    while len(words) > 1 and (words[-1] in _SALTS or words[-1].endswith("ATE")):
        words.pop()
    return _BIOLOGIC_SUFFIX_RE.sub("", " ".join(words))


def _names(text: str, name: str) -> bool:
    return bool(re.search(_WORD_RE.format(re.escape(name)), text, re.IGNORECASE))


def on_target(label: Mapping[str, Any], symbols: Iterable[str]) -> bool:
    """A label whose drug acts on the gene. A mechanism section opens with what the drug does
    and goes on to pathways and biomarkers, so naming the gene anywhere is not enough: an
    inhibitor of one kinase can name a tumour suppressor whose loss activates it. The gene
    must be in the first sentence, or in the second beside the drug's own name."""
    text = _SECTION_HEAD_RE.sub("", str(label.get("mechanism_of_action") or ""))
    first, second = [*_SENTENCE_RE.split(text, maxsplit=2), "", ""][:2]
    drug = drug_key(str(label.get("drug_name") or ""))
    for symbol in filter(None, symbols):
        if _names(first, symbol) or (_names(second, symbol) and drug and _names(second, drug)):
            return True
    return False


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
             "approved": False, "brands": [], "action": "", "mechanism": ""},
        )  # fmt: skip
        e["modality"] = e["modality"] or str(d.get("drug_type") or "")
        phase = d.get("max_phase")
        if isinstance(phase, int | float) and (e["max_phase"] is None or phase > e["max_phase"]):
            e["max_phase"] = phase
        e["approved"] = e["approved"] or bool(d.get("is_approved"))
        # Which way the drug acts on the target: an inhibitor and a potentiator of the same
        # protein are opposite designs. Sparse in the source, so never filled in by inference.
        e["action"] = e["action"] or str(d.get("action_type") or "").lower()
        e["mechanism"] = e["mechanism"] or str(d.get("mechanism_of_action") or "")
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
            "action": e.get("action") or "",
            "mechanism": e.get("mechanism") or "",
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
    return any(n and _names(text, n) for n in names)


def _display(row: Mapping[str, Any]) -> str:
    brands = ", ".join(b.title() for b in row.get("brands") or [])
    return f"{row['name'].title()} ({brands})" if brands else row["name"].title()


# Which way an Open Targets action_type moves the target's activity. Only used to report the
# drug's own annotation beside the gene's direction of effect; never to infer one from the other.
_RAISES = ("activator", "agonist", "positive modulator", "positive allosteric modulator",
           "opener", "stabiliser", "stabilizer", "potentiator")  # fmt: skip
_LOWERS = ("inhibitor", "antagonist", "negative modulator", "negative allosteric modulator",
           "blocker", "degrader", "disrupting agent", "inverse agonist")  # fmt: skip


def action_direction(action: str) -> str:
    """ "raises", "lowers" or "" for an action this does not classify."""
    text = (action or "").lower()
    if any(w in text for w in _RAISES):
        return "raises"
    if any(w in text for w in _LOWERS):
        return "lowers"
    return ""


def action_text(row: Mapping[str, Any]) -> str:
    """The drug's annotated action on the target, as the record words it."""
    action = str(row.get("action") or "").strip()
    if not action:
        return "not stated"
    direction = action_direction(action)
    return f"{action.lower()} ({direction} activity)" if direction else action.lower()


def landscape_md(data: Mapping[str, Any], cite: str) -> str:
    """Every drug of the target with its modality and stage, from the records."""
    drugs = list(data.get("drugs") or [])
    if not drugs:
        return ""
    out = ["| Drug | Modality | Action on the target | Stage | Refs |", "|---|---|---|---|---|"]
    for r in drugs[:_MAX_ROWS]:
        cells = [_display(r), r.get("modality") or "—", action_text(r), r["stage"]]
        out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + f" | {cite} |")
    if len(drugs) > _MAX_ROWS:
        out += ["", f"Top {_MAX_ROWS} of {len(drugs)} drugs, most advanced first. {cite}"]
    out += [
        "",
        "Stages from Open Targets known drugs: 'Approved' when Open Targets marks the drug "
        "approved, otherwise its highest clinical phase. The action is Open Targets' own "
        "annotation, and 'not stated' means the record carries none — not that the drug acts in "
        "some other way. Read it against the direction of effect in Human genetics: an inhibitor "
        "of a gene whose disease variants are loss of function is a different design, not a "
        f"correction. {cite}",
    ]
    return "\n".join(out)
