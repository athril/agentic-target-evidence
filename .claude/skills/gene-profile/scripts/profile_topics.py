# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What a run's abstracts say about the gene, sentence by sentence, by topic.

A run caches hundreds of abstracts and the writer reads a few, so a finding can be retrieved
and still go unseen. This pulls the sentences that name the gene on the questions a profile
has to answer, most-cited paper first. Each is a lead to the abstract, not a finding: the
lexicons match words, not meaning.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_PER_TOPIC = 8
_SENTENCE_CHARS = 280
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

TOPICS: dict[str, re.Pattern[str]] = {
    "Which activities it has and on what substrates": re.compile(
        r"\b(substrates?|dephosphorylat\w+|lipid phosphatase|protein phosphatase|kinase activity"
        r"|catalytic\w*|enzymatic\w*|catalytically (?:dead|inactive)|non-?catalytic"
        r"|independent of its \w+ activity|\w+-independent)\b",
        re.IGNORECASE,
    ),
    "What it does in each compartment": re.compile(
        r"\b(nuclear|nucleus|chromatin|genomic stability|DNA repair|mitochondri\w+"
        r"|membrane[- ]bound|cytoplasmic|cytosolic|subcellular|mislocali[sz]\w+)\b",
        re.IGNORECASE,
    ),
    "How disease variants act beyond simple loss": re.compile(
        r"\b(dominant[- ]negative|gain[- ]of[- ]function|neomorphic|hypomorph\w*"
        r"|selective(?:ly)? (?:loss|impair\w+)|separation[- ]of[- ]function)\b",
        re.IGNORECASE,
    ),
    "How its transcription is controlled": re.compile(
        r"(?<!post-)\b(promoter|transcription factors?|transcriptional\w*|trans-?activat\w+|enhancers?"
        r"|response elements?|binding sites? for|cis-regulatory)\b",
        re.IGNORECASE,
    ),
    "How the protein is processed or modified": re.compile(
        r"\b(cleav\w+|proteoly\w+|phosphorylat\w+|glycosylat\w+|ubiquitinat\w+|sumoylat\w+"
        r"|acetylat\w+|methylat\w+|truncated|mature form|post-translational\w*)\b",
        re.IGNORECASE,
    ),
    "By which routes it acts": re.compile(
        r"\b(routes?|pathways? (?:of|to|for)|intracellular\w*|extracellular\w*|traffick\w+"
        r"|endocyt\w+|recycl\w+|secretory pathway|lysosom\w+|proteasom\w+|nuclear import)\b",
        re.IGNORECASE,
    ),
    "Where it is made and where it acts": re.compile(
        r"\b(source of|derived from|originat\w+|circulating|tissue-specific|cell-type-specific"
        r"|\w+-specific (?:knockout|deletion|inactivation)|conditional (?:knockout|deletion)"
        r"|locally|paracrine|autocrine|cell-autonomous)\b",
        re.IGNORECASE,
    ),
    "Where findings disagree or stay open": re.compile(
        r"\b(contrary to|conflicting|controvers\w+|discrepan\w+|debated"
        r"|remains? (?:unclear|unknown|unresolved)|not (?:fully )?resolved|not required"
        r"|independent(?:ly)? of|surprisingly|unexpectedly)\b",
        re.IGNORECASE,
    ),
}


def _names_gene(sentence: str, symbols: Iterable[str]) -> bool:
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])", sentence, re.IGNORECASE)
        for s in symbols
    )


def _citations(paper: Mapping[str, Any]) -> int:
    count = paper.get("citation_count")
    return count if isinstance(count, int) else 0


def topic_sentences(
    papers: Mapping[str, Mapping[str, Any]], symbols: Iterable[str]
) -> dict[str, list[tuple[str, str]]]:
    """Per topic, up to a handful of (pmid, sentence) naming the gene, one per paper, the
    most-cited papers first."""
    symbols = [s for s in symbols if s]
    ranked = sorted(papers.items(), key=lambda kv: -_citations(kv[1]))
    out: dict[str, list[tuple[str, str]]] = {topic: [] for topic in TOPICS}
    for pmid, paper in ranked:
        sentences = _SENTENCE_RE.split(str(paper.get("abstract") or ""))
        for topic, lexicon in TOPICS.items():
            if len(out[topic]) >= _PER_TOPIC:
                continue
            hit = next(
                (s for s in sentences if lexicon.search(s) and _names_gene(s, symbols)), None
            )
            if hit:
                out[topic].append((pmid, hit.strip()))
    return out


def topics_digest(papers: Mapping[str, Mapping[str, Any]], symbols: Iterable[str]) -> list[str]:
    """The digest section: topic headings with their sentences; nothing when none match."""
    found = topic_sentences(papers, symbols)
    if not any(found.values()):
        return []
    lines = [
        "## What the abstracts say, by topic",
        "",
        "Sentences naming the gene, most-cited paper first, matched on words. They point to "
        "papers to read; check each against its abstract before using it.",
        "",
    ]
    for topic, hits in found.items():
        if hits:
            lines += [f"### {topic}", ""]
            lines += [f"- pmid:{p} — {s[:_SENTENCE_CHARS]}" for p, s in hits]
            lines.append("")
    return lines
