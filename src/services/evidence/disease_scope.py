# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Does a curated record, or a piece of text, concern the run's indication?

Curation sources (ClinGen, GenCC, Orphanet) and ClinVar list *every* disease a gene is
linked to. Treating the strongest of them as evidence for the run's indication made a
gene's Mendelian disease count for any disease it was run against — PCSK9's
"hypercholesterolemia, autosomal dominant, 3" (Definitive) was reported as Mendelian
validation for myocardial infarction, and the causality axis was clamped to favourable.

A record is in scope when its disease is the run's disease or one of its ontology
descendants (Open Targets EFO/MONDO hierarchy), or — when ids are missing — when one
disease name contains the other ("focal segmental glomerulosclerosis 2" is in scope for
"focal segmental glomerulosclerosis"). Pure module: the caller supplies the id set.

For text (claims, label warnings, paper titles, trial outcomes), `DiseaseScope` below
holds the disease's names by relation and the outcome names that count its events.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, PrivateAttr

from core.evidence_text import normalize_typography, uninvert_heading

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Names shorter than this never name-match (too generic: "cancer", "obesity" alone).
_MIN_NAME_CHARS = 8

# Strength order shared by ClinGen and GenCC classifications.
CLASSIFICATION_RANK: dict[str, int] = {
    "definitive": 6,
    "strong": 5,
    "moderate": 4,
    "supportive": 3,
    "limited": 2,
    "disputed": 1,
    "disputed evidence": 1,
    "refuted": 0,
    "refuted evidence": 0,
    "no known disease relationship": 0,
}


def normalize_curie(curie: str | None) -> str:
    """``MONDO:0011369`` / ``mondo_0011369`` / an OBO IRI → ``MONDO_0011369``."""
    if not curie:
        return ""
    tail = curie.rsplit("/", 1)[-1].strip()
    return tail.replace(":", "_").upper()


