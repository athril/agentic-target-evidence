# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic checks for a gene-profile draft. No LLM involved.

Usage:
    uv run python .claude/skills/gene-profile/scripts/check_profile.py <run_dir> [--draft draft.md]

Errors (exit 1):
    E_UNKNOWN_KEY        [@key] does not exist in sources.json
    E_UNVERIFIED         cited source whose metadata was not fetched from an authoritative API
    E_ANCHOR             {{key:path|fmt}} does not resolve to a value in raw/<key>.json, or
                         its format does not apply (unknown format, a long list unformatted)
    E_ANCHOR_UNIT        the text around an anchor misstates its unit: a fraction followed
                         by '%' (use |pct), a count followed by '-fold'
    E_ANCHOR_NOT_CITED   a block uses a value from raw/<key>.json but does not cite [@key]
    E_UNCITED            a factual block (paragraph, list item, table row) has no citation
    E_TIER3_SNAPSHOT     a Tier-3 (press/news/web) source is cited inside the Snapshot
    E_MISSING_SECTION    a required section heading is absent
    E_GUARD_CONSTRAINT   text contradicts gnomAD constraint bands (repo guard)
    E_GUARD_COMMERCIAL   text overstates commercial whitespace (repo guard)
    E_UNKNOWN_BLOCK      a {{table:…}} / {{figure:…}} line names no known block
    E_MISSING_TABLE      a required block is absent from its section (the allelic series in
                         Human genetics, when the run has one; the indication table in
                         Indication fit, when the run is scoped to a disease)
    E_SCOPE_CONTRADICTION the text says no disease was given, but the run is scoped to one
    E_VARIANT_FUNCTION   an entry of variant_function.json is malformed, names no variant of
                         the series, cites an unregistered paper, or its quote or variant is
                         not in that paper's abstract
    E_HOW_WE_KNOW        how_we_know.json is missing (template-2 runs), malformed, has a claim
                         without a supporting key, or cites a key not in sources.json
    E_MECHANISM          mechanism.json is missing (template-2 runs, standard or deep depth),
                         malformed, has a step without keys or with an unknown key, or a
                         compartment outside UniProt's subcellular-location vocabulary
    E_OPEN_QUESTION      open_questions.json is missing (as mechanism.json), malformed, has a
                         question without an evidence key, or an unknown data or status key
    E_CLAIM_CHECK        claim_checks.json is missing (as mechanism.json), malformed, leaves a
                         How-we-know claim unchecked, or records a search that was not run, an
                         outcome without what was read, or a changed claim without its paper
    E_CLAIM_AUDIT        a block citing a paper has no current verdict in claim_audit.json, a
                         verdict other than supported, or a quote not in the cited abstracts
