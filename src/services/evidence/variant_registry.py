# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What each named variant does to the target, learned from the run's own sentences.

The allele question reads one sentence at a time and, rightly, answers "unknown" when
the sentence names an allele without saying how it changes the gene ("the B2 allele was
associated with lower risk"). Papers state a variant's functional effect in one place
and its association in another, so most association sentences about a named variant
stayed unusable although the run held the answer: another claim saying the same allele
lowers the gene's product, or that the protein change is a loss of function.

This registry collects, per run, every variant whose effect on the target a sentence
states, and fills an unknown gene change of another sentence naming the same variant.
Matching is exact and orientation-safe (pure, no I/O):

- a protein change by position and variant residue ("R46L", "p.Arg46Leu", "46L",
  "the L46 allele" are one key; the reference "R46" is another);
- an rs id only together with its allele ("the T allele of rs123", "rs123-T"); an rs id
  alone names no allele and is never matched;
- a named polymorphism's allele ("the B2 allele of the TaqIB polymorphism", "TaqIB
  B2B2"), with restriction-site names normalised ("Taq1B" = "TaqIB").

The other allele of a pair is never inferred. A truncating protein change (a stop or a
frameshift) is a loss by its consequence. A key two sentences disagree on is dropped.
Seeds come from the typed allele answers and from claims whose checked effect
(services/evidence/claim_effect.py) names a genetic change; a sentence naming more than
one variant seeds nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel

from core.evidence_text import normalize_typography
from services.evidence.direction_of_effect import names_gene

Change = Literal["loss", "gain"]
VariantKey = tuple[str, str, str]

_AA3 = {
    "Ala": "A",
    "Arg": "R",
    "Asn": "N",
    "Asp": "D",
    "Cys": "C",
    "Gln": "Q",
    "Glu": "E",
    "Gly": "G",
    "His": "H",
    "Ile": "I",
    "Leu": "L",
    "Lys": "K",
    "Met": "M",
    "Phe": "F",
    "Pro": "P",
    "Ser": "S",
    "Thr": "T",
    "Trp": "W",
    "Tyr": "Y",
    "Val": "V",
    "Ter": "*",
}
_AA1 = "ACDEFGHIKLMNPQRSTVWY"
_RES = rf"(?:[{_AA1}]|{'|'.join(k for k in _AA3 if k != 'Ter')})"
_ALT = rf"(?:[{_AA1}*X]|{'|'.join(_AA3)}|fs\w*)"
_B = r"(?<![A-Za-z0-9])"
_E = r"(?![A-Za-z0-9])"
_ALLELE_WORD = r"(?:alleles?|variants?|carriers?|polymorphisms?|genotypes?|substitutions?)"

# "R46L", "p.R46L", "p.Arg46Leu", "Y74*", "S56fs": reference, position (two digits or
# more, so "T2D" or "A1C" are not variants), variant residue.
_PROTEIN_CHANGE = re.compile(rf"{_B}(?:p\.)?(?P<ref>{_RES})(?P<pos>\d{{2,5}})(?P<alt>{_ALT}){_E}")
# "the L46 allele", "46L polymorphism": the residue a variant allele encodes.
_RESIDUE_FIRST = re.compile(rf"{_B}(?P<res>[{_AA1}])(?P<pos>\d{{2,5}})\s+{_ALLELE_WORD}\b")
_POSITION_FIRST = re.compile(rf"{_B}(?P<pos>\d{{2,5}})(?P<res>[{_AA1}])\s+{_ALLELE_WORD}\b")
# rs ids with their allele: "rs123 T allele", "rs123-T", "T allele of (the) rs123".
_RS_THEN_ALLELE = re.compile(
    r"\b(?P<rs>rs\d{3,})\s*(?:[-:/]\s*|\(\s*|\s+)(?P<a>[ACGT])\s*\)?(?:\s+allele|(?![A-Za-z0-9]))"
)
_ALLELE_THEN_RS = re.compile(
    r"\b(?P<a>[ACGT])[- ]allele\s+(?:of|at|in)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}?(?P<rs>rs\d{3,})\b"
)
# A named polymorphism: a restriction site (enzyme + roman or arabic numeral, optional
# site letter: "TaqIB", "Taq1B", "MspI") and its allele ("B2", "M1").
_SITE = r"(?P<site>[A-Z][a-z]{2,3}(?:[IV]{1,3}|[1-4])[A-Z]?)"
_SITE_ALLELE = r"(?P<al>[A-Z]\d)"
_NAMED = (
    re.compile(rf"{_B}{_SITE}\s+(?:polymorphism\s+)?{_SITE_ALLELE}(?P=al)?{_E}"),
    re.compile(rf"{_B}{_SITE_ALLELE}\s+allele\s+of\s+(?:the\s+)?(?:[\w-]+\s+){{0,2}}?{_SITE}{_E}"),
)
_ROMAN = {"1": "i", "2": "ii", "3": "iii", "4": "iv"}

# A seed's words must describe a genetic change, or the target's own level in a sentence
# naming one variant; never a drug acting on the target.
_GENETIC_WORDS = re.compile(
    r"\b(?:variants?|alleles?|carriers?|mutations?|polymorphisms?|genotypes?|"
    r"loss[- ]of[- ]function|gain[- ]of[- ]function|truncat\w*|nonsense|frameshift|"
    r"deficien\w*|homozyg\w*|heterozyg\w*|rs\d{3,})\b",
    re.IGNORECASE,
)
_DRUG_WORDS = re.compile(
    r"\b(?:inhibit\w*|antagonist|agonist|activator|blocker|blockade|degrader|treatment|"
    r"therapy|therap\w*|drugs?|antibod\w*|sirna|oligonucleotide)\b",
    re.IGNORECASE,
)


def _site_key(site: str) -> str:
    low = site.lower()
    m = re.fullmatch(r"([a-z]{3,4})([1-4]|i{1,3}|iv|v)([a-z]?)", low)
    if not m:
        return low
    return m.group(1) + _ROMAN.get(m.group(2), m.group(2)) + m.group(3)


def _one_letter(residue: str) -> str:
    if residue.startswith("fs"):
        return "fs"
    if residue == "X":
        return "*"
    return _AA3.get(residue, residue)


def variant_keys(text: str) -> dict[VariantKey, str]:
    """The variants ``text`` names, as exact keys, each with the words naming it."""
    t = normalize_typography(text or "")
    out: dict[VariantKey, str] = {}
    for m in _PROTEIN_CHANGE.finditer(t):
        out.setdefault(("p", m.group("pos"), _one_letter(m.group("alt"))), m.group(0))
    for pattern in (_RESIDUE_FIRST, _POSITION_FIRST):
        for m in pattern.finditer(t):
            out.setdefault(("p", m.group("pos"), m.group("res")), m.group(0))
    for pattern in (_RS_THEN_ALLELE, _ALLELE_THEN_RS):
        for m in pattern.finditer(t):
            out.setdefault(("rs", m.group("rs").lower(), m.group("a")), m.group(0))
    for pattern in _NAMED:
        for m in pattern.finditer(t):
            out.setdefault(("site", _site_key(m.group("site")), m.group("al")), m.group(0))
    return out


def truncating(key: VariantKey) -> bool:
    """A protein change that stops or frameshifts the protein: a loss by its consequence."""
    return key[0] == "p" and key[2] in ("*", "fs")


class RegistryEntry(BaseModel):
    change: Change
    mention: str  # the words naming the variant in the seed sentence
    source: str  # the claim id whose sentence states the change


class VariantRegistry(BaseModel):
    entries: dict[str, RegistryEntry] = {}  # "kind|id|allele" → entry
    conflicting: list[str] = []

    def get(self, key: VariantKey) -> RegistryEntry | None:
        return self.entries.get("|".join(key))


def _about_target(variant_gene: str, symbols: Iterable[str]) -> bool:
    gene = re.sub(r"[^a-z0-9]", "", (variant_gene or "").lower())
    return not gene or any(gene == re.sub(r"[^a-z0-9]", "", s.lower()) for s in symbols if s)


def _seeding_basis(basis: str, symbols: list[str]) -> bool:
    """The checked effect's words state a genetic change, or the target's own level, and
    not a drug's action."""
    if not basis or _DRUG_WORDS.search(basis):
        return False
    return bool(_GENETIC_WORDS.search(basis)) or names_gene(basis, symbols)


def build_registry(
    claims: Iterable[Any],
    answers: Mapping[str, Mapping[str, Any]],
    *,
    target_symbols: Iterable[str] = (),
) -> VariantRegistry:
    """Variants whose effect on the target a run's sentence states: a typed allele answer
    with a loss or gain, or a claim whose checked effect is a genetic change or the
    target's own level. A sentence naming more than one variant seeds nothing; a key
    seeded both ways is dropped."""
    symbols = [s for s in target_symbols if s]
    seeds: dict[str, set[str]] = {}
    first: dict[str, RegistryEntry] = {}
    for c in claims:
        cid = str(getattr(c, "evidence_id", ""))
        keys = variant_keys(str(getattr(c, "claim_text", "") or ""))
        if len(keys) != 1:
            continue
        ((key, mention),) = keys.items()
        change: str | None = None
        answer = answers.get(cid) or {}
        if answer.get("gene_change") in ("loss", "gain") and _about_target(
            str(answer.get("variant_gene") or ""), symbols
        ):
            change = str(answer["gene_change"])
        elif not getattr(c, "off_target", False) and _seeding_basis(
            str(getattr(c, "effect_basis", "") or ""), symbols
        ):
            change = {"decrease": "loss", "increase": "gain"}.get(
                str(getattr(c, "perturbation", "none"))
            )
        if change is None:
            continue
        k = "|".join(key)
        seeds.setdefault(k, set()).add(change)
        first.setdefault(k, RegistryEntry(change=change, mention=mention, source=cid))
    entries = {k: first[k] for k, changes in seeds.items() if len(changes) == 1}
    return VariantRegistry(
        entries=entries, conflicting=sorted(k for k, ch in seeds.items() if len(ch) > 1)
    )


def fill_unknown_changes(
    claims: Iterable[Any],
    answers: Mapping[str, Mapping[str, Any]],
    registry: VariantRegistry,
    *,
    target_symbols: Iterable[str] = (),
) -> tuple[dict[str, dict[str, Any]], int]:
    """``answers`` with each unknown gene change filled from the registry (or from a
    truncating consequence) when every known variant the claim names agrees; the count
    filled. A filled answer quotes the variant's words as its basis and names its source
    in ``change_source``."""
    symbols = [s for s in target_symbols if s]
    out = {cid: dict(a) for cid, a in answers.items()}
    filled = 0
    for c in claims:
        cid = str(getattr(c, "evidence_id", ""))
        answer = out.get(cid)
        if answer is None or answer.get("gene_change") in ("loss", "gain"):
            continue
        if not _about_target(str(answer.get("variant_gene") or ""), symbols):
            continue
        found: dict[str, tuple[str, str]] = {}
        for key, mention in variant_keys(str(getattr(c, "claim_text", "") or "")).items():
            if truncating(key):
                found.setdefault("loss", (mention, "a truncating change"))
            elif (entry := registry.get(key)) is not None and entry.source != cid:
                found.setdefault(entry.change, (mention, f"claim {entry.source}"))
        if len(found) != 1:
            continue
        ((change, (mention, source)),) = found.items()
        answer.update(
            gene_change=change, change_basis=mention, change_source=f"variant registry: {source}"
        )
        filled += 1
    return out, filled