def _norm_name(name: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", (name or "").lower()).split())


def names_match(a: str, b: str) -> bool:
    """One disease name contains the other, word-aligned ("fsgs 2" ⊃ "fsgs")."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    if len(short) < _MIN_NAME_CHARS:
        return short == long_
    return f" {short} " in f" {long_} "


def in_scope(
    curie: str | None,
    label: str,
    *,
    scope_ids: frozenset[str] | set[str] | None,
    disease_name: str,
) -> bool:
    """Whether a curated record (``curie``/``label``) concerns the run's disease."""
    ids = {normalize_curie(i) for i in (scope_ids or ())}
    if curie and normalize_curie(curie) in ids:
        return True
    return names_match(label, disease_name)


def strongest_in_scope(
    associations: list[dict[str, object]],
    *,
    scope_ids: frozenset[str] | set[str] | None,
    disease_name: str,
) -> str | None:
    """Strongest classification among associations for the run's disease, else None.

    Accepts ClinGen (``disease_label``) and GenCC (``disease_title``) field names.
    """
    best: tuple[int, str] | None = None
    for a in associations:
        label = str(a.get("disease_label") or a.get("disease_title") or a.get("disease_name") or "")
        curie = a.get("disease_curie") or a.get("disease_id")
        if not in_scope(
            str(curie) if curie else None, label, scope_ids=scope_ids, disease_name=disease_name
        ):
            continue
        cls = str(a.get("classification") or "")
        rank = CLASSIFICATION_RANK.get(cls.lower(), -1)
        if best is None or rank > best[0]:
            best = (rank, cls)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Disease scope for text: how literature, labels and registries name the disease
# ---------------------------------------------------------------------------
#
# Literature names a disease in many forms the ontology's exact synonyms do not list:
# hyphenation ("non-alcoholic" / "non alcoholic" / "nonalcoholic"), British spelling
# ("leukaemia"), inverted headings ("Leukemia, Myeloid, Acute"), plurals, "disease" for
# "disorder", the head noun alone ("steatohepatitis"), abbreviations defined in the paper,
# and subtypes. Outcome trials name the disease's events instead ("major coronary
# events"). `DiseaseScope` holds the run disease's names by relation and decides, for a
# text, whether it concerns the disease (in scope), a broader or neighbouring concept
# (related) or neither. Pure: the caller builds it from the ontology once per run.

ScopeMatch = Literal["in_scope", "related", "none"]
ENDPOINT_VOCABULARY = Path("config/endpoint_vocabulary.yaml")

# British spellings of medical stems, folded to the American form before matching.
_SPELLING = (
    ("leukaem", "leukem"),
    ("anaem", "anem"),
    ("ischaem", "ischem"),
    ("haemorrh", "hemorrh"),
    ("haemat", "hemat"),
    ("haemo", "hemo"),
    ("glycaem", "glycem"),
    ("oedem", "edem"),
    ("oesophag", "esophag"),
    ("oestr", "estr"),
    ("paediatr", "pediatr"),
    ("foetal", "fetal"),
    ("tumour", "tumor"),
    ("diarrhoea", "diarrhea"),
)
_JOINABLE_PREFIXES = (
    "non",
    "pre",
    "post",
    "anti",
    "auto",
    "peri",
    "poly",
    "multi",
    "semi",
    "sub",
    "inter",
    "intra",
    "hyper",
    "hypo",
    "neuro",
    "cardio",
)
# Head nouns too generic to stand for the disease on their own.
_GENERIC_HEADS = frozenset(
    {
        "disease",
        "disorder",
        "syndrome",
        "cancer",
        "carcinoma",
        "tumor",
        "neoplasm",
        "infection",
        "deficiency",
        "failure",
        "injury",
        "condition",
        "lesion",
        "disorders",
        "diseases",
        "type",
        "form",
        "abnormality",
        "malformation",
    }
)
_MIN_HEAD_CHARS = 8
_ABBREV_DEF = re.compile(r"\(([A-Z][A-Za-z0-9-]{1,9})\)")
_POSSESSIVE = re.compile(r"(?<=[a-z])'s?\b")


def fold_text(text: str) -> str:
    """Lower case, ASCII typography, American spelling, no possessives, single spaces,
    hyphens as spaces."""
    lowered = normalize_typography(text or "").lower()
    for british, american in _SPELLING:
        lowered = lowered.replace(british, american)
    lowered = _POSSESSIVE.sub("", lowered)
    return " ".join(_NON_ALNUM.sub(" ", lowered).split())


def name_forms(name: str) -> set[str]:
    """Folded forms of one disease name: as written, uninverted, with hyphenated words
    joined ("nonalcoholic") and a joinable prefix split ("non alcoholic"), and with
    "disease" and "disorder" interchanged."""
    raw = {name, uninvert_heading(name)}
    forms: set[str] = set()
    for r in raw:
        text = normalize_typography(r)
        forms.add(fold_text(text))
        forms.add(fold_text(re.sub(r"(?<=\w)-(?=\w)", "", text)))
        for prefix in _JOINABLE_PREFIXES:
            split = re.sub(rf"\b{prefix}(?=[a-z]{{4,}})", f"{prefix} ", text, flags=re.IGNORECASE)
            forms.add(fold_text(split))
    for f in list(forms):
        if f.endswith(" disease"):
            forms.add(f[: -len("disease")] + "disorder")
        elif f.endswith(" disorder"):
            forms.add(f[: -len("disorder")] + "disease")
    return {f for f in forms if f}


def _word_regex(word: str) -> str:
    """One folded word as a regex over original text: British spellings as alternatives,
    an optional possessive."""
    body = re.escape(word)
    for british, american in _SPELLING:
        if american in word:
            body = body.replace(
                re.escape(american), f"(?:{re.escape(american)}|{re.escape(british)})"
            )
    return body + r"(?:'s?|s')?"


def _original_text_body(form: str) -> str:
    return r"[\s,\-]*".join(_word_regex(w) for w in form.split())


def _is_abbreviation(term: str) -> bool:
    return 2 <= len(term) <= 8 and term.upper() == term and any(c.isalpha() for c in term)


class Endpoint(BaseModel):
    name: str
    source: str = ""
    patterns: list[str] = []
    counts: list[str] = []


@lru_cache(maxsize=2)
def load_endpoints(path: str = str(ENDPOINT_VOCABULARY)) -> tuple[Endpoint, ...]:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    return tuple(Endpoint.model_validate(e) for e in raw.get("endpoints") or [])


class DiseaseScope(BaseModel):
    """The run disease's names by relation, and the outcome patterns that count it."""

    disease: str
    names: set[str] = set()  # the disease's name and exact synonyms (in scope)
    descendant_names: set[str] = set()  # subtypes and narrow synonyms (in scope)
    related_names: set[str] = set()  # parents, broad and related synonyms (related)
    ancestor_names: set[str] = set()  # every ancestor (endpoint matching only)
    abbreviations: set[str] = set()  # the ontology's own abbreviations, exact case
    related_abbreviations: set[str] = set()  # parents' abbreviations (related), exact case
    endpoint_patterns: list[str] = []  # outcome names that count this disease's events
    partial: bool = False  # descendant names capped or the ontology unavailable
    _compiled: dict[str, re.Pattern[str] | None] = PrivateAttr(default_factory=dict)
    _compiled_lists: dict[str, list[re.Pattern[str]]] = PrivateAttr(default_factory=dict)

    def _forms(self, names: Iterable[str]) -> set[str]:
        return {f for n in names if not _is_abbreviation(n) for f in name_forms(n)}

    def related_forms(self) -> set[str]:
        """Forms of the related names, plus each name's head without a trailing anatomical
        qualifier ("<kind> disease of <site>" → "<kind> disease"): how a label names the
        broader condition."""
        heads = {
            re.split(r"\s+of\s+", normalize_typography(n), maxsplit=1)[0]
            for n in self.related_names
            if re.search(r"\s+of\s+", n)
        }
        return self._forms(self.related_names | {h for h in heads if len(h.split()) >= 2})

    def _regex(self, key: str, forms: Iterable[str]) -> re.Pattern[str] | None:
        """One alternation per relation, compiled once (longest forms first)."""
        if key not in self._compiled:
            alternatives = sorted({re.escape(f) for f in forms if f}, key=len, reverse=True)
            self._compiled[key] = (
                re.compile(rf"(?<![a-z0-9])(?:{'|'.join(alternatives)})s?(?![a-z0-9])")
                if alternatives
                else None
            )
        return self._compiled[key]

    def in_scope_forms(self) -> set[str]:
        return self._forms(self.names | self.descendant_names | {self.disease})

    def head_nouns(self) -> set[str]:
        """Specific last words of the disease's own names ("steatohepatitis"): a mention of
        one alone may name a sibling condition, so it is related, never in scope. Read from
        each name in natural order, so an inverted heading's last word is not taken."""
        heads: set[str] = set()
        for name in self.names | {self.disease}:
            if _is_abbreviation(name):
                continue
            words = fold_text(uninvert_heading(name)).split()
            if (
                len(words) >= 2
                and words[-1] not in _GENERIC_HEADS
                and len(words[-1]) >= _MIN_HEAD_CHARS
            ):
                heads.add(words[-1])
        return heads

    def defined_abbreviations(self, source_text: str) -> set[str]:
        """Abbreviations a document defines for this disease: "long form (ABBR)" where the
        long form, read back from the parenthesis, is one of the disease's names."""
        found: set[str] = set()
        forms = self.in_scope_forms()
        text = normalize_typography(source_text or "")
        for m in _ABBREV_DEF.finditer(text):
            abbr = m.group(1)
            before = fold_text(text[max(0, m.start() - 120) : m.start()])
            if any(before.endswith(f) or before.endswith(f + "s") for f in forms):
                found.add(abbr)
        return found

    def match(self, text: str, source_text: str = "") -> ScopeMatch:
        """Whether ``text`` names the disease (or a subtype), a related concept, or neither.
        ``source_text`` is the document the text comes from (title and abstract), where
        abbreviations are defined."""
        if not text:
            return "none"
        folded = fold_text(text)
        if "," in text:
            folded = f"{folded} | {fold_text(uninvert_heading(text))}"
        in_scope = self._regex("in_scope", self.in_scope_forms())
        if in_scope and in_scope.search(folded):
            return "in_scope"
        abbreviations = {a for a in self.abbreviations if len(a) >= 3}
        if source_text:
            abbreviations |= self.defined_abbreviations(source_text)
            # A short ontology abbreviation ("MI") is ambiguous on its own, but not in a
            # document that names the disease in full.
            if in_scope and in_scope.search(fold_text(source_text)):
                abbreviations |= self.abbreviations
        raw = normalize_typography(text)
        if any(
            re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}s?(?![A-Za-z0-9])", raw)
            for a in abbreviations
        ):
            return "in_scope"
        related = self._regex("related", self.related_forms() | self.head_nouns())
        if related and related.search(folded):
            return "related"
        if any(
            re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}s?(?![A-Za-z0-9])", raw)
            for a in self.related_abbreviations
            if len(a) >= 3
        ):
            return "related"
        return "none"

    def patterns(self, relation: ScopeMatch, *, min_words: int = 1) -> list[re.Pattern[str]]:
        """Regexes for where the text names the disease (``in_scope``) or a related
        concept (``related``), over the original text — for callers that read the words
        around the mention (a label warning's cue). Separators between words may be a
        space, a hyphen, a comma or nothing; British spellings, possessives and plurals
        match; "<name>-like" does not. Inverted headings are matched by `match`, which
        reorders the text; prose does not use them. ``min_words`` drops shorter forms (a
        one-word category is not a condition a label warns of)."""
        key = f"patterns:{relation}:{min_words}"
        if key in self._compiled_lists:
            return self._compiled_lists[key]
        if relation == "in_scope":
            forms = self.in_scope_forms()
            abbreviations = {a for a in self.abbreviations if len(a) >= 3}
        else:
            forms = self.related_forms() | self.head_nouns()
            abbreviations = {a for a in self.related_abbreviations if len(a) >= 3}
        bodies = sorted(
            {_original_text_body(f) for f in forms if len(f.split()) >= min_words},
            key=len,
            reverse=True,
        )
        out: list[re.Pattern[str]] = []
        if bodies:
            out.append(
                re.compile(
                    rf"(?<![A-Za-z0-9])(?:{'|'.join(bodies)})s?(?![A-Za-z0-9]|-like)",
                    re.IGNORECASE,
                )
            )
        if abbreviations and min_words <= 1:
            alternatives = "|".join(sorted(map(re.escape, abbreviations), key=len, reverse=True))
            out.append(re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})s?(?![A-Za-z0-9]|-like)"))
        self._compiled_lists[key] = out
        return out

    def outcome_counts(self, outcome: str) -> bool:
        """Whether an outcome measure counts this disease's events: it names the disease (or
        a subtype), or it is an endpoint the vocabulary maps to the disease."""
        if self.match(outcome) == "in_scope":
            return True
        return any(re.search(p, outcome or "", re.IGNORECASE) for p in self.endpoint_patterns)