Warnings:
    W_BARE_NUMBER        decimal/percentage typed by hand instead of a {{key:path}} anchor
    W_TIER3_ONLY         a block's only support is Tier-3 sources
    W_EMPTY_SECTION      a section heading with no content
    W_GUARDS_UNAVAILABLE repo guard modules could not be imported
    W_OFF_TARGET_CITATION a cited article PubTator3 annotated without any mention of the
                         target, an ortholog, a variant of it or one of its drugs
    W_GENERATED_SECTION  the draft writes a section render.py generates (References,
                         Appendix, Run Information, Known issues); it is dropped at render
    W_UNGROUNDED_NAME    a proper name (company, institution, place, person) in a sentence
                         whose cited records and abstracts never mention it: the citation
                         does not support the sentence
    W_OVERCLAIM_SAFETY   a safety conclusion the sources cannot carry: "safe", "clean safety
                         record", "no safety risk/concerns", or "no liabilities" without saying
                         it is the curated list that has none
    W_FAERS_INFERENCE    a FAERS sentence that reads reports as a rate, incidence or
                         safety, or ranks drugs by report volume
    W_ESSENTIALITY_SCOPE a cell-line screen result stretched beyond cell-autonomous fitness
                         ("not a cancer dependency", "irrelevant to cancer")
    W_STRING_BINDING     binding or physical interaction supported only by STRING, whose
                         scores are functional associations
    W_ASSOCIATION_COUNT  the Open Targets disease total quoted without "with any evidence"
    W_PROVEN             "proven": say which endpoint each modality met
    W_STRENGTH_PROSE     a sentence calls a claim established, shown or proven citing only
                         papers of a How-we-know claim whose computed strength is one group
                         or cells only
    W_CROSS_KIND_CONTRADICTION a How-we-know source against a claim is a different kind of
                         evidence from all its support: it may not test the same question
    W_DRUG_NO_READOUT    an approved drug of the drug list that Translational context never
                         names: say what it showed, or that nothing was found
    W_SCREEN_INFERENCE   a conclusion against a role drawn from a screen's hit count: few hits
                         neither support nor argue against one
    W_INFERENCE_IN_FACT  a fact block citing a paper goes on to what follows from it ("so",
                         "therefore"): the inference belongs in *Interpretation:*
    W_VERB_NOT_IN_SOURCE a biochemical verb that no cited abstract uses: keep the source's verb
    W_RECORD_ONLY_MECHANISM a mechanism stated in Biology on a curated record's sentence
                         alone: read and cite the paper behind it
    E_CONSTRAINT_WORDING prose on pLoF depletion that the gene's LOEUF contradicts
    E_THERAPY_NOT_DISCUSSED an approved therapy selecting patients by the gene that
                         Translational context never names
    W_OUTCOME_UNREAD     a large completed trial with result papers that the prose never reaches
    W_SAFETY_EXTRAPOLATION a risk stated for every modality, or constraint used to negate
                         disease evidence
    W_RECLASSIFICATION   a ClinVar classification called wrong ("misclassification", "best
                         read as …") without citing a paper: a call is for the condition it
                         names; state that condition, its review status and submitters, and
                         put the conflict in Contested

Questions need a citation like any claim: an open question cites the evidence that leaves
it open. Block lines are checked as blocks, not as prose; horizontal rules and
abbreviation lines carry no claim. Hand-typed numbers are checked per sentence: a sentence
quoting a paper may carry its numbers, a sentence citing only database records may not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_blocks import BLOCKS, blank_blocks, find_blocks, load_functions, required  # noqa: E402
from profile_claims import (  # noqa: E402
    AUDIT_FILE,
    CHECK_FILE,
    audit_problems,
    block_id,
    check_problems,
    gene_terms,
    load_searches,
)
from profile_schema import (  # noqa: E402
    ANCHOR_RE,
    CITE_GROUP_RE,
    GENERATED_SECTIONS,
    SECTION_TITLES,
    TEMPLATE_VERSION,
    Manifest,
    Source,
    anchor_problem,
    anchor_value,
    cited_keys,
    draft_sections,
    load_manifest,
    load_raw,
    load_sources,
    resolve_anchor,
)
from profile_tables import (
    constraint_wording_problem,  # noqa: E402
    function_problems,  # noqa: E402
)

BlockKind = Literal["para", "item", "row"]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_INTERP_RE = re.compile(r"^[>\s]*[*_]{0,2}\s*(interpretation|analyst note)\b", re.IGNORECASE)
# "Not checked …" / "Not assessed …" describe the run, not the gene (e.g. sections skipped at
# --depth quick); the coverage appendix is their evidence.
_NOT_CHECKED_RE = re.compile(r"^[>\s]*[*_]{0,2}\s*not (checked|assessed)\b", re.IGNORECASE)
_LABEL_RE = re.compile(r"^[*_]{1,2}[^*_]{1,80}[*_]{1,2}:?$")
_BARE_NUMBER_RE = re.compile(r"(?<![\w.:/-])(\d+\.\d+|\d+(?:\.\d+)?\s?%)(?![\w.])")
_NUMBERING_RE = re.compile(r"^\d+(\.\d+)*[.)]?\s+")
_RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")  # a horizontal rule
_ABBREV_RE = re.compile(r"^[>\s]*[*_]{1,2}\s*(abbreviations?|glossary)\b", re.IGNORECASE)
# Sentence ends: a full stop / ? / ! (after any citation group) followed by a capital.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*_(\[])")
_RECLASSIFY_RE = re.compile(
    r"\b(mis-?classif\w*|best (read|understood|interpreted) as|erroneous(ly)?|in error"
    r"|(wrongly|incorrectly) (classified|called)|likely (wrong|incorrect))\b",
    re.IGNORECASE,
)
_SAFETY_OVERCLAIM_RE = re.compile(
    r"\b(clean safety|clean (?:record|profile)s?|(?:is|are|was|were|appears?|proven|considered|"
    r"remains?|be) (?:to be |generally |entirely |completely )?safe\b|"
    r"no (?:known |major |significant )?safety (?:risks?|concerns?|issues?|signals?|liabilit\w+)|"
    r"no (?:known |major )?(?:on-target )?(?:liabilit\w+|toxicit\w+))",
    re.IGNORECASE,
)
_FAERS_RE = re.compile(r"\b(FAERS|adverse[- ]event reports?)\b", re.IGNORECASE)
_FAERS_INFERENCE_RE = re.compile(
    r"\b(than|rates?|incidence|safer|safe|clean|frequenc\w+)\b", re.IGNORECASE
)
_ESSENTIALITY_SCOPE_RE = re.compile(
    r"\b(not an? (?:cancer |tumou?r )?dependency|(?:irrelevant|not relevant) (?:to|in|for) "
    r"(?:cancer|tumou?rs?|oncology)|no role in (?:cancer|tumou?rs?)|not (?:a )?cancer[- ]relevant)",
    re.IGNORECASE,
)
_BINDING_RE = re.compile(
    r"\b(bind(?:s|ing)?(?: partners?)?|partners?|physical(?:ly)? interact\w*|direct(?:ly)? interact\w*|"
    r"complex(?:es)? with)\b",
    re.IGNORECASE,
)
_SCREEN_KEYS = {"biogrid_orcs", "depmap", "project_score"}
_SCREEN_CONCLUSION_RE = re.compile(
    r"\b(suggests? no role|no role|no function|not involved|argues? against|rules? out)\b",
    re.IGNORECASE,
)
_DENIED_CONCLUSION_RE = re.compile(r"\b(not evidence|neither|nor|does not)\b", re.IGNORECASE)
_INFERENCE_RE = re.compile(
    r"[,;:]\s*(so|thus|therefore|hence|which means|meaning that|implying)\b", re.IGNORECASE
)
# Verbs with a precise biochemical meaning, each with the words a source may use for it.
_BIOCHEMICAL_VERBS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), stems)
    for pattern, stems in (
        (r"\b(cut|cuts|cutting|cleav\w+|proteolys\w+)\b", ("cleav", "proteoly")),
        (r"\b(phosphorylat\w+)\b", ("phosphorylat",)),
        (r"\b(ubiquitinat\w+|ubiquitylat\w+)\b", ("ubiquit",)),
        (r"\b(acetylat\w+)\b", ("acetylat",)),
        (r"\b(methylat\w+)\b", ("methylat",)),
        (r"\b(sumoylat\w+)\b", ("sumoylat",)),
        (r"\b(glycosylat\w+)\b", ("glycosylat",)),
        (r"\b(hydroly[sz]\w+)\b", ("hydroly",)),
        (r"\b(sequester\w*)\b", ("sequest",)),
        (r"\b(chaperon\w+)\b", ("chaperon",)),
    )
)
_NEGATED_RE = re.compile(r"\b(not|never|without|no)\b(\W+\w+){0,2}\W*$", re.IGNORECASE)
_VERB_SECTIONS = {"snapshot", "biology", "genetics", "status"}
_RECORD_TEXT_KEYS = {"uniprot_extra", "uniprot_profile", "ncbi_gene"}
_MECHANISM_RE = re.compile(
    r"\b(cleav\w+|degrad\w+|inactivat\w+|activat\w+|inhibit\w+|binds?|binding|phosphorylat\w+"
    r"|ubiquitinat\w+|regulat(?:es?|ed|ion)|induc\w+|repress\w+)\b",
    re.IGNORECASE,
)
_ASSOC_TOTAL = "ot_associations:total_associated_diseases"
_ANY_EVIDENCE_RE = re.compile(r"\b(any evidence|score > ?0|any score)\b", re.IGNORECASE)
# "biopsy-proven", "genetically-proven" describe a diagnosis, not a modality's evidence.
_PROVEN_RE = re.compile(r"(?<![-\w])proven\b", re.IGNORECASE)
# Sentence and clause breaks outside a citation group ("[@a; @b]" stays whole).
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?![^\[\]]*\])")
_ESTABLISHED_RE = re.compile(
    r"\b(?:established|(?:clearly |conclusively )?(?:shown|demonstrated|proven)|confirmed)\b",
    re.IGNORECASE,
)
_NO_PARTNERS_RE = re.compile(r"\bno (?:\w+ )?partners?\b", re.IGNORECASE)
# An absence stated as the curated record's: "no curated liability in Open Targets".
_CURATED_ABSENCE_RE = re.compile(r"\bcurated\b", re.IGNORECASE)
_CLASSIFICATION_RE = re.compile(
    r"\b(clinvar|classification|classified|calls?|P/LP)\b", re.IGNORECASE
)
# "no disease was given" — the reading of an unscoped run.
_UNSCOPED_RE = re.compile(
    r"\b(no (specific |particular )?(disease|indication)s? (was |were |is )?"
    r"(given|indicated|specified|provided|selected|requested)"
    r"|not scoped to (a|any|one) (disease|indication)"
    r"|as a gene target only)",
    re.IGNORECASE,
)
_FOLD_AFTER_RE = re.compile(r"^\s*-?\s*fold\b", re.IGNORECASE)
# A capitalised word or run of them mid-sentence: a company, institution, place or person.
_NAME_RE = re.compile(r"(?<![\w-])([A-Z][a-z]{2,}(?:[ -][A-Z][a-z]{2,})*)(?![\w-])")
# Eponyms and pathway names that are vocabulary, not sources' content.
_COMMON_NAMES = {
    "mendelian", "gaussian", "western", "northern", "southern", "bonferroni", "kaplan", "meier",
    "hardy", "weinberg", "golgi", "purkinje", "schwann", "leydig", "sertoli", "langerhans",
    "kupffer", "paneth", "henle", "bowman", "wnt", "hedgehog", "notch", "hippo", "toll",
    "bayes", "bayesian", "fisher", "pearson", "spearman", "student", "mann", "whitney", "cox",
    # three-letter amino-acid codes ("the Asp–His–Ser triad", "Arg to Cys")
    "ala", "arg", "asn", "asp", "cys", "gln", "glu", "gly", "his", "ile", "leu", "lys", "met",
    "phe", "pro", "ser", "thr", "trp", "tyr", "val", "ter",
}  # fmt: skip


