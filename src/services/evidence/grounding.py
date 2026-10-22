# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Literature mentions grounded to identifiers, sentence by sentence.

Whether a level is the target's, whether a knockout was of the target and whether two
sentences name the same variant were decided from strings, by the extractor or by
regexes. PubTator3 (mcp_servers/pubtator) marks each mention with an identifier; this
module turns a paper's annotations into numbered sentences whose mentions are ids:

* a gene is the target when PubTator's NCBI Gene id is the target's. The approved-symbol
  rule: a mention written exactly as the target's HGNC approved symbol is the target when
  PubTator chose a gene for which that text is only an alias or a previous symbol (HGNC
  approved symbols are unique; "MET" was read as NCBI Gene 79811, whose alias is "Met");
* a variant belongs to the target when its tmVar ``CorrespondingGene`` is the target's
  NCBI id. The allele its carriers carry comes from its HGVS (the variant base or
  residue) or, for an rs id, from an allele written next to it ("rs123 T allele");
* a chemical names one of the target's drugs when its text or PubTator name is one of
  the drug names (Open Targets known drugs);
* a disease is in scope when its MeSH id is one the run's disease or a subtype
  cross-references (Open Targets ``dbXRefs``), or, failing that, when its words name the
  disease or a subtype by the ontology's own names (``DiseaseScope.match``, with the
  abbreviations the paper defines). The ids alone miss diseases without a MeSH
  cross-reference (NASH has none: every mention of it was out of scope) and descriptors
  the paper's wording maps to ("CHD" → Coronary Disease, not Coronary Artery Disease);
* species are NCBI taxon ids.

Sentence selection (``select_sentences``) is deterministic: a sentence mentioning the
target, a variant tied to it or one of its drugs, together with an outcome mention (a
disease or a chemical), capped per paper.

Pure module except ``rehome_ids``, which asks HGNC about the genes the approved-symbol
rule needs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from mcp_servers.pubtator.tools import PubTatorAnnotation, PubTatorDocument
from services.evidence.drug_precedent import drug_key

if TYPE_CHECKING:
    from services.evidence.disease_scope import DiseaseScope

MentionKind = Literal["gene", "variant", "chemical", "disease", "species", "cell_line", "other"]
_KINDS: dict[str, MentionKind] = {
    "gene": "gene",
    "variant": "variant",
    "dnamutation": "variant",
    "proteinmutation": "variant",
    "snp": "variant",
    "chemical": "chemical",
    "disease": "disease",
    "species": "species",
    "cellline": "cell_line",
}
# Sentences read per paper; claims point at some of a paper's sentences, and those come
# first. Reading takes about 2.2 s a sentence on the local model and must fit in about 20
# minutes a run: at 4 a paper, the PCSK9 and CETP resumes selected 1143 and 761 sentences;
# at 1, 407 and 258.
MAX_SENTENCES_PER_PAPER = 1


class TargetIdentity(BaseModel):
    """The target as identifiers (HGNC, resolved by the planner)."""

    symbol: str
    ncbi_id: str = ""
    name: str = ""
    aliases: list[str] = []
    previous_symbols: list[str] = []
    # NCBI Gene ids of the target's orthologs in other species (biology_lens: a knockout of
    # the mouse gene is a knockout of the target).
    ortholog_ids: list[str] = []


class Mention(BaseModel):
    number: int  # 1-based within its sentence, as the reader sees it
    kind: MentionKind
    text: str
    id: str = ""  # NCBI Gene id, rs id or HGVS, MeSH id, taxon id
    start: int = 0  # offsets within the sentence
    end: int = 0
    is_target: bool = False  # gene: the target; variant: belongs to the target
    ortholog: bool = False  # gene: an ortholog of the target in another species
    gene_id: str = ""  # variant: the gene it belongs to
    rsid: str = ""
    hgvs: str = ""
    allele: str = ""  # variant: the allele its carriers carry ("" when not stated)
    drug: str = ""  # chemical: the target's drug it names (drug key)
    in_scope: bool = False  # disease: in the run's scope, by MeSH id or by its words

    def label(self) -> str:
        """How the reader is shown the mention."""
        if self.kind == "gene":
            what = (
                "the target"
                if self.is_target
                else "the target's ortholog"
                if self.ortholog
                else "another gene"
            )
            return f"gene, {what}"
        if self.kind == "variant":
            owner = "of the target" if self.is_target else "of another gene"
            ident = f", {self.rsid}" if self.rsid else ""
            return f"variant {owner}{ident}"
        if self.kind == "chemical":
            return "drug acting on the target" if self.drug else "chemical"
        if self.kind == "disease":
            return "disease or phenotype"
        return self.kind.replace("_", " ")