def _counts_disease(term: str, names: Iterable[str]) -> bool:
    """An endpoint's counted term is one of the names, or their last words ("nephropathy"
    for "IgA nephropathy")."""
    t = fold_text(term)
    for name in names:
        for form in name_forms(name):
            if form == t or form.endswith(" " + t):
                return True
    return False


def build_disease_scope(
    disease: str,
    *,
    names: Iterable[str] = (),
    narrow_names: Iterable[str] = (),
    descendant_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
    broad_names: Iterable[str] = (),
    ancestor_names: Iterable[str] = (),
    partial: bool = False,
    endpoints: Iterable[Endpoint] | None = None,
) -> DiseaseScope:
    """The scope of ``disease`` from its ontology names (all optional: without them the
    scope is the disease string alone, marked partial)."""
    own = {n for n in names if n} | {disease}
    abbreviations = {n for n in own | set(narrow_names) if _is_abbreviation(n)}
    lineage = own | set(ancestor_names) | set(parent_names)
    patterns = [
        p
        for e in (load_endpoints() if endpoints is None else endpoints)
        if any(_counts_disease(term, lineage) for term in e.counts)
        for p in e.patterns
    ]
    return DiseaseScope(
        disease=disease,
        names={n for n in own if not _is_abbreviation(n)},
        descendant_names={
            n for n in [*narrow_names, *descendant_names] if n and not _is_abbreviation(n)
        },
        related_names={n for n in [*parent_names, *broad_names] if n and not _is_abbreviation(n)},
        related_abbreviations={
            n for n in [*parent_names, *broad_names] if n and _is_abbreviation(n)
        },
        ancestor_names=set(ancestor_names),
        abbreviations=abbreviations,
        endpoint_patterns=patterns,
        partial=partial or not any(names),
    )


def scope_from_ontology(disease: str, ontology: Any | None) -> DiseaseScope:
    """`build_disease_scope` from an Open Targets ``DiseaseOntology`` (or None)."""
    if ontology is None:
        return build_disease_scope(disease, partial=True)
    fields: Mapping[str, Any] = ontology.model_dump() if hasattr(ontology, "model_dump") else {}
    return build_disease_scope(
        disease,
        names=fields.get("names") or (),
        narrow_names=fields.get("narrow_names") or (),
        descendant_names=fields.get("descendant_names") or (),
        parent_names=fields.get("parent_names") or (),
        broad_names=fields.get("broad_names") or (),
        ancestor_names=fields.get("ancestor_names") or (),
        partial=bool(fields.get("names_partial")),
    )