@dataclass
class Block:
    kind: BlockKind
    text: str
    line: int
    section: str  # section key ("" before the first known heading)


@dataclass
class Issue:
    code: str
    line: int
    message: str


@dataclass
class CheckReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "errors": [asdict(i) for i in self.errors],
                "warnings": [asdict(i) for i in self.warnings],
                "stats": self.stats,
            },
            indent=2,
            ensure_ascii=False,
        )


_TITLE_TO_KEY = {title.lower(): key for key, title in SECTION_TITLES.items()}


def section_key(heading: str) -> str:
    """Map a '## 3 Human genetics' style heading to its section key ('' if unknown;
    'generated' for a section render.py writes itself)."""
    title = _NUMBERING_RE.sub("", heading).strip().lower()
    if any(title.startswith(g.lower()) for g in GENERATED_SECTIONS):
        return "generated"
    if title in _TITLE_TO_KEY:
        return _TITLE_TO_KEY[title]
    for t, k in _TITLE_TO_KEY.items():
        if title.startswith(t):
            return k
    return ""


def parse_blocks(draft: str) -> tuple[list[Block], dict[str, int]]:
    """Split markdown into citation-bearing blocks. Returns (blocks, {section_key: heading_line})."""
    blocks: list[Block] = []
    headings: dict[str, int] = {}
    lines = draft.splitlines()
    section = ""
    in_fence = in_comment = False
    buf: list[str] = []
    buf_kind: BlockKind | None = None
    buf_line = 0
    item_indent = 0

    def flush() -> None:
        nonlocal buf, buf_kind
        if buf and buf_kind:
            blocks.append(Block(buf_kind, " ".join(s.strip() for s in buf), buf_line, section))
        buf, buf_kind = [], None

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if in_comment or line.strip().startswith("<!--"):
            flush()
            in_comment = "-->" not in line
            continue
        if not line.strip():
            flush()
            continue
        if _RULE_RE.match(line):
            flush()
            continue
        h = _HEADING_RE.match(line)
        if h:
            flush()
            if len(h.group(1)) == 2:
                section = section_key(h.group(2))
                if section:
                    headings.setdefault(section, i)
            continue
        if line.lstrip().startswith("|"):
            flush()
            nxt = lines[i] if i < len(lines) else ""
            if _TABLE_SEP_RE.match(line) or _TABLE_SEP_RE.match(nxt):
                continue  # separator or header row
            blocks.append(Block("row", line.strip(), i, section))
            continue
        m = _ITEM_RE.match(line)
        if m:
            flush()
            buf, buf_kind, buf_line, item_indent = [line[m.end() :]], "item", i, len(m.group(1))
            continue
        if buf_kind == "item" and (len(line) - len(line.lstrip())) > item_indent:
            buf.append(line)
            continue
        if buf_kind != "para":
            flush()
            buf, buf_kind, buf_line = [], "para", i
        buf.append(line.lstrip("> ").rstrip())
    flush()
    return blocks, headings


def needs_citation(block: Block) -> bool:
    t = block.text.strip()
    if block.section in ("gaps", "generated"):  # meta statements; render drops 'generated'
        return False
    if _INTERP_RE.match(t) or _NOT_CHECKED_RE.match(t) or _ABBREV_RE.match(t):
        return False
    plain = CITE_GROUP_RE.sub("", t).strip()
    if _LABEL_RE.match(plain) or (plain.endswith(":") and len(plain) < 120):
        return False
    # A table row of only separators/dashes carries no claim.
    return not (block.kind == "row" and not re.sub(r"[|\s\-—–]", "", plain))


