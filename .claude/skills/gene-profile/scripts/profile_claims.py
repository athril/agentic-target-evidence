# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Two checks on what the writer states, each recorded in a file the checker validates.

* ``claim_checks.json``: for each core claim, a targeted literature search and what it found.
  Gene-wide retrieval rarely reaches the paper that qualifies one claim, and a curated
  record's sentence can stand for years after the literature moved on. The writer names the
  claim's terms, ``claim_search.py`` runs the search, and the writer records whether the claim
  holds, needs qualifying or conflicts with what was read.

* ``claim_audit.json``: for each block citing a paper, a verdict from a reader who saw only
  the block and the cited abstracts, with a quote the checker finds in those abstracts.
  ``claim_audit.py`` lists the blocks; a verdict goes stale when its block changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from profile_schema import load_manifest, load_raw

CHECK_FILE = "claim_checks.json"
SEARCH_FILE = "claim_search.json"
SEARCH_REPORT = "claim_search.md"
AUDIT_FILE = "claim_audit.json"
AUDIT_INPUT = "audit_input.json"
OUTCOMES = ("holds", "qualified", "conflicts", "not_found")
VERDICTS = ("supported", "overstated", "unsupported")
_ALTERNATIVES_RE = re.compile(r"\s+OR\s+")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _pmids(entry: Mapping[str, Any]) -> list[str]:
    return [str(k).removeprefix("@") for k in entry.get("read") or [] if str(k).strip()]


# ── claim checks ────────────────────────────────────────────────────────────────────


def search_query(symbols: Sequence[str], terms: Sequence[str]) -> str:
    """The PubMed query for a claim: the gene and every term group, each group's
    alternatives separated by OR ("cleaved OR cleavage")."""
    gene = " OR ".join(f'"{s}"[tiab]' for s in dict.fromkeys(symbols) if s)
    groups = [
        "(" + " OR ".join(f'"{a.strip()}"[tiab]' for a in _ALTERNATIVES_RE.split(t)) + ")"
        for t in terms
        if str(t).strip()
    ]
    return " AND ".join([f"({gene})", *groups])


def gene_terms(run_dir: Path) -> list[str]:
    """The gene's literature terms: the symbol and those the run measured as naming it."""
    scope = load_raw(run_dir, "literature_scope")
    terms = (scope.data or {}).get("terms") if scope and isinstance(scope.data, dict) else None
    return list(dict.fromkeys([load_manifest(run_dir).gene, *(terms or [])]))


def load_searches(run_dir: Path) -> dict[str, dict[str, Any]]:
    """The searches run so far, by query."""
    path = run_dir / SEARCH_FILE
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text())
    except ValueError:
        return {}
    return {e["query"]: e for e in entries if isinstance(e, dict) and e.get("query")}


def check_problems(
    entries: Any,
    claims: Sequence[str],
    searches: Mapping[str, Mapping[str, Any]],
    symbols: Sequence[str],
    cited: set[str],
) -> list[str]:
    """What is wrong with ``claim_checks.json``: a core claim without a check, terms that were
    not searched, an outcome outside the vocabulary, nothing read from what the search
    returned, a claim that moved without the paper that moved it being cited."""
    if not isinstance(entries, list):
        return [f"{CHECK_FILE} must be a JSON list of checks"]
    out: list[str] = []
    checked = {_norm(e.get("claim") or "") for e in entries if isinstance(e, dict)}
    out += [
        f"core claim has no check: {c[:60]!r}"
        for c in claims
        if str(c).strip() and _norm(c) not in checked
    ]
    for i, e in enumerate(entries, start=1):
        if not isinstance(e, dict) or not str(e.get("claim") or "").strip():
            out.append(f"check {i}: needs a 'claim' text")
            continue
        label = f"check {i} ({e['claim'][:40]!r})"
        terms = [t for t in e.get("terms") or [] if str(t).strip()]
        if not terms:
            out.append(f"{label}: no search terms")
            continue
        search = searches.get(search_query(symbols, terms))
        if search is None:
            out.append(f"{label}: run claim_search.py for these terms")
            continue
        outcome = e.get("outcome")
        if outcome not in OUTCOMES:
            out.append(f"{label}: outcome must be one of {', '.join(OUTCOMES)}")
            continue
        found = {f"pmid:{p}" for p in search.get("pmids") or []}
        read = _pmids(e)
        out += [f"{label}: [@{k}] was not in this search's results" for k in read if k not in found]
        if outcome == "not_found":
            if found:
                out.append(f"{label}: the search returned papers; read them")
            continue
        if found and not read:
            out.append(f"{label}: no paper read from this search")
        if outcome in ("qualified", "conflicts"):
            if not str(e.get("note") or "").strip():
                out.append(f"{label}: say how the claim changed in 'note'")
            if not set(read) & cited:
                out.append(f"{label}: cite in the draft the paper that changed the claim")
    return out


# ── the audit ───────────────────────────────────────────────────────────────────────


def block_id(text: str) -> str:
    """A block's identity: a verdict applies to these words only."""
    return hashlib.sha256(_norm(text).encode()).hexdigest()[:12]


def _quoted(quote: str, sources: Sequence[str]) -> bool:
    wanted = _norm(quote)
    return bool(wanted) and any(wanted in _norm(s) for s in sources)


def audit_problems(entries: Any, items: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(block text, problem) for each audited block that fails: no current verdict, a verdict
    other than supported, or a quote that is not in the cited abstracts."""
    if not isinstance(entries, list):
        return [("", f"{AUDIT_FILE} must be a JSON list of verdicts")]
    verdicts = {str(e.get("id")): e for e in entries if isinstance(e, dict)}
    out: list[tuple[str, str]] = []
    for item in items:
        text, entry = item["text"], verdicts.get(item["id"])
        if entry is None:
            out.append((text, "not audited, or changed since: run claim_audit.py"))
            continue
        verdict = entry.get("verdict")
        if verdict not in VERDICTS:
            out.append((text, f"verdict must be one of {', '.join(VERDICTS)}"))
        elif verdict != "supported":
            out.append((text, f"{verdict}: {str(entry.get('note') or 'no note')[:160]}"))
        elif not _quoted(str(entry.get("quote") or ""), [s["text"] for s in item["sources"]]):
            out.append((text, "the quote is not in the cited abstracts"))
    return out
