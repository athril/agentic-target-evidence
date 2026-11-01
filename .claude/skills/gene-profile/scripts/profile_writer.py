# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What the writer (the LLM) supplies beside ``draft.md``, validated and rendered here.

* ``how_we_know.json``: 3–5 core claims, each with the source keys that support it, those
  that contradict it, and those that bear on it without testing the same question (with the
  reason). The claims are the LLM's choice; the table's cells are counted from the records of
  each cited paper (design, species, last author, stated n: profile_strength.py), so how
  strong a claim is never rests on the LLM's word.

Each file's problems are reported by check_profile.py; a block whose file is missing renders
its empty state.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from profile_strength import KIND_TITLES, KINDS, key_kind

HOW_FILE = "how_we_know.json"
MIN_CLAIMS, MAX_CLAIMS = 3, 5


def load_writer_file(run_dir: Path, name: str) -> Any:
    """The file's JSON, [] when the run has none, None when it is not valid JSON."""
    path = run_dir / name
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def _keys(entry: Mapping[str, Any], side: str) -> list[str]:
    return [str(k).removeprefix("@") for k in entry.get(side) or [] if str(k).strip()]


# ── how we know ─────────────────────────────────────────────────────────────────────


def _not_comparable(entry: Mapping[str, Any]) -> list[dict[str, str]]:
    """The claim's ``not_comparable`` sources: each a key and why it tests something else."""
    items = entry.get("not_comparable") or []
    return [
        {"key": str(i.get("key") or "").removeprefix("@"), "why": str(i.get("why") or "").strip()}
        for i in (items if isinstance(items, list) else [])
        if isinstance(i, dict)
    ]


def how_problems(entries: Any, sources: Mapping[str, Any]) -> list[str]:
    """What is wrong with ``how_we_know.json``: not a list of claims, a claim without a
    supporting key, a key not in sources.json, a not-comparable source without its reason,
    more claims than the Snapshot holds."""
    if not isinstance(entries, list):
        return [f"{HOW_FILE} must be a JSON list of claims"]
    out: list[str] = []
    if len(entries) > MAX_CLAIMS:
        out.append(f"{len(entries)} claims; the Snapshot holds at most {MAX_CLAIMS}")
    for i, e in enumerate(entries, start=1):
        if not isinstance(e, dict) or not str(e.get("claim") or "").strip():
            out.append(f"claim {i}: needs a 'claim' text")
            continue
        support, contra = _keys(e, "supports"), _keys(e, "contradicts")
        if not support:
            out.append(f"claim {i} ({e['claim'][:40]!r}): no supporting key")
        other = _not_comparable(e)
        for item in other:
            if not item["why"]:
                out.append(f"claim {i}: [@{item['key']}] not_comparable needs a 'why'")
        for k in [*support, *contra, *(item["key"] for item in other)]:
            if k not in sources:
                out.append(f"claim {i}: [@{k}] not in sources.json")
    return out


