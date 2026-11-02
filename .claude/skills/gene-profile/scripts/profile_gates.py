# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Five gates between a draft and a released report, computed from what the run recorded.

Collecting evidence, validating a claim against it and drawing a sound conclusion are separate
acts, and passing one says nothing of the next. Each check maps to one gate; the release gate
requires the others and an unblocked scientific review. A report that does not pass says so
under its title, whatever the renderer was allowed to produce.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from profile_claims import REVIEW_FILE, ledger_summary, review_problems
from profile_retrieval import failed_dimensions, matrix, uncovered_dimensions
from profile_schema import Manifest
from profile_writer import load_writer_file

PASS, REVIEW, FAIL, NOT_RUN = "pass", "pass with warnings", "fail", "not run"
GATE_TITLES = {
    "retrieval": "Retrieval completeness",
    "claims": "Claim validation",
    "biology": "Biological consistency",
    "numbers": "Quantitative and method validation",
    "release": "Scientific release review",
}
GATE_CODES: dict[str, set[str]] = {
    "retrieval": {"E_THERAPY_NOT_DISCUSSED", "W_OUTCOME_UNREAD", "W_DRUG_NO_READOUT"},
    "claims": {
        "E_CLAIM_AUDIT", "E_CLAIM_CHECK", "E_UNCITED", "E_UNKNOWN_KEY", "E_UNVERIFIED",
        "E_TIER3_SNAPSHOT", "E_VARIANT_FUNCTION", "E_HOW_WE_KNOW", "E_OPEN_QUESTION",
        "W_OFF_TARGET_CITATION", "W_TIER3_ONLY", "W_UNGROUNDED_NAME", "W_VERB_NOT_IN_SOURCE",
        "W_RECORD_ONLY_MECHANISM", "W_INFERENCE_IN_FACT", "W_RECLASSIFICATION",
    },
    "biology": {
        "E_MECHANISM", "E_CONSTRAINT_WORDING", "E_GUARD_CONSTRAINT", "E_SAFETY_INFERENCE",
        "W_CROSS_KIND_CONTRADICTION", "W_STRENGTH_PROSE", "W_SAFETY_EXTRAPOLATION",
        "W_OVERCLAIM_SAFETY", "W_PROVEN", "W_ESSENTIALITY_SCOPE", "W_SCREEN_INFERENCE",
        "W_STRING_BINDING", "W_ASSOCIATION_COUNT", "W_FAERS_INFERENCE", "E_GUARD_COMMERCIAL",
        "E_SCOPE_CONTRADICTION",
    },
    "numbers": {
        "E_ANCHOR", "E_ANCHOR_UNIT", "E_ANCHOR_NOT_CITED", "E_QUERY_SCOPE", "W_BARE_NUMBER",
        "W_NUMBER_NOT_IN_SOURCE",
    },
}  # fmt: skip
_REASONS_SHOWN = 3


def _gate(errors: Sequence[str], warnings: Sequence[str]) -> dict[str, Any]:
    status = FAIL if errors else REVIEW if warnings else PASS
    return {"status": status, "errors": list(errors), "warnings": list(warnings)}


def _messages(issues: Iterable[Any], codes: set[str]) -> list[str]:
    return [f"{i.code}: {i.message}" for i in issues if i.code in codes]


def evaluate(rep: Any, manifest: Manifest, run_dir: Path) -> dict[str, dict[str, Any]]:
    """Every gate's status with the reasons behind it."""
    gates = {
        name: _gate(_messages(rep.errors, codes), _messages(rep.warnings, codes))
        for name, codes in GATE_CODES.items()
    }
    rows = matrix(manifest, run_dir)
    gates["retrieval"]["errors"] += [f"search failed: {d}" for d in failed_dimensions(rows)]
    gates["retrieval"]["uncovered"] = uncovered_dimensions(rows)
    if gates["retrieval"]["errors"]:
        gates["retrieval"]["status"] = FAIL

    if manifest.depth == "quick":
        gates["claims"] = {"status": NOT_RUN, "errors": [], "warnings": [],
                           "note": "quick depth runs no claim checks or audit"}  # fmt: skip
        gates["release"] = {"status": NOT_RUN, "errors": [], "warnings": [],
                            "note": "quick depth has no scientific review"}  # fmt: skip
        return gates

    entry = load_writer_file(run_dir, REVIEW_FILE)
    blocked = [f"write {REVIEW_FILE}"] if entry is None or entry == [] else review_problems(entry)
    earlier = [GATE_TITLES[k] for k in ("retrieval", "claims", "biology", "numbers")
               if gates[k]["status"] == FAIL]  # fmt: skip
    errors = [*blocked, *(f"gate failed: {t}" for t in earlier)]
    findings = entry.get("findings") if isinstance(entry, dict) else None
    still_open = [
        f"open {f.get('severity')} finding: {str(f.get('finding'))[:120]}"
        for f in findings or []
        if isinstance(f, dict) and f.get("resolution") == "open"
    ]
    gates["release"] = _gate(errors, still_open)
    return gates


def released(gates: Mapping[str, Mapping[str, Any]]) -> bool:
    return gates["release"]["status"] in (PASS, REVIEW)


def release_line(gates: Mapping[str, Mapping[str, Any]]) -> str:
    """The line under the title. It states the report's standing plainly."""
    if released(gates):
        waiting = [GATE_TITLES[k] for k, g in gates.items() if g["status"] == REVIEW]
        tail = f" Warnings to read: {', '.join(waiting)}." if waiting else ""
        return f"**Release status: released.** All five quality gates passed.{tail}"
    if gates["release"]["status"] == NOT_RUN:
        return (
            "**Release status: quick profile.** No claim audit or scientific review was run, so "
            "its claims are cited but not validated."
        )
    reasons: list[str] = []
    for name, gate in gates.items():
        if name != "release" and gate["status"] == FAIL:
            reasons.append(GATE_TITLES[name])
    blocked = [e for e in gates["release"]["errors"] if not e.startswith("gate failed")]
    reasons += blocked[:_REASONS_SHOWN]
    return (
        "**Release status: not released.** Read this as a draft: its claims are cited but "
        f"have not all been validated. Unresolved: {'; '.join(reasons)}."
    )


def gates_md(
    gates: Mapping[str, Mapping[str, Any]], claims: Sequence[Mapping[str, Any]]
) -> list[str]:
    """The appendix section: each gate and how the claims stand."""
    out = ["| Gate | Status | What it found |", "|---|---|---|"]
    for name, title in GATE_TITLES.items():
        gate = gates[name]
        notes = [*gate["errors"], *gate["warnings"]]
        if gate.get("note"):
            notes.append(gate["note"])
        if gate.get("uncovered"):
            notes.append(f"no search covers: {', '.join(gate['uncovered'])}")
        text = "; ".join(n.replace("|", "/")[:160] for n in notes[:_REASONS_SHOWN])
        more = f" (+{len(notes) - _REASONS_SHOWN} more)" if len(notes) > _REASONS_SHOWN else ""
        out.append(f"| {title} | {gate['status']} | {(text + more) or '—'} |")
    counts = ledger_summary(claims)
    if counts:
        tally = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        out += [
            "",
            f"Claims in `claim_ledger.json`: {len(claims)} ({tally}). An audited claim was judged "
            "against its cited abstracts by a reader who did not write it; a verdict covers those "
            "abstracts, not the full papers or the rest of the literature.",
        ]
    return out
