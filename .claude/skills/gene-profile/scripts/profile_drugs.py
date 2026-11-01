# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""One list of a target's drugs, built from every source that names them.

Sources key drugs differently and lag each other: an aggregator may not list a new approval,
a label may be filed under its brand name, and a biologic carries a four-letter suffix in one
source and none in another. Each drug's stage is set here, by a fixed rule, so nothing
downstream has to reconcile them: the regulator's record outranks the aggregator's, and a
lookup that failed is reported as not checked, never as no approval.
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
_REASON_CHARS = 90
_MAX_ROWS = 25

STAGE_US = "US approved"
STAGE_OTHER = "Approved, no US application found"
STAGE_LABEL = "US label, no application found"
STAGE_APPROVED_UNCHECKED = "Approved, US status not checked"
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


def _stops(entry: Mapping[str, Any]) -> list[str]:
    """Why the drug's stopped trials stopped, in the record's own words."""
    return [
        str(r.get("why"))
        for r in entry.get("stop_reports") or []
        if r.get("why") and str(r.get("status") or "").upper() in ("TERMINATED", "WITHDRAWN")
    ]


def candidates(
    known: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    symbols: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Every drug named for the target, by shared name: Open Targets rows merged across
    indications and salts, then on-target labels Open Targets does not list."""
    out: dict[str, dict[str, Any]] = {}

    def entry(name: str) -> dict[str, Any]:
        return out.setdefault(
            drug_key(name),
            {"name": drug_key(name), "modality": "", "max_phase": None, "approved": False,
             "labelled": False, "brands": [], "stops": []},
        )  # fmt: skip

    for d in known:
        if not d.get("drug_name"):
            continue
        e = entry(d["drug_name"])
        e["modality"] = e["modality"] or str(d.get("drug_type") or "")
        phase = d.get("max_phase")
        if isinstance(phase, int | float) and (e["max_phase"] is None or phase > e["max_phase"]):
            e["max_phase"] = phase
        e["approved"] = e["approved"] or bool(d.get("is_approved"))
        e["stops"] += _stops(d)
    symbols = list(symbols)
    for label in labels:
        if not label.get("drug_name") or not on_target(label, symbols):
            continue
        e = entry(label["drug_name"])
        e["labelled"] = True
        e["brands"] += [str(b).upper() for b in label.get("brand_names") or []]
    return out


def _stage(e: Mapping[str, Any]) -> str:
    approval = e.get("approval") or {}
    if approval:
        stage = f"{STAGE_US} {approval.get('approval_date')}"
        return f"{stage}; discontinued" if approval.get("discontinued") else stage
    if e.get("lookup_failed"):
        return STAGE_APPROVED_UNCHECKED if e.get("approved") else _phase(e)
    if e.get("approved"):
        return STAGE_OTHER
    return STAGE_LABEL if e.get("labelled") else _phase(e)


def _phase(e: Mapping[str, Any]) -> str:
    phase = e.get("max_phase")
    return f"Phase {phase:g}" if isinstance(phase, int | float) and phase > 0 else STAGE_NONE


def _rank(row: Mapping[str, Any]) -> tuple[int, float, str, str]:
    approved = row["us_approved"]
    return (
        0 if approved else 1 if row["approved"] else 2,
        -(row.get("max_phase") or 0),
        str(row.get("approval_date") or ""),
        row["name"],
    )


def rows(
    entries: Mapping[str, Mapping[str, Any]],
    approvals: Mapping[str, Mapping[str, Any] | None],
    failed: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """One row per drug: entries that resolve to the same US application are one drug, named
    by the shared name of its ingredient. ``approvals`` is by entry key, None for no US
    application; ``failed`` holds the keys whose lookup failed."""
    failed = set(failed)
    merged: dict[str, dict[str, Any]] = {}
    for key, entry in entries.items():
        approval = dict(approvals.get(key) or {})
        ingredient = drug_key((approval.get("ingredients") or [key])[0])
        group = str(approval.get("application_number") or ingredient)
        into = merged.setdefault(
            group,
            {**entry, "name": ingredient, "brands": [], "stops": [], "modality": "",
             "max_phase": None, "approved": False, "labelled": False, "approval": {}},
        )  # fmt: skip
        into["approval"] = into["approval"] or approval
        into["lookup_failed"] = into.get("lookup_failed") or key in failed
        into["modality"] = into["modality"] or entry.get("modality") or ""
        into["approved"] = into["approved"] or bool(entry.get("approved"))
        into["labelled"] = into["labelled"] or bool(entry.get("labelled"))
        if entry.get("max_phase") is not None:
            into["max_phase"] = max(into["max_phase"] or 0, entry["max_phase"])
        into["brands"] += list(entry.get("brands") or [])
        into["stops"] += list(entry.get("stops") or [])
    out = []
    for e in merged.values():
        approval = e["approval"]
        out.append(
            {
                "name": e["name"],
                "brands": sorted({*e["brands"], *(approval.get("brand_names") or [])}),
                "modality": e["modality"],
                "route": approval.get("route") or "",
                "stage": _stage(e),
                "us_approved": bool(approval),
                "approved": bool(approval) or bool(e["approved"]),
                "approval_date": approval.get("approval_date") or "",
                "application": approval.get("application_number") or "",
                "sponsor": approval.get("sponsor") or "",
                "max_phase": e["max_phase"],
                "stops": list(dict.fromkeys(e["stops"])),
                "source_link": approval.get("source_link") or "",
            }
        )
    return sorted(out, key=_rank)


def approved_names(data: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """(name, brands) of the approved drugs, US approvals first."""
    return [(r["name"], r["brands"]) for r in data.get("drugs") or [] if r.get("approved")]


def named(row: Mapping[str, Any], text: str) -> bool:
    """Whether the text names the drug, by its name or a brand."""
    names = [row.get("name"), *(row.get("brands") or [])]
    return any(n and re.search(_WORD_RE.format(re.escape(n)), text, re.IGNORECASE) for n in names)


def _display(row: Mapping[str, Any]) -> str:
    brands = ", ".join(b.title() for b in row.get("brands") or [])
    return f"{row['name'].title()} ({brands})" if brands else row["name"].title()


def landscape_md(data: Mapping[str, Any], cite: str) -> str:
    """Every drug of the target with its stage, from the records."""
    drugs = list(data.get("drugs") or [])
    if not drugs:
        return ""
    out = [
        "| Drug | Modality | Route | Stage | Sponsor | Application | Trial stopped | Refs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in drugs[:_MAX_ROWS]:
        stops = [] if r.get("approved") else r.get("stops") or []
        stop = stops[0][:_REASON_CHARS].replace("|", "/") if stops else "—"
        if len(stops) > 1:
            stop += f" (+{len(stops) - 1} more)"
        cells = [
            _display(r),
            r.get("modality") or "not in Open Targets",
            (r.get("route") or "—").title(),
            r["stage"],
            (r.get("sponsor") or "—").title(),
            r.get("application") or "—",
            stop,
        ]
        out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + f" | {cite} |")
    if len(drugs) > _MAX_ROWS:
        out += ["", f"Top {_MAX_ROWS} of {len(drugs)} drugs, most advanced first. {cite}"]
    out += [
        "",
        "US approval and its date come from Drugs@FDA, the original application of the drug's "
        "ingredient; other stages from Open Targets. 'No US application found' means the "
        "ingredient has no NDA or BLA there under this name, which leaves open where else it "
        "is approved. 'Trial stopped' is a stopped trial's own reason, not the fate of the "
        f"programme. {cite}",
    ]
    return "\n".join(out)
