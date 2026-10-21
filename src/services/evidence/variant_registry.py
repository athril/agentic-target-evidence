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

Every change here is typed in code, never by the allele question: from the variant's
nomenclature (truncating) or from the claim's own typed effect
(services/evidence/claim_effect.py: the extractor's two fixed-choice fields, which ask
about the target itself). Seeds are claims naming exactly one variant whose change is
known; a sentence naming more than one variant seeds nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel

from core.evidence_text import normalize_typography

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


class KnownChange(BaseModel):
    """A claim's gene change, typed in code, and where it came from."""

    change: Change
    source: str  # "consequence" | "claim" | "registry: <claim id>"
    basis: str = ""  # the words stating it: the claim's effect span, or the variant named


def claim_changes(claims: Iterable[Any]) -> dict[str, KnownChange]:
    """{claim id: change} for claims whose gene change is known without asking: a loss
    when the claim names a truncating change; otherwise the claim's own typed effect
    (``effect_source == "claim"``: decrease → loss, increase → gain). A drug's action
    (``effect_source == "drug"``) is not a variant's, and an off-target claim says nothing
    of the target."""
    out: dict[str, KnownChange] = {}
    for c in claims:
        cid = str(getattr(c, "evidence_id", ""))
        truncation = next(
            (
                m
                for k, m in variant_keys(str(getattr(c, "claim_text", "") or "")).items()
                if truncating(k)
            ),
            None,
        )
        change = {"decrease": "loss", "increase": "gain"}.get(str(getattr(c, "perturbation", "")))
        if truncation is not None:
            out[cid] = KnownChange(change="loss", source="consequence", basis=truncation)
        elif (
            change is not None
            and getattr(c, "effect_source", "none") == "claim"
            and not getattr(c, "off_target", False)
        ):
            out[cid] = KnownChange(
                change=change,
                source="claim",
                basis=str(getattr(c, "effect_basis", "") or ""),
            )
    return out


class RegistryEntry(BaseModel):
    change: Change
    mention: str  # the words naming the variant in the seed sentence
    source: str  # the claim id whose sentence states the change


class VariantRegistry(BaseModel):
    entries: dict[str, RegistryEntry] = {}  # "kind|id|allele" → entry
    conflicting: list[str] = []

    def get(self, key: VariantKey) -> RegistryEntry | None:
        return self.entries.get("|".join(key))


def build_registry(claims: Iterable[Any], changes: Mapping[str, KnownChange]) -> VariantRegistry:
    """Variants whose effect on the target a run's sentence states: claims naming exactly
    one variant whose change is known (``claim_changes``). A key seeded both ways is
    dropped."""
    seeds: dict[str, set[str]] = {}
    first: dict[str, RegistryEntry] = {}
    for c in claims:
        cid = str(getattr(c, "evidence_id", ""))
        known = changes.get(cid)
        keys = variant_keys(str(getattr(c, "claim_text", "") or ""))
        if known is None or len(keys) != 1:
            continue
        ((key, mention),) = keys.items()
        k = "|".join(key)
        seeds.setdefault(k, set()).add(known.change)
        first.setdefault(k, RegistryEntry(change=known.change, mention=mention, source=cid))
    entries = {k: first[k] for k, ch in seeds.items() if len(ch) == 1}
    return VariantRegistry(
        entries=entries, conflicting=sorted(k for k, ch in seeds.items() if len(ch) > 1)
    )


def fill_unknown_changes(
    claims: Iterable[Any], changes: Mapping[str, KnownChange], registry: VariantRegistry
) -> tuple[dict[str, KnownChange], int]:
    """``changes`` with each claim whose change is unknown filled from the registry (or from
    a truncating consequence) when every known variant the claim names agrees; the count
    filled. A filled change quotes the variant's words as its basis."""
    out = dict(changes)
    filled = 0
    for c in claims:
        cid = str(getattr(c, "evidence_id", ""))
        if cid in out:
            continue
        found: dict[str, tuple[str, str]] = {}
        for key, mention in variant_keys(str(getattr(c, "claim_text", "") or "")).items():
            if truncating(key):
                found.setdefault("loss", (mention, "consequence"))
            elif (entry := registry.get(key)) is not None and entry.source != cid:
                found.setdefault(entry.change, (mention, f"registry: {entry.source}"))
        if len(found) != 1:
            continue
        ((change, (mention, source)),) = found.items()
        out[cid] = KnownChange(change=change, source=source, basis=mention)
        filled += 1
    return out, filled