class GroundedSentence(BaseModel):
    source: str  # "PMID:123"
    index: int  # 0-based over the paper's passages, title first
    text: str
    mentions: list[Mention] = []

    @property
    def exposures(self) -> list[Mention]:
        return [
            m
            for m in self.mentions
            if (m.kind in ("gene", "variant") and (m.is_target or m.ortholog))
            or (m.kind == "chemical" and m.drug)
        ]

    @property
    def outcomes(self) -> list[Mention]:
        return [
            m for m in self.mentions if m.kind == "disease" or (m.kind == "chemical" and not m.drug)
        ]

    def species(self) -> list[str]:
        return [m.id for m in self.mentions if m.kind == "species" and m.id]


class GroundedPaper(BaseModel):
    source: str
    date: str = ""
    sentences: list[GroundedSentence] = []

    def species(self) -> list[str]:
        """Taxon ids the paper mentions, most mentioned first."""
        counts: dict[str, int] = {}
        for s in self.sentences:
            for t in s.species():
                counts[t] = counts.get(t, 0) + 1
        return sorted(counts, key=lambda t: -counts[t])


# ── sentences ───────────────────────────────────────────────────────────────────────

# A boundary is ".", "!" or "?" then whitespace then a capital, a digit or an opening
# bracket — unless the word before is an abbreviation or an initial.
_BOUNDARY = re.compile(r"[.!?](?=\s+[A-Z0-9(\[])")
_NO_BREAK_BEFORE = re.compile(
    r"(?:\b(?:e\.g|i\.e|vs|al|Fig|Figs|approx|ca|cf|Dr|No|Nos|Ref|resp|sp|spp|St)|"
    r"(?<![A-Za-z])[A-Z])$"
)


def split_sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence of ``text``, whitespace trimmed."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if _NO_BREAK_BEFORE.search(text[start : m.start()]):
            continue
        spans.append((start, m.end()))
        start = m.end()
    spans.append((start, len(text)))
    out: list[tuple[int, int]] = []
    for a, b in spans:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            out.append((a, b))
    return out


# ── mentions ────────────────────────────────────────────────────────────────────────

# A change in HGVS: the base or residue the variant carries ("c.-629C>A" → A,
# "p.R46L" → L, "p.Y74*" → *, "p.S56fs" → fs).
_HGVS_DNA_ALT = re.compile(r">(?P<alt>[ACGT])$")
_HGVS_PROTEIN = re.compile(
    r"^p\.\(?(?P<ref>[A-Z](?:[a-z]{2})?)(?P<pos>\d+)(?P<alt>[A-Z](?:[a-z]{2})?|\*|X|fs\w*)\)?$"
)
_AA3 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E",
    "Gly": "G", "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F",
    "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "Ter": "*",
}  # fmt: skip
# An allele written next to an rs id: "rs123 T allele", "rs123-T", "(rs123, T)", or
# "the T allele of (the) rs123", "the AA genotype of rs123".
_ALLELE_AFTER = re.compile(r"^[\s(,:/-]{0,3}(?P<a>[ACGT])(?![A-Za-z0-9])")
_ALLELE_BEFORE = re.compile(
    r"(?<![A-Za-z0-9])(?P<a>[ACGT])(?P=a)?[- ](?:allele|genotype|carriers?)\s+(?:of|at|in)\s+"
    r"(?:the\s+)?(?:\S+\s+){0,2}$"
)
# A mention naming one allele by its position: "-629A", "-629AA" (a genotype), "46L",
# "L46"; and a pair of alleles right after it ("-629C/A"), which names none.
_ONE_BASE = re.compile(r"^(?:[cg]\.)?[-+*]?\d+(?:[-+]\d+)?(?P<a>[ACGT])(?P=a)?$")
_ONE_RESIDUE = re.compile(r"^(?:(?P<a>[A-Z])\d+|\d+(?P<b>[A-Z]))$")
_PAIR_AFTER = re.compile(r"^\s*[/>]\s*[ACGT](?![A-Za-z0-9])")


def residue(code: str) -> str:
    """A residue in one-letter form ("Leu" → "L", "X" and "Ter" → "*", "fs…" → "fs")."""
    if code.startswith("fs"):
        return "fs"
    if code in ("X", "*"):
        return "*"
    return _AA3.get(code, code)