def _unit_problem(value: Any, fmt: str | None, after: str) -> str:
    """The unit the text gives an anchored value, when it contradicts the value: a 0–1
    fraction written as a percentage, a count written as a fold change."""
    if (fmt or "").strip():
        return ""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    if isinstance(value, float) and 0 <= value <= 1 and after.lstrip().startswith("%"):
        return "is a 0–1 fraction followed by '%': write {{key:path|pct}}"
    if isinstance(value, int) and _FOLD_AFTER_RE.match(after):
        return "is a count, not a fold change"
    return ""


def _bare_numbers(text: str, known: list[str], sources: dict[str, Source]) -> list[str]:
    """Hand-typed decimals and percentages, sentence by sentence. A sentence quoting a
    paper, trial or web page may carry its numbers (they cannot be anchored to raw/); a
    sentence citing only database records may not. A sentence without its own citation
    takes the block's."""
    out: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        keys = [k for k in cited_keys(sentence) if k in sources] or known
        if any(sources[k].kind in ("article", "trial", "web") for k in keys):
            continue
        out += _BARE_NUMBER_RE.findall(CITE_GROUP_RE.sub("", ANCHOR_RE.sub("", sentence)))
    return out


class _CitedText:
    """The text a cited source holds, lower-cased: the raw record of a database source, the
    abstract of an article (``pubmed_abstracts``). None when the run does not hold it."""

    def __init__(self, run_dir: Path, sources: dict[str, Source]) -> None:
        self.run_dir, self.sources = run_dir, sources
        self._cache: dict[str, str | None] = {}
        rec = load_raw(run_dir, "pubmed_abstracts")
        papers = rec.data.get("papers") if rec and isinstance(rec.data, dict) else None
        self._abstracts = {
            str(p): str(a.get("abstract") or "")
            for p, a in (papers or {}).items()
            if isinstance(a, dict) and a.get("abstract")
        }
        # Database names are vocabulary of the report itself ("Open Targets", "Orphanet").
        self.vocabulary = " ".join(
            f"{s.db_name} {s.title}" for s in sources.values() if s.kind == "database"
        ).lower()

    def __call__(self, key: str) -> str | None:
        if key not in self._cache:
            s = self.sources[key]
            text: str | None = None
            if s.kind == "database":
                rec = load_raw(self.run_dir, key)
                if rec is not None and rec.data is not None:
                    text = json.dumps(rec.data, ensure_ascii=False)
            elif s.kind == "article" and s.pmid in self._abstracts:
                text = f"{s.title} {s.venue} {self._abstracts[s.pmid]}"
            self._cache[key] = text.lower() if text else None
        return self._cache[key]


def _ungrounded_names(text: str, known: list[str], cited: _CitedText) -> list[str]:
    """Proper names in a sentence that none of its cited sources mention. Sentences citing a
    source whose text the run does not hold (trials, patents, web pages) are not judged."""
    out: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        keys = [k for k in cited_keys(sentence) if k in cited.sources] or known
        texts = [cited(k) for k in keys]
        if not keys or any(t is None for t in texts):
            continue
        body = CITE_GROUP_RE.sub("", ANCHOR_RE.sub("", sentence))
        for m in _NAME_RE.finditer(body):
            before = body[: m.start()].rstrip()
            if not before or before.endswith((".", ":", "*", "|", "(", "—")):
                continue  # sentence- or label-initial capital
            name = m.group(1).lower()
            if name in _COMMON_NAMES or name in cited.vocabulary:
                continue
            if not any(name in t for t in texts if t):
                out.append(m.group(1))
    return list(dict.fromkeys(out))


def _unsourced_verbs(text: str, keys: list[str], cited: _CitedText) -> list[str]:
    """Biochemical verbs in a sentence citing only papers, that none of those abstracts use
    in any form. Negated uses ("not by cutting") and sentences without a held abstract are
    left alone."""
    out: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        cites = [k for k in cited_keys(sentence) if k in cited.sources] or keys
        texts = [cited(k) for k in cites]
        if not cites or not all(k.startswith("pmid:") for k in cites) or not all(texts):
            continue
        body = CITE_GROUP_RE.sub("", sentence)
        joined = " ".join(t for t in texts if t)
        for pattern, stems in _BIOCHEMICAL_VERBS:
            for m in pattern.finditer(body):
                if not _NEGATED_RE.search(body[: m.start()]) and not any(
                    stem in joined for stem in stems
                ):
                    out.append(m.group(0))
    return list(dict.fromkeys(out))


_CONSTRAINT_NEGATES_RE = re.compile(
    r"\b(?:LOEUF|pLI|constraint|constrained)\b[^.;]{0,80}"
    r"\b(?:benign|argues? against|rules? out|not pathogenic|not disease-causing)\b",
    re.IGNORECASE,
)
HI_SUFFICIENT = "3"  # ClinGen's score for sufficient evidence of haploinsufficiency
_TRIALS_TO_NAME = 5  # the largest late-phase trials the prose must reach


def _claim_warnings(b: Block, keys: list[str], cited: _CitedText) -> list[Issue]:
    """Warnings on how a block relates to its sources, sentence by sentence."""
    out: list[Issue] = []
    for sentence in _CLAUSE_SPLIT_RE.split(b.text):
        cites = set(cited_keys(sentence)) or set(keys)
        if (
            cites & _SCREEN_KEYS
            and (m := _SCREEN_CONCLUSION_RE.search(sentence))
            and not _DENIED_CONCLUSION_RE.search(sentence)
        ):
            out.append(
                Issue(
                    "W_SCREEN_INFERENCE",
                    b.line,
                    f"'{m.group(0)}' from a screen: few hits neither support nor argue against a "
                    "role; state the count and draw no conclusion from it",
                )
            )
        if any(k.startswith("pmid:") for k in cites) and (m := _INFERENCE_RE.search(sentence)):
            out.append(
                Issue(
                    "W_INFERENCE_IN_FACT",
                    b.line,
                    f"'{m.group(1)}' adds what follows from the finding: state what was seen, and "
                    "put the inference in *Interpretation:* unless the paper draws it",
                )
            )
        if (
            b.section == "biology"
            and cites
            and cites <= _RECORD_TEXT_KEYS
            and (m := _MECHANISM_RE.search(CITE_GROUP_RE.sub("", sentence)))
        ):
            out.append(
                Issue(
                    "W_RECORD_ONLY_MECHANISM",
                    b.line,
                    f"'{m.group(0)}' rests on a curated record alone: its sentence is a lead, "
                    "so read and cite the paper behind it",
                )
            )
    if b.section in _VERB_SECTIONS:
        verbs = _unsourced_verbs(b.text, keys, cited)
        if verbs:
            out.append(
                Issue(
                    "W_VERB_NOT_IN_SOURCE",
                    b.line,
                    f"not in the cited abstracts: {', '.join(verbs)} — use the verb the source "
                    "uses, since these words have a precise biochemical meaning",
                )
            )
    return out