def cross_kind_contradictions(
    entry: Mapping[str, Any], strengths: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """The claim's contradicting keys of a kind of evidence none of its support belongs to.
    Those are rarely tests of the same question: a short trial against lifelong genetics, a
    mouse against people, cells against an organism."""
    kinds = {key_kind(k, strengths) for k in _keys(entry, "supports")} - {""}
    return [
        k
        for k in _keys(entry, "contradicts")
        if (kind := key_kind(k, strengths)) and kinds and kind not in kinds
    ]


def claim_cells(
    entry: Mapping[str, Any], strengths: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Per kind of evidence, the claim's supporting and contradicting keys with the counts
    the cell shows: papers, independent groups (distinct last authors), the largest stated
    n, the species, and the database records."""
    cells: dict[str, dict[str, Any]] = {
        k: {"support": [], "contra": [], "other": [], "papers": 0, "groups": set(), "n": None,
            "species": set(), "records": []}
        for k in KINDS
    }  # fmt: skip
    for item in _not_comparable(entry):
        kind = key_kind(item["key"], strengths)
        if kind:
            cells[kind]["other"].append(item)
    for side, keys in (
        ("support", _keys(entry, "supports")),
        ("contra", _keys(entry, "contradicts")),
    ):
        for key in keys:
            kind = key_kind(key, strengths)
            if not kind:
                continue
            cell = cells[kind]
            cell[side].append(key)
            if side != "support":
                continue
            if key.startswith("pmid:"):
                s = strengths.get(key.removeprefix("pmid:")) or {}
                cell["papers"] += 1
                if s.get("last_author"):
                    cell["groups"].add(s["last_author"].lower())
                if s.get("n") and (cell["n"] is None or s["n"] > cell["n"]):
                    cell["n"] = s["n"]
                cell["species"].update(s.get("species") or [])
            else:
                cell["records"].append(key)
    return cells


def is_weak(cells: Mapping[str, Mapping[str, Any]]) -> bool:
    """A claim resting on one group's papers, or on cells and biochemistry alone, with no
    record behind it: the prose may not call it established, shown or proven."""
    supported = [k for k, c in cells.items() if c["support"]]
    records = any(c["records"] for c in cells.values())
    groups = set().union(*(c["groups"] for c in cells.values()))
    papers = sum(c["papers"] for c in cells.values())
    if not supported:
        return True
    if supported == ["cells"]:
        return True
    return not records and papers > 0 and len(groups) <= 1


def _cite(keys: Sequence[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


def _cell_text(kind: str, cell: Mapping[str, Any], available: set[str]) -> str:
    parts = []
    if cell["support"]:
        bits = []
        if cell["papers"]:
            groups = len(cell["groups"])
            bits.append(f"{cell['papers']} paper{'s' * (cell['papers'] != 1)}")
            bits.append(f"{groups} group{'s' * (groups != 1)}" if groups else "groups not recorded")
            if kind == "animal" and cell["species"]:
                bits.append(", ".join(sorted(cell["species"] - {"human"})) or "species not tagged")
            if kind in ("human", "pharmacology"):
                bits.append(f"largest n {cell['n']:,}" if cell["n"] else "n not stated")
        if cell["records"]:
            bits.append("records")
        parts.append("✓ " + " · ".join(bits) + " " + _cite(cell["support"], available))
    if cell["contra"]:
        n = len(cell["contra"])
        parts.append(f"✗ {n} source{'s' * (n != 1)} against {_cite(cell['contra'], available)}")
    for item in cell["other"]:
        why = item["why"].replace("|", "/")
        parts.append(f"≠ not directly comparable: {why} {_cite([item['key']], available)}")
    return "<br>".join(p.strip() for p in parts) or "none cited"


def how_we_know_md(
    entries: Sequence[Mapping[str, Any]],
    strengths: Mapping[str, Mapping[str, Any]],
    available: set[str],
) -> tuple[str, list[str]]:
    """The *How we know* table and the keys its claims rest on (for the caption)."""
    rows = [e for e in entries if isinstance(e, dict) and str(e.get("claim") or "").strip()]
    if not rows:
        return "", []
    out = [
        "| Claim | " + " | ".join(KIND_TITLES[k] for k in KINDS) + " |",
        "|---|" + "---|" * len(KINDS),
    ]
    keys: list[str] = []
    weak = 0
    for e in rows[:MAX_CLAIMS]:
        cells = claim_cells(e, strengths)
        flag = " *(one group or cells only)*" if is_weak(cells) else ""
        weak += bool(flag)
        out.append(
            f"| {str(e['claim']).replace('|', '/')}{flag} | "
            + " | ".join(_cell_text(k, cells[k], available) for k in KINDS)
            + " |"
        )
        keys += [
            *_keys(e, "supports"),
            *_keys(e, "contradicts"),
            *(item["key"] for item in _not_comparable(e)),
        ]
    papers = [k for k in dict.fromkeys(keys) if k.startswith("pmid:")]
    classified = sum(1 for k in papers if key_kind(k, strengths))
    out += [
        "",
        f"Counts from each cited paper's records: design from PubMed publication types and "
        f"MeSH, species from PubTator or MeSH, groups as distinct last authors, n as stated in "
        f"the abstract. {classified} of {len(papers)} cited papers classified by kind; reviews "
        "and unclassified papers are not counted.",
    ]
    return "\n".join(out), list(dict.fromkeys(keys))


# ── mechanism ───────────────────────────────────────────────────────────────────────

MECHANISM_FILE = "mechanism.json"
MIN_STEPS, MAX_STEPS = 4, 8
MAX_STEPS_ROUTED = 10  # parallel routes need more boxes than one chain
_STEP_FIELDS = ("actor", "action", "target", "compartment")


def step_routes(steps: Sequence[Any]) -> list[str]:
    """The named routes of a mechanism, in order of appearance."""
    named = (str(s.get("route") or "").strip() for s in steps if isinstance(s, dict))
    return list(dict.fromkeys(r for r in named if r))


def step_edges(steps: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """The arrows between steps. Steps without a route form the shared chain; a route starts
    from the shared step before it and runs on its own; the next shared step receives every
    open route. Without routes this is one chain."""
    edges: list[tuple[int, int]] = []
    trunk: int | None = None
    open_routes: dict[str, int] = {}
    for i, step in enumerate(steps):
        route = str(step.get("route") or "").strip()
        if route:
            start = open_routes.get(route, trunk)
            if start is not None:
                edges.append((start, i))
            open_routes[route] = i
            continue
        edges += [(end, i) for end in open_routes.values()]
        if not open_routes and trunk is not None:
            edges.append((trunk, i))
        open_routes, trunk = {}, i
    return edges


def mechanism_problems(steps: Any, sources: Mapping[str, Any], vocabulary: set[str]) -> list[str]:
    """What is wrong with ``mechanism.json``: not a list of 4–8 steps (10 with routes), a
    lone route, a step missing a field or a key, a key not in sources.json, a compartment
    outside UniProt's subcellular-location vocabulary (when the run holds it)."""
    if not isinstance(steps, list):
        return [f"{MECHANISM_FILE} must be a JSON list of steps"]
    out = []
    routes = step_routes(steps)
    most = MAX_STEPS_ROUTED if len(routes) > 1 else MAX_STEPS
    if not MIN_STEPS <= len(steps) <= most:
        out.append(f"{len(steps)} steps; write {MIN_STEPS}–{most}")
    if len(routes) == 1:
        out.append(f"only one route ({routes[0]!r}): name a second, or drop 'route'")
    lower = {v.lower() for v in vocabulary}
    for i, st in enumerate(steps, start=1):
        if not isinstance(st, dict):
            out.append(f"step {i}: not an object")
            continue
        missing = [f for f in _STEP_FIELDS if not str(st.get(f) or "").strip()]
        if missing:
            out.append(f"step {i}: missing {', '.join(missing)}")
        keys = _keys(st, "keys")
        if not keys:
            out.append(f"step {i}: not cited (no keys)")
        out += [f"step {i}: [@{k}] not in sources.json" for k in keys if k not in sources]
        comp = str(st.get("compartment") or "").strip()
        if comp and lower and comp.lower() not in lower:
            out.append(f"step {i}: compartment {comp!r} is not a UniProt subcellular location")
    return out


def step_strength(step: Mapping[str, Any], strengths: Mapping[str, Mapping[str, Any]]) -> str:
    """How a step is drawn: "solid" (human or in vivo evidence from ≥ 2 groups, or a
    record), "dashed" (one group, or cells only), "dotted" (proposed: no primary paper or
    record behind it)."""
    cells = claim_cells({"supports": step.get("keys") or []}, strengths)
    papers = sum(c["papers"] for c in cells.values())
    records = any(c["records"] for c in cells.values())
    in_vivo = cells["human"]["groups"] | cells["animal"]["groups"]
    if records or len(in_vivo) >= 2:
        return "solid"
    return "dashed" if papers else "dotted"


# ── open questions ──────────────────────────────────────────────────────────────────

OPEN_FILE = "open_questions.json"


def open_problems(entries: Any, sources: Mapping[str, Any]) -> list[str]:
    """What is wrong with ``open_questions.json``: not a list, a question without the
    evidence that leaves it open, a data or status key not in sources.json."""
    if not isinstance(entries, list):
        return [f"{OPEN_FILE} must be a JSON list of questions"]
    out = []
    for i, e in enumerate(entries, start=1):
        if not isinstance(e, dict) or not str(e.get("question") or "").strip():
            out.append(f"question {i}: needs a 'question' text")
            continue
        if not _keys(e, "evidence"):
            out.append(f"question {i}: no evidence key (what leaves it open)")
        for side in ("evidence", "data", "active"):
            out += [
                f"question {i}: [@{k}] not in sources.json"
                for k in _keys(e, side)
                if k not in sources
            ]
    return out


def open_questions_md(
    entries: Sequence[Mapping[str, Any]], available: set[str]
) -> tuple[str, list[str]]:
    """Each open question as a project: the evidence that leaves it open, what would settle
    it (the LLM's suggestion, marked), the public data that could test it, and the trials or
    recent papers working on it."""
    rows = [e for e in entries if isinstance(e, dict) and str(e.get("question") or "").strip()]
    if not rows:
        return "", []
    out = [
        "| Open question | Evidence that leaves it open | What would settle it (LLM) "
        "| Public data to test it | Active work |",
        "|---|---|---|---|---|",
    ]
    keys: list[str] = []
    for e in rows:
        ev, data, active = _keys(e, "evidence"), _keys(e, "data"), _keys(e, "active")
        keys += [*ev, *data, *active]
        out.append(
            f"| {str(e['question']).replace('|', '/')} | {_cite(ev, available)} "
            f"| *{str(e.get('would_settle') or '—').replace('|', '/')}* "
            f"| {_cite(data, available) or '—'} | {_cite(active, available) or 'none found'} |"
        )
    return "\n".join(out), list(dict.fromkeys(keys))