def hgvs_alleles(hgvs: str) -> tuple[str, str] | None:
    """(reference, variant) of a single-base DNA change or a protein change in HGVS, in
    the HGVS's own orientation; None for anything else."""
    h = (hgvs or "").strip()
    if h.startswith(("c.", "g.", "n.", "m.")):
        m = re.search(r"(?P<ref>[ACGT])>(?P<alt>[ACGT])$", h)
        return (m.group("ref"), m.group("alt")) if m else None
    m = _HGVS_PROTEIN.match(h)
    if m:
        return residue(m.group("ref")), residue(m.group("alt"))
    return None


def carried_allele(annotation: PubTatorAnnotation, sentence: str, start: int, end: int) -> str:
    """The allele the sentence's carriers of this variant carry (nomenclature grammar):
    the one base or residue the mention names ("-629A"), the variant base or residue of a
    change ("C-629A", "R46L"), or a base written next to an rs id; "" when not stated,
    or when a pair follows the mention ("-629C/A")."""
    text = annotation.text.strip()
    if _PAIR_AFTER.match(sentence[end:]):
        return ""
    one = _ONE_BASE.match(text) or _ONE_RESIDUE.match(text)
    if one is not None:
        return next(g for g in one.groups() if g)
    alleles = hgvs_alleles(annotation.hgvs)
    if alleles is not None and text.lower() != annotation.rsid:
        return alleles[1]
    m = _ALLELE_AFTER.match(sentence[end:])
    if m:
        return m.group("a")
    m = _ALLELE_BEFORE.search(sentence[:start])
    return m.group("a") if m else ""


def _ids(identifier: str) -> list[str]:
    return [i.strip() for i in re.split(r"[;,]", identifier or "") if i.strip()]


def _mesh(identifier: str) -> str:
    ident = (identifier or "").strip()
    return ident.split(":", 1)[1] if ident.upper().startswith("MESH:") else ""


def needs_rehoming(doc: PubTatorDocument, target: TargetIdentity) -> set[str]:
    """NCBI Gene ids PubTator chose for mentions written as the target's approved symbol,
    other than the target: the approved-symbol rule asks HGNC about them."""
    out: set[str] = set()
    for p in doc.passages:
        for a in p.annotations:
            if a.type.lower() == "gene" and a.text.strip() == target.symbol:
                ids = _ids(a.identifier)
                if target.ncbi_id not in ids:
                    out.update(ids)
    return out


def rehomed(text: str, ids: Sequence[str], target: TargetIdentity, hgnc: Mapping[str, Any]) -> bool:
    """The approved-symbol rule: ``text`` is the target's approved symbol and, for every
    gene PubTator chose, HGNC lists that text only as an alias or previous symbol.

    ``hgnc`` maps NCBI Gene ids to HGNC records (``aliases``, ``previous_symbols``,
    ``symbol``); a gene HGNC does not know (another species) is not rehomed."""
    if text.strip() != target.symbol or not ids:
        return False
    word = text.strip().upper()
    for i in ids:
        record = hgnc.get(i)
        if record is None:
            return False
        symbol = str(getattr(record, "symbol", "") or "").upper()
        others = {
            str(s).upper()
            for s in [
                *(getattr(record, "aliases", []) or []),
                *(getattr(record, "previous_symbols", []) or []),
            ]
        }
        if symbol == word or word not in others:
            return False
    return True


def _mention(
    a: PubTatorAnnotation,
    number: int,
    sentence: str,
    start: int,
    end: int,
    *,
    target: TargetIdentity,
    hgnc: Mapping[str, Any],
    drug_names: Mapping[str, str],
    scope_mesh: frozenset[str],
    scope: DiseaseScope | None = None,
    document: str = "",
) -> Mention:
    kind = _KINDS.get(a.type.lower().replace(" ", ""), "other")
    base = {"number": number, "kind": kind, "text": a.text, "start": start, "end": end}
    if kind == "gene":
        ids = _ids(a.identifier)
        is_target = (bool(target.ncbi_id) and target.ncbi_id in ids) or (
            not target.ncbi_id and a.name == target.symbol
        )
        if not is_target and rehomed(a.text, ids, target, hgnc):
            is_target, ids = True, [target.ncbi_id]
        ortholog = not is_target and any(i in target.ortholog_ids for i in ids)
        return Mention(**base, id=";".join(ids), is_target=is_target, ortholog=ortholog)
    if kind == "variant":
        gene = _ids(a.gene_id)
        return Mention(
            **base,
            id=a.rsid or a.hgvs,
            is_target=bool(target.ncbi_id) and target.ncbi_id in gene,
            gene_id=";".join(gene),
            rsid=a.rsid,
            hgvs=a.hgvs,
            allele=carried_allele(a, sentence, start, end),
        )
    if kind == "chemical":
        drug = next(
            (drug_names[k] for k in (drug_key(a.text), drug_key(a.name)) if k in drug_names),
            "",
        )
        return Mention(**base, id=_mesh(a.identifier) or a.identifier, drug=drug)
    if kind == "disease":
        mesh = _mesh(a.identifier)
        in_scope = (bool(mesh) and mesh in scope_mesh) or (
            scope is not None and scope.match(a.text, document) == "in_scope"
        )
        return Mention(**base, id=mesh or a.identifier, in_scope=in_scope)
    return Mention(**base, id=a.identifier)