def _resolve_anchors(text: str, run_dir: Path) -> str:
    def sub(m: re.Match[str]) -> str:
        v = resolve_anchor(run_dir, m.group(1), m.group(2), m.group(3))
        return v if v is not None else m.group(0)

    return ANCHOR_RE.sub(sub, text)


def _guard_inputs(run_dir: Path) -> dict[str, Any]:
    def data(key: str) -> dict[str, Any]:
        rec = load_raw(run_dir, key)
        return rec.data if rec and rec.status == "ok" and isinstance(rec.data, dict) else {}

    gc, kd = data("gnomad_constraint"), data("ot_known_drugs")
    return {
        "constraint": {k: gc.get(k) for k in ("loeuf", "pli", "mis_z", "moeuf")} if gc else None,
        "known_drugs_count": int(kd.get("total_count") or 0),
        "approved_count": sum(1 for d in kd.get("drugs", []) if d.get("is_approved")),
        "indication_approved_drug_count": int(
            data("openfda_indication").get("approved_drug_count") or 0
        ),
        "indication_active_trial_count": int(data("ct_condition").get("active_count") or 0),
    }


def _check_blocks(rep: CheckReport, run_dir: Path, draft: str, disease: str = "") -> None:
    """Block names are known, and the required blocks sit in their sections."""
    section_at: dict[int, str] = {}
    section = ""
    for i, line in enumerate(draft.splitlines(), start=1):
        h = _HEADING_RE.match(line)
        if h and len(h.group(1)) == 2:
            section = section_key(h.group(2)) or section
        section_at[i] = section
    placed: dict[str, set[str]] = {}
    for line, name in find_blocks(draft):
        if name not in BLOCKS:
            rep.errors.append(Issue("E_UNKNOWN_BLOCK", line, f"unknown block '{name}'"))
            continue
        placed.setdefault(name, set()).add(section_at.get(line, ""))
    for name, sec in required(run_dir, disease).items():
        if sec not in placed.get(name, set()):
            rep.errors.append(
                Issue("E_MISSING_TABLE", 0, f"'{{{{{name}}}}}' missing from section '{sec}'")
            )


def _check_functions(rep: CheckReport, run_dir: Path, sources: dict[str, Source]) -> None:
    """Every functional-study entry the writer recorded must be the paper's own finding:
    a registered paper whose abstract names the variant and holds the quote."""
    entries = load_functions(run_dir)
    if entries == []:
        return
    if entries is None:
        rep.errors.append(Issue("E_VARIANT_FUNCTION", 0, "variant_function.json is not valid JSON"))
        return
    series = load_raw(run_dir, "allelic_series")
    rows = ((series.data or {}).get("series") or {}).get("rows") or [] if series else []
    abstracts_rec = load_raw(run_dir, "pubmed_abstracts")
    papers = (abstracts_rec.data or {}).get("papers", {}) if abstracts_rec else {}
    abstracts = {p: str(v.get("abstract") or "") for p, v in papers.items()}
    for problem in function_problems(entries, rows, abstracts, sources):
        rep.errors.append(Issue("E_VARIANT_FUNCTION", 0, problem))


def _abstracts(run_dir: Path) -> dict[str, str]:
    rec = load_raw(run_dir, "pubmed_abstracts")
    papers = (rec.data or {}).get("papers", {}) if rec and isinstance(rec.data, dict) else {}
    return {str(p): str(v.get("abstract") or "") for p, v in papers.items() if v.get("abstract")}


def _audit_items(blocks: list[Block], abstracts: dict[str, str]) -> list[dict[str, Any]]:
    """The blocks a reader can judge against a paper: factual, citing a paper whose abstract
    the run holds. A block citing records beside a paper is judged on the paper's part."""
    out = []
    for b in blocks:
        if not needs_citation(b) or b.section == "gaps":
            continue
        cited = [k.removeprefix("pmid:") for k in cited_keys(b.text) if k.startswith("pmid:")]
        sources = [{"key": f"pmid:{p}", "text": abstracts[p]} for p in cited if p in abstracts]
        if sources:
            out.append(
                {
                    "id": block_id(b.text),
                    "section": b.section,
                    "text": CITE_GROUP_RE.sub("", b.text).strip(),
                    "sources": sources,
                    "also_cites": [k for k in cited_keys(b.text) if not k.startswith("pmid:")],
                }
            )
    return out


def audit_items(run_dir: Path, draft: str) -> list[dict[str, Any]]:
    """The draft's auditable blocks, with the cited abstracts."""
    blocks, _headings = parse_blocks(blank_blocks(draft))
    return _audit_items(blocks, _abstracts(run_dir))


def _check_claims(
    rep: CheckReport, run_dir: Path, blocks: list[Block], manifest: Manifest | None
) -> None:
    """The core claims were searched and every paper-cited block was audited."""
    from profile_writer import HOW_FILE, load_writer_file

    if manifest is None or manifest.template < 3 or manifest.depth == "quick":
        return
    how = load_writer_file(run_dir, HOW_FILE)
    claims = [str(e.get("claim") or "") for e in how or [] if isinstance(e, dict)]
    checks = load_writer_file(run_dir, CHECK_FILE)
    if checks is None or checks == []:
        rep.errors.append(Issue("E_CLAIM_CHECK", 0, f"write a valid {CHECK_FILE}"))
    else:
        cited = {k for b in blocks for k in cited_keys(b.text)}
        rep.errors += [
            Issue("E_CLAIM_CHECK", 0, p)
            for p in check_problems(
                checks, claims, load_searches(run_dir), gene_terms(run_dir), cited
            )
        ]
    items = _audit_items(blocks, _abstracts(run_dir))
    verdicts = load_writer_file(run_dir, AUDIT_FILE)
    if items and (verdicts is None or verdicts == []):
        rep.errors.append(Issue("E_CLAIM_AUDIT", 0, f"run claim_audit.py and write {AUDIT_FILE}"))
    elif items:
        rep.errors += [
            Issue("E_CLAIM_AUDIT", 0, f"{text[:70]!r}: {problem}" if text else problem)
            for text, problem in audit_problems(verdicts, items)
        ]


