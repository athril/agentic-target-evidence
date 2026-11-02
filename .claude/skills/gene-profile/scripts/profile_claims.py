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

* ``claim_ledger.json``: every factual block as a claim with an id, its sources with their
  design and organism, what qualifies it, and whether a reader validated it and when. Built
  here from the other files, so what it says of a claim cannot drift from them.

Retrieving a source, a sentence matching it, and a conclusion following from the evidence are
separate questions. The retrieval matrix answers the first, the audit the second, and the
scientific review (``scientific_review.json``) the third.
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
MIN_QUOTE_WORDS = 5  # shorter than this, a match shows shared words, not a shared statement
LEDGER_FILE = "claim_ledger.json"
NOT_AUDITED, RECORD_ONLY = "not audited", "cites records or trials only"
REVIEW_FILE = "scientific_review.json"
REVIEW_INPUT = "review_input.md"
REVIEW_CHECKS = (
    "mechanism_vs_genetics", "genetics_vs_clinical", "constraint_vs_disease",
    "safety_reasoning", "status_is_current", "sections_agree", "strength_of_claims",
)  # fmt: skip
SEVERITIES = ("critical", "high", "medium", "low")
RESOLUTIONS = ("fixed", "limitation", "open")
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
    return len(wanted.split()) >= MIN_QUOTE_WORDS and any(wanted in _norm(s) for s in sources)


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
            out.append((text, f"the quote is not {MIN_QUOTE_WORDS}+ words of the cited abstracts"))
    return out


# ── the ledger ──────────────────────────────────────────────────────────────────────


def stamp_verdicts(entries: list[Any], today: str) -> bool:
    """Date the verdicts that have none; True when any changed. The date comes from here, not
    the reader, so "last verified" is when the verdict was recorded."""
    changed = False
    for e in entries:
        if isinstance(e, dict) and not e.get("checked_on"):
            e["checked_on"] = today
            changed = True
    return changed


def _qualifiers(keys: set[str], checks: Any) -> list[str]:
    """What the claim searches found against a claim citing these papers."""
    out = []
    for c in checks if isinstance(checks, list) else []:
        if (
            isinstance(c, dict)
            and c.get("outcome") in ("qualified", "conflicts")
            and keys & set(_pmids(c))
        ):
            out.append(str(c.get("note") or "").strip())
    return list(dict.fromkeys(x for x in out if x))


def _confidence(sources: Sequence[Mapping[str, Any]]) -> str:
    """How much stands behind a claim, from its papers' records: never the writer's word."""
    papers = [s for s in sources if s["key"].startswith("pmid:")]
    groups = {s["last_author"].lower() for s in papers if s.get("last_author")}
    kinds = {s["evidence_kind"] for s in papers if s.get("evidence_kind")}
    if len(groups) >= 2 and len(kinds) >= 2:
        return "high: independent groups, several kinds of evidence"
    if len(groups) >= 2:
        return "moderate: independent groups"
    if papers:
        return "limited: one group's papers"
    return "records"


def ledger(
    blocks: Sequence[Mapping[str, Any]],
    verdicts: Any,
    checks: Any,
    strengths: Mapping[str, Mapping[str, Any]],
    kind_of: Any,
) -> list[dict[str, Any]]:
    """One entry per factual block. ``blocks`` hold id, section, text and keys;
    ``kind_of`` names a key's kind of evidence."""
    by_id = {str(e.get("id")): e for e in verdicts if isinstance(e, dict)} if isinstance(verdicts, list) else {}  # fmt: skip
    out = []
    for b in blocks:
        keys = list(b["keys"])
        sources = []
        for k in keys:
            s = strengths.get(k.removeprefix("pmid:")) or {} if k.startswith("pmid:") else {}
            sources.append(
                {
                    "key": k,
                    "evidence_kind": kind_of(k),
                    "design": s.get("design", ""),
                    "organism": list(s.get("species") or []),
                    "n": s.get("n"),
                    "last_author": s.get("last_author", ""),
                }
            )
        verdict = by_id.get(b["id"])
        if verdict:
            status = str(verdict.get("verdict"))
        else:
            status = NOT_AUDITED if b.get("auditable") else RECORD_ONLY
        out.append(
            {
                "id": f"C-{b['id']}",
                "section": b["section"],
                "claim": b["text"],
                "sources": sources,
                "confidence": _confidence(sources),
                "qualified_by": _qualifiers({k for k in keys if k.startswith("pmid:")}, checks),
                "validation": status,
                "quote": str((verdict or {}).get("quote") or ""),
                "last_verified": str((verdict or {}).get("checked_on") or ""),
            }
        )
    return out


def ledger_summary(entries: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["validation"]] = counts.get(e["validation"], 0) + 1
    return counts


# ── the scientific review ───────────────────────────────────────────────────────────


def review_problems(entry: Any) -> list[str]:
    """What keeps ``scientific_review.json`` from counting: a question left unanswered, a
    finding outside the vocabulary, or an open critical or high finding."""
    if not isinstance(entry, dict):
        return [f"{REVIEW_FILE} must be an object with 'reader', 'checks' and 'findings'"]
    out = []
    if not str(entry.get("reader") or "").strip():
        out.append("'reader': say who reviewed the report (not the writer)")
    checks = entry.get("checks") if isinstance(entry.get("checks"), dict) else {}
    out += [
        f"check {c!r}: answer clear or findings"
        for c in REVIEW_CHECKS
        if checks.get(c) not in ("clear", "findings")
    ]
    findings = entry.get("findings") if isinstance(entry.get("findings"), list) else []
    for i, f in enumerate(findings, start=1):
        if not isinstance(f, dict) or not str(f.get("finding") or "").strip():
            out.append(f"finding {i}: needs a 'finding' text")
            continue
        if f.get("check") not in REVIEW_CHECKS:
            out.append(f"finding {i}: check must be one of {', '.join(REVIEW_CHECKS)}")
        if f.get("severity") not in SEVERITIES:
            out.append(f"finding {i}: severity must be one of {', '.join(SEVERITIES)}")
        if f.get("resolution") not in RESOLUTIONS:
            out.append(f"finding {i}: resolution must be one of {', '.join(RESOLUTIONS)}")
        elif f["resolution"] == "open" and f.get("severity") in ("critical", "high"):
            out.append(f"open {f['severity']} finding: {str(f['finding'])[:120]}")
    return out