def ground_paper(
    doc: PubTatorDocument,
    target: TargetIdentity,
    *,
    hgnc: Mapping[str, Any] | None = None,
    drug_names: Iterable[str] = (),
    scope_mesh: Iterable[str] = (),
    scope: DiseaseScope | None = None,
) -> GroundedPaper:
    """The paper's sentences, title first, each with its mentions as identifiers.

    ``hgnc`` holds the HGNC records of the genes ``needs_rehoming`` named;
    ``drug_names`` are the target's known drugs; ``scope_mesh`` the MeSH ids of the
    run's disease scope, and ``scope`` its names (a disease mention whose id is not in
    ``scope_mesh`` is in scope when its words name the disease)."""
    drugs = {drug_key(d): drug_key(d) for d in drug_names if d}
    mesh_scope = frozenset(scope_mesh)
    document = " ".join(p.text for p in doc.passages)
    sentences: list[GroundedSentence] = []
    for passage in doc.passages:
        for a_start, a_end in split_sentences(passage.text):
            lo, hi = passage.offset + a_start, passage.offset + a_end
            text = passage.text[a_start:a_end]
            inside = sorted(
                (a for a in passage.annotations if lo <= a.offset and a.offset + a.length <= hi),
                key=lambda a: a.offset,
            )
            mentions = [
                _mention(
                    a,
                    n,
                    text,
                    a.offset - lo,
                    a.offset - lo + a.length,
                    target=target,
                    hgnc=hgnc or {},
                    drug_names=drugs,
                    scope_mesh=mesh_scope,
                    scope=scope,
                    document=document,
                )
                for n, a in enumerate(inside, start=1)
            ]
            sentences.append(
                GroundedSentence(
                    source=f"PMID:{doc.pmid}", index=len(sentences), text=text, mentions=mentions
                )
            )
    return GroundedPaper(source=f"PMID:{doc.pmid}", date=doc.date, sentences=sentences)


# ── selection ───────────────────────────────────────────────────────────────────────


# A structured abstract's own label (NLM's categories) for a part that states an aim or a
# method, not a finding: such a sentence is read only when nothing else is selectable.
_AIM_LABEL = re.compile(
    r"^(?:BACKGROUND|INTRODUCTION|OBJECTIVES?|AIMS?|PURPOSE|METHODS?|MATERIALS AND METHODS|"
    r"DESIGN|SETTING|PARTICIPANTS)\s*:"
)


def selectable(sentence: GroundedSentence) -> bool:
    """The sentence names the target (or its ortholog), a variant of the target or one of
    its drugs, and an outcome: a disease or a chemical other than that drug."""
    return bool(sentence.exposures) and bool(sentence.outcomes)


def select_sentences(
    paper: GroundedPaper,
    claimed: Iterable[int] = (),
    cap: int = MAX_SENTENCES_PER_PAPER,
) -> list[GroundedSentence]:
    """Up to ``cap`` selectable sentences of the paper, in reading order: those a claim
    points to first, then, past any labelled as an aim or a method, those naming a variant
    of the target or one of its drugs, then those with a disease outcome (in scope first),
    later sentences (results, conclusions) before earlier ones."""
    wanted = set(claimed)
    pool = [s for s in paper.sentences if selectable(s)]

    def rank(s: GroundedSentence) -> tuple[int, int, int, int, int, int]:
        typed = any(m.kind in ("variant", "chemical") for m in s.exposures)
        disease = any(m.kind == "disease" for m in s.outcomes)
        scoped = any(m.in_scope for m in s.outcomes)
        aim = bool(_AIM_LABEL.match(s.text))
        return (s.index not in wanted, aim, not typed, not disease, not scoped, -s.index)

    chosen = sorted(pool, key=rank)[:cap]
    return sorted(chosen, key=lambda s: s.index)