def _check_therapies(rep: CheckReport, run_dir: Path, blocks: list[Block]) -> None:
    """A therapy selected by the gene must reach the prose, and so must the largest trials: a
    table can hold what the writer never read."""
    from profile_drugs import named
    from profile_tables import named_trial

    text = " ".join(b.text for b in blocks if b.section == "translational")
    data = load_raw(run_dir, "therapy_relevance")
    selected = (data.data or {}).get("biomarker") or [] if data and data.status == "ok" else []
    for r in selected:
        if r.get("approved") and not named(r, text):
            rep.errors.append(
                Issue(
                    "E_THERAPY_NOT_DISCUSSED",
                    0,
                    f"{r['name'].title()} is approved for patients selected by this gene, and "
                    "Translational context does not name it",
                )
            )
    trials = load_raw(run_dir, "ct_outcomes")
    rows = (trials.data or {}).get("trials") or [] if trials and trials.status == "ok" else []
    unread = [
        t.get("acronym") or t.get("nct_id")
        for t in rows[:_TRIALS_TO_NAME]
        if t.get("status") == "COMPLETED" and t.get("papers") and not named_trial(t, text)
    ]
    if unread:
        rep.warnings.append(
            Issue(
                "W_OUTCOME_UNREAD",
                0,
                f"completed trials with result papers not discussed: {', '.join(unread)} — read "
                "the linked papers and say what each showed, or why it does not bear on the gene",
            )
        )


def _check_drugs(rep: CheckReport, run_dir: Path, blocks: list[Block], template: int) -> None:
    """The drug table lists every drug; the approved ones also need what they showed, which
    only the writer can say."""
    if template < 3:
        return
    from profile_drugs import named

    if template >= 4:
        _check_therapies(rep, run_dir, blocks)

    rec = load_raw(run_dir, "drug_landscape")
    drugs = (rec.data or {}).get("drugs") or [] if rec and rec.status == "ok" else []
    text = " ".join(b.text for b in blocks if b.section == "translational")
    missing = [d["name"].title() for d in drugs if d.get("approved") and not named(d, text)]
    if missing:
        rep.warnings.append(
            Issue(
                "W_DRUG_NO_READOUT",
                0,
                f"approved but not discussed in Translational context: {', '.join(missing)} — "
                "give each its biomarker, outcome and safety readout, or say none was found",
            )
        )


def _check_writer_files(
    rep: CheckReport, run_dir: Path, sources: dict[str, Source], template: int, depth: str = ""
) -> list[set[str]]:
    """The writer's JSON files are well formed and cite registered sources. Returns the
    supporting keys of each weak How-we-know claim, for W_STRENGTH_PROSE."""
    from profile_blocks import paper_strengths
    from profile_writer import (
        HOW_FILE,
        MECHANISM_FILE,
        OPEN_FILE,
        claim_cells,
        cross_kind_contradictions,
        how_problems,
        is_weak,
        load_writer_file,
        mechanism_problems,
        open_problems,
    )

    full = template >= 2 and depth != "quick"
    locations = load_raw(run_dir, "uniprot_locations")
    vocabulary = set((locations.data or {}).get("names") or []) if locations else set()
    for code, name, problems in (
        ("E_MECHANISM", MECHANISM_FILE, lambda x: mechanism_problems(x, sources, vocabulary)),
        ("E_OPEN_QUESTION", OPEN_FILE, lambda x: open_problems(x, sources)),
    ):
        entries = load_writer_file(run_dir, name)
        if entries is None:
            rep.errors.append(Issue(code, 0, f"{name} is not valid JSON"))
        elif entries == [] and full:
            rep.errors.append(Issue(code, 0, f"write {name}"))
        elif entries:
            rep.errors += [Issue(code, 0, p) for p in problems(entries)]

    how = load_writer_file(run_dir, HOW_FILE)
    if how is None:
        rep.errors.append(Issue("E_HOW_WE_KNOW", 0, f"{HOW_FILE} is not valid JSON"))
        return []
    if how == [] and template >= 2:
        rep.errors.append(
            Issue("E_HOW_WE_KNOW", 0, f"write {HOW_FILE}: 3–5 core claims with their keys")
        )
        return []
    for problem in how_problems(how, sources) if how else []:
        rep.errors.append(Issue("E_HOW_WE_KNOW", 0, problem))
    if not how or not isinstance(how, list):
        return []
    strengths = paper_strengths(run_dir)
    for e in how:
        if isinstance(e, dict) and (keys := cross_kind_contradictions(e, strengths)):
            rep.warnings.append(
                Issue(
                    "W_CROSS_KIND_CONTRADICTION",
                    0,
                    f"[@{', @'.join(keys)}] against {str(e.get('claim'))[:50]!r} is a different "
                    "kind of evidence from its support: if exposure, duration, population or "
                    "outcome differ, list it under not_comparable with the reason",
                )
            )
    return [
        {str(k).removeprefix("@") for k in e.get("supports") or []}
        for e in how
        if isinstance(e, dict) and is_weak(claim_cells(e, strengths))
    ]


