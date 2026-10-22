# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Which literature a lens most needs to see, and which claims say the same thing.

On well-studied pairs a lens is shown a few percent of its routed claims, and many of the
slots went to papers repeating one finding. Two rules, both from facts the run already
computed:

* Relevance. Within literature, papers are ranked by scope first (in the disease, then a
  related one), then by a lens-specific signal, then by study design and quality
  (``literature_selection.rank_literature``). The signal says the paper bears on the
  lens's own question: for genetics a variant statement read into the direction reading; for
  clinical a link to a programme in the indication; for safety human exposure (a human
  study); for biology a perturbation in a model of the disease; commercial and regulatory
  have none.
* Restatements. Literature claims from different papers that state the same finding are
  shown once, with "+N papers agree" and every member's id, all still citable. Genetics
  claims of a paper whose statement the direction reading counted collapse by (change of
  the target, outcome, scope) when the claim carries that typed effect; other claims when their normalised text (lower case, digits and the gene
  symbols masked, stop words removed) shares at least ``JACCARD_MIN`` of its word 3-grams.

Pure module: the caller passes the lens's task spec and the run's literature index.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from schemas.evidence import CoreClaim

JACCARD_MIN = 0.8
_WORD = re.compile(r"[a-z0-9]+")
_DIGITS = re.compile(r"\d+(?:\.\d+)?")
# Function words carry no finding; removing them keeps the 3-grams on content.
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "and",
        "or",
        "but",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "which",
        "who",
        "whom",
        "than",
        "then",
        "there",
        "here",
        "also",
        "not",
        "no",
        "into",
        "onto",
        "over",
        "under",
        "between",
        "among",
        "within",
        "without",
        "via",
        "per",
        "vs",
        "versus",
    ]
)


class Relevance(BaseModel):
    """A lens's selection signals (see module docstring)."""

    signal_papers: set[str] = set()  # literature paper ids carrying the lens's signal
    # Typed restatement key per claim id ("decrease|benefit|in_scope"): claims sharing a
    # key state the same variant finding.
    restatement_keys: dict[str, str] = {}
    gene_terms: list[str] = []  # symbols masked before comparing claim text
    pinned: set[str] = set()  # decisive claim ids, always shown when routed (lens_selection)


def _observations(reading: Any) -> list[Mapping[str, Any]]:
    if not isinstance(reading, Mapping):
        return []
    return [o for o in reading.get("observations") or [] if isinstance(o, Mapping)]


def relevance_from_spec(
    lens: str, spec: Mapping[str, Any], claims: Sequence[CoreClaim], papers: Any
) -> Relevance:
    """The lens's signals from its task spec and the run's literature index (``papers``,
    a ``LiteratureIndex`` or None)."""
    paper_of = {str(c.evidence_id): str(c.source_evidence_id or c.evidence_id) for c in claims}
    signal: set[str] = set()
    keys: dict[str, str] = {}
    if lens == "genetics":
        # A literature observation's source is its statement's paper ("PMID:…"), or a
        # claim id on readings of runs before the statement route.
        by_pmid = (
            {str(rec.pmid): pid for pid, rec in papers.papers.items()} if papers is not None else {}
        )
        for o in _observations(spec.get("direction_reading")):
            if o.get("evidence") != "genetics_literature":
                continue
            src = str(o.get("source") or "")
            paper = paper_of.get(src) or by_pmid.get(src.removeprefix("PMID:"), "")
            if not paper:
                continue
            signal.add(paper)
            scope = "in_scope" if o.get("in_scope", True) else "other"
            key = f"{o.get('perturbation')}|{o.get('outcome')}|{scope}"
            if src in paper_of:
                keys[src] = key
                continue
            for c in claims:
                if (
                    paper_of[str(c.evidence_id)] == paper
                    and c.perturbation == o.get("perturbation")
                    and c.outcome == o.get("outcome")
                ):
                    keys[str(c.evidence_id)] = key
    elif lens == "clinical":
        reading = spec.get("programme_reading")
        programmes = reading.get("programmes") if isinstance(reading, Mapping) else None
        for p in programmes or []:
            for pub in p.get("publications") or []:
                cid = str(pub.get("claim_id") or "")
                if cid in paper_of:
                    signal.add(paper_of[cid])
            for cid in p.get("engagement_claim_ids") or []:
                if str(cid) in paper_of:
                    signal.add(paper_of[str(cid)])
    elif lens == "biology":
        signal |= {
            str(o.get("source") or "") for o in _observations(spec.get("perturbation_reading"))
        }
    elif lens == "safety" and papers is not None:
        signal |= {pid for pid, rec in papers.papers.items() if rec.human is True}
    gene = str(spec.get("target_gene") or "")
    aliases = list((spec.get("resolved_context") or {}).get("gene_aliases") or [])
    return Relevance(
        signal_papers={s for s in signal if s},
        restatement_keys=keys,
        gene_terms=[t for t in [gene, *aliases] if t],
        pinned={str(i) for i in spec.get("decisive_claim_ids") or []} & set(paper_of),
    )