def check(run_dir: Path, draft: str) -> CheckReport:
    rep = CheckReport()
    sources: dict[str, Source] = load_sources(run_dir)
    _check_functions(rep, run_dir, sources)
    manifest: Manifest | None = (
        load_manifest(run_dir) if (run_dir / "manifest.json").exists() else None
    )
    weak_claims = _check_writer_files(
        rep,
        run_dir,
        sources,
        manifest.template if manifest else TEMPLATE_VERSION,
        manifest.depth if manifest else "",
    )
    _check_blocks(rep, run_dir, draft, manifest.disease if manifest else "")
    blocks, headings = parse_blocks(blank_blocks(draft))
    _check_drugs(rep, run_dir, blocks, manifest.template if manifest else TEMPLATE_VERSION)
    _check_claims(rep, run_dir, blocks, manifest)

    # Required sections ------------------------------------------------------
    sections = draft_sections(manifest.template if manifest else TEMPLATE_VERSION)
    required = [k for k, _ in sections if k != "indication" or (manifest and manifest.disease)]
    for key in required:
        if key not in headings:
            rep.errors.append(Issue("E_MISSING_SECTION", 0, f"missing '## {dict(sections)[key]}'"))
    if "generated" in headings:
        rep.warnings.append(
            Issue(
                "W_GENERATED_SECTION",
                headings.pop("generated"),
                "render.py writes References, Appendix and Run Information; drop it from the draft",
            )
        )
        blocks = [b for b in blocks if b.section != "generated"]
    for key, line in headings.items():
        if not any(b.section == key for b in blocks):
            rep.warnings.append(Issue("W_EMPTY_SECTION", line, f"section '{key}' has no content"))

    # Guards (reuse the pipeline's deterministic guard functions) -------------
    guards: dict[str, Any] | None = None
    try:
        from services.evidence.commercial_interpret import apply_commercial_guards
        from services.evidence.constraint_interpret import (
            apply_constraint_guards,
            interpret_constraint,
        )

        gi = _guard_inputs(run_dir)
        reading = (
            interpret_constraint(gene_symbol=manifest.gene if manifest else "", **gi["constraint"])
            if gi["constraint"]
            else None
        )
        guards = {
            "reading": reading,
            "gi": gi,
            "constraint": apply_constraint_guards,
            "commercial": apply_commercial_guards,
        }
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the checker
        rep.warnings.append(Issue("W_GUARDS_UNAVAILABLE", 0, f"guards skipped: {exc}"))

    template = manifest.template if manifest else TEMPLATE_VERSION
    constraint = load_raw(run_dir, "gnomad_constraint")
    loeuf = (
        (constraint.data or {}).get("loeuf") if constraint and constraint.status == "ok" else None
    )
    dosage = load_raw(run_dir, "clingen_dosage")
    hi_established = bool(
        dosage and isinstance(dosage.data, dict)
        and str(dosage.data.get("haploinsufficiency_score")) == HI_SUFFICIENT
    )  # fmt: skip

    cited_text = _CitedText(run_dir, sources)
    fact_blocks = cited_fact_blocks = anchors = bare = 0
    cited_all: set[str] = set()
    for b in blocks:
        keys = cited_keys(b.text)
        cited_all.update(keys)
        for k in keys:
            if k not in sources:
                rep.errors.append(Issue("E_UNKNOWN_KEY", b.line, f"[@{k}] not in sources.json"))
                continue
            s = sources[k]
            if not s.verified:
                rep.errors.append(Issue("E_UNVERIFIED", b.line, f"[@{k}] metadata not verified"))
            if s.tier == 3 and b.section == "snapshot":
                rep.errors.append(Issue("E_TIER3_SNAPSHOT", b.line, f"[@{k}] is Tier 3"))
            if s.kind == "article" and s.grounded is False and b.section != "gaps":
                rep.warnings.append(
                    Issue(
                        "W_OFF_TARGET_CITATION",
                        b.line,
                        f"[@{k}]: PubTator3 finds no mention of the gene in its abstract",
                    )
                )
        known = [k for k in keys if k in sources]
        if known and all(sources[k].tier == 3 for k in known) and b.section != "snapshot":
            rep.warnings.append(Issue("W_TIER3_ONLY", b.line, "only Tier-3 support"))

        for m in ANCHOR_RE.finditer(b.text):
            anchors += 1
            key, path = m.group(1), m.group(2)
            problem = anchor_problem(run_dir, key, path, m.group(3))
            if problem:
                rep.errors.append(
                    Issue("E_ANCHOR", b.line, f"{{{{{key}:{path}}}}} does not resolve: {problem}")
                )
            else:
                unit = _unit_problem(
                    anchor_value(run_dir, key, path), m.group(3), b.text[m.end() :]
                )
                if unit:
                    rep.errors.append(Issue("E_ANCHOR_UNIT", b.line, f"{m.group(0)} {unit}"))
            if key not in keys:
                rep.errors.append(
                    Issue(
                        "E_ANCHOR_NOT_CITED",
                        b.line,
                        f"value from '{key}' used without citing [@{key}]",
                    )
                )

        if needs_citation(b):
            fact_blocks += 1
            if keys:
                cited_fact_blocks += 1
            else:
                rep.errors.append(Issue("E_UNCITED", b.line, f"no citation: {b.text[:90]!r}"))
            rep.warnings += _claim_warnings(b, known, cited_text)
            names = _ungrounded_names(b.text, known, cited_text)
            if names:
                rep.warnings.append(
                    Issue(
                        "W_UNGROUNDED_NAME",
                        b.line,
                        f"not in the cited sources: {', '.join(names)} — cite the record or "
                        "paper that names them, or drop them",
                    )
                )
            for num in _bare_numbers(b.text, known, sources):
                bare += 1
                rep.warnings.append(Issue("W_BARE_NUMBER", b.line, f"hand-typed number {num!r}"))

        if (
            _RECLASSIFY_RE.search(b.text)
            and _CLASSIFICATION_RE.search(b.text)
            and not any(k.startswith("pmid:") for k in keys)
        ):
            rep.warnings.append(
                Issue(
                    "W_RECLASSIFICATION",
                    b.line,
                    "a ClinVar call is called wrong without a paper: name the condition it is "
                    "for, its review status and submitters [@clinvar], and put the conflict in "
                    "Contested; call it wrong only citing a paper or record that reclassifies it",
                )
            )

        for sentence in re.split(r"(?<=[.;])\s+", b.text):
            m = _SAFETY_OVERCLAIM_RE.search(sentence)
            if m and not (
                m.group(0).lower().startswith("no ") and _CURATED_ABSENCE_RE.search(sentence)
            ):
                rep.warnings.append(
                    Issue(
                        "W_OVERCLAIM_SAFETY",
                        b.line,
                        f"'{m.group(0)}': the sources show what was found, not that a target or "
                        'drug is safe — name the source and its coverage ("no curated liability '
                        'in Open Targets"), and keep genetic, model, trial and pharmacovigilance '
                        "evidence apart",
                    )
                )
                break

        for sentence in re.split(r"(?<=[.;])\s+", b.text):
            if _FAERS_RE.search(sentence) and (m := _FAERS_INFERENCE_RE.search(sentence)):
                rep.warnings.append(
                    Issue(
                        "W_FAERS_INFERENCE",
                        b.line,
                        f"'{m.group(0)}' in a FAERS sentence: spontaneous reports have no "
                        "exposure denominator — give counts and shares of reports as signals, "
                        "never as rates, incidence, safety or a ranking of drugs",
                    )
                )
                break

        for sentence in re.split(r"(?<=[.;])\s+", b.text):
            cites = cited_keys(sentence)
            m = _BINDING_RE.search(sentence)
            if (
                m
                and "string" in cites
                and not any(k.startswith("pmid:") for k in cites)
                and not _NO_PARTNERS_RE.search(sentence)
            ):
                rep.warnings.append(
                    Issue(
                        "W_STRING_BINDING",
                        b.line,
                        f"'{m.group(0)}' cited to STRING alone: its partners are functional "
                        "associations (co-expression, text mining, databases) — call them "
                        "associated, or cite the paper or structure that shows the binding",
                    )
                )
                break
        if _ASSOC_TOTAL in b.text and not _ANY_EVIDENCE_RE.search(b.text):
            rep.warnings.append(
                Issue(
                    "W_ASSOCIATION_COUNT",
                    b.line,
                    "the Open Targets total counts diseases with any evidence (score > 0), "
                    "literature co-mention included — say so, and separate genetic, clinical "
                    "and literature-only links",
                )
            )
        for sentence in _CLAUSE_SPLIT_RE.split(b.text):
            papers = {k for k in cited_keys(sentence) if k.startswith("pmid:")}
            m = _ESTABLISHED_RE.search(sentence)
            if m and papers and any(papers <= weak for weak in weak_claims):
                rep.warnings.append(
                    Issue(
                        "W_STRENGTH_PROSE",
                        b.line,
                        f"'{m.group(0)}' for a claim the How-we-know table counts as one group "
                        "or cells only: say who showed it and in what system, or cite the "
                        "independent evidence",
                    )
                )
                break
        if m := _PROVEN_RE.search(b.text):
            rep.warnings.append(
                Issue(
                    "W_PROVEN",
                    b.line,
                    "'proven': name what each modality showed — biomarker effect, outcome "
                    "trial, long-term safety — rather than one verdict for all",
                )
            )
        if m := _ESSENTIALITY_SCOPE_RE.search(b.text):
            rep.warnings.append(
                Issue(
                    "W_ESSENTIALITY_SCOPE",
                    b.line,
                    f"'{m.group(0)}': CRISPR screens of cancer lines test cell-autonomous fitness "
                    "in culture — write 'not a broadly essential cancer-cell fitness gene in "
                    "<screen>', and keep tumour–immune and context-specific effects as separate "
                    "questions",
                )
            )

        if template >= 4:
            if loeuf is not None:
                prose = _resolve_anchors(CITE_GROUP_RE.sub("", b.text), run_dir)
                if problem := constraint_wording_problem(prose, loeuf):
                    rep.errors.append(Issue("E_CONSTRAINT_WORDING", b.line, problem))
            if m := _CONSTRAINT_NEGATES_RE.search(b.text):
                rep.warnings.append(
                    Issue(
                        "W_SAFETY_EXTRAPOLATION",
                        b.line,
                        f"'{m.group(0)[:70]}': a population measure does not negate disease "
                        "evidence; keep selection, pathogenicity and fitness apart",
                    )
                )

        if manifest and manifest.disease and _UNSCOPED_RE.search(b.text):
            rep.errors.append(
                Issue(
                    "E_SCOPE_CONTRADICTION",
                    b.line,
                    f"the run is scoped to '{manifest.disease}'; write the indication section "
                    "from its records ({{table:indication}}), not as an unscoped profile",
                )
            )

        if guards:
            text = _resolve_anchors(CITE_GROUP_RE.sub("", b.text), run_dir)
            if guards["reading"] is not None:
                reading = guards["reading"]
                # A curated dosage score or a paper establishes haploinsufficiency; constraint
                # alone neither shows nor excludes it.
                if template >= 4 and (hi_established or any(k.startswith("pmid:") for k in keys)):
                    reading = reading.model_copy(update={"claims_haploinsufficiency_ok": True})
                out = guards["constraint"](text, reading)
                if out != text:
                    rep.errors.append(Issue("E_GUARD_CONSTRAINT", b.line, out[len(text) :].strip()))
            gi = guards["gi"]
            out = guards["commercial"](
                text,
                known_drugs_count=gi["known_drugs_count"],
                approved_count=gi["approved_count"],
                indication_approved_drug_count=gi["indication_approved_drug_count"],
                indication_active_trial_count=gi["indication_active_trial_count"],
            )
            if out != text:
                rep.errors.append(Issue("E_GUARD_COMMERCIAL", b.line, out[len(text) :].strip()))

    kinds: dict[str, int] = {}
    for k in cited_all & sources.keys():
        kinds[sources[k].kind] = kinds.get(sources[k].kind, 0) + 1
    rep.stats = {
        "fact_blocks": fact_blocks,
        "cited_fact_blocks": cited_fact_blocks,
        "citation_coverage": round(cited_fact_blocks / fact_blocks, 3) if fact_blocks else 1.0,
        "anchored_values": anchors,
        "bare_numbers": bare,
        "sources_cited": len(cited_all & sources.keys()),
        "sources_cited_by_kind": kinds,
        "sources_available": len(sources),
    }
    return rep


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("--draft", default="draft.md")
    a = p.parse_args()
    rep = check(a.run_dir, (a.run_dir / a.draft).read_text())
    (a.run_dir / "check_report.json").write_text(rep.to_json())
    for i in rep.errors:
        print(f"ERROR   L{i.line:<4} {i.code:<20} {i.message}")
    for i in rep.warnings:
        print(f"warning L{i.line:<4} {i.code:<20} {i.message}")
    print(json.dumps(rep.stats))
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