def _shingles(text: str, gene_terms: Iterable[str]) -> frozenset[tuple[str, ...]]:
    folded = text.lower()
    for term in sorted({t.lower() for t in gene_terms if t}, key=len, reverse=True):
        folded = re.sub(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", " gene ", folded)
    folded = _DIGITS.sub(" 0 ", folded)
    words = [w for w in _WORD.findall(folded) if w not in _STOP_WORDS]
    if len(words) < 3:
        return frozenset({tuple(words)}) if words else frozenset()
    return frozenset(tuple(words[i : i + 3]) for i in range(len(words) - 2))


def _jaccard(a: frozenset[Any], b: frozenset[Any]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def collapse_restatements(
    ranked: Sequence[CoreClaim], relevance: Relevance
) -> list[list[CoreClaim]]:
    """Groups of claims stating the same finding, in the order of their best (first)
    member, which represents the group. Members always come from different papers, so a
    group of n counts n independent sources; a claim never joins a group holding a claim
    of its own paper."""
    groups: list[list[CoreClaim]] = []
    papers: list[set[str]] = []
    keyed: dict[str, int] = {}
    shingles: list[frozenset[tuple[str, ...]]] = []
    for claim in ranked:
        cid = str(claim.evidence_id)
        paper = str(claim.source_evidence_id or claim.evidence_id)
        key = relevance.restatement_keys.get(cid)
        own = _shingles(claim.claim_text or "", relevance.gene_terms)
        target: int | None = None
        if key is not None:
            g = keyed.get(key)
            if g is not None and paper not in papers[g]:
                target = g
        else:
            target = next(
                (
                    g
                    for g, sh in enumerate(shingles)
                    if paper not in papers[g]
                    and str(groups[g][0].evidence_id) not in relevance.restatement_keys
                    and _jaccard(own, sh) >= JACCARD_MIN
                ),
                None,
            )
        if target is None:
            groups.append([claim])
            papers.append({paper})
            shingles.append(own)
            if key is not None:
                keyed.setdefault(key, len(groups) - 1)
        else:
            groups[target].append(claim)
            papers[target].add(paper)
    return groups


# ── decisive evidence ──────────────────────────────────────────────────────────

# Registry statuses of a trial that ended early.
_STOPPED = frozenset({"TERMINATED", "SUSPENDED", "WITHDRAWN"})
_LATE_PHASE = re.compile(r"PHASE\s*[34]|PHASE[34]")


def is_decisive_trial(evidence: Any, drug_names: Iterable[str], names_disease: Any) -> bool:
    """A phase 3 or later trial of one of the target's drugs, in the indication
    (``names_disease(conditions) -> bool``), that was terminated, suspended or withdrawn."""
    x = getattr(evidence, "extra", None) or {}
    if str(x.get("status") or "").upper() not in _STOPPED:
        return False
    if not _LATE_PHASE.search(str(x.get("phase") or "").upper().replace("_", "")):
        return False
    interventions = " ".join(str(i) for i in x.get("interventions") or []).lower()
    by_drug = str(x.get("matched_by") or "").startswith("drug:") or any(
        re.search(rf"(?<![a-z0-9]){re.escape(d.lower())}(?![a-z0-9])", interventions)
        for d in drug_names
        if d
    )
    conditions = "; ".join(str(c) for c in x.get("conditions") or [])
    return by_drug and bool(names_disease(conditions))


def decisive_claim_ids(claims: Iterable[CoreClaim], decisive_sources: Iterable[str]) -> set[str]:
    """Claims a lens must see when they are routed to it: approval of a drug in this
    indication and boxed warnings (from the label claims' own wording), and every claim of
    a decisive source record (``decisive_sources``: stopped late-phase trials of the
    target's drugs in the indication, curated Mendelian records for the disease)."""
    from services.evidence.claim_extraction import LABEL_APPROVED_HERE, LABEL_BOXED_WARNING

    sources = set(decisive_sources)
    return {
        str(c.evidence_id)
        for c in claims
        if LABEL_APPROVED_HERE in (c.claim_text or "")
        or LABEL_BOXED_WARNING in (c.claim_text or "")
        or str(c.source_evidence_id or "") in sources
    }
