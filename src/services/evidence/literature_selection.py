# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Rank a lens's literature by paper: scope, study design and impact, then diversity.

Literature claims used to be ranked by their journal's score and the extractor's own
confidence. Journal scores were missing in most saved runs, so the order was extraction
order; and nothing in it knew whether a sentence came from a phase 3 trial, a review or a
docking study, or whether its paper concerned the run's disease at all.

Papers are ranked first, from data the run already has: the paper's publication types,
MeSH headings and abstract (study design per lens, human or animal), its scope against the
run's disease, and its own iCite citation ratio. The order is lexicographic over coarse,
reviewable buckets, not a fitted score:

    evidence class (scope × design; background unless the paper concerns the target)
    → scope → design level → impact bucket → journal score → newer first → PMID

then a greedy pass keeps the view diverse within each evidence class (it never lifts a
weaker class above a stronger one): one paper per study before a second paper of any study, reviews at most a quarter of the papers placed so far, no design family above
half (past its first four papers) while others remain, and — once four in-scope papers
with a direction are placed and they lean one way by their claims' direction tags — the
strongest non-background papers of the other direction until they make a fifth. Within a paper, claims naming the target and the disease, with a quantity, come
first. Everything here is deterministic and costs one pass over the papers.

The index is built once per run (``build_literature_index``) and travels in the lens task
spec; ``rank_literature`` orders one lens's literature claims with it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from core.evidence_text import mentions_disease, normalize_typography
from schemas.evidence import CoreClaim, Direction, Evidence
from services.evidence.disease_scope import DiseaseScope

Scope = Literal["in_scope", "related", "target_level", "none"]
_ANIMAL_MODELS = "disease models, animal"  # the MeSH descriptor, lower case
LensName = Literal["genetics", "biology", "safety", "clinical"]
LENSES: tuple[LensName, ...] = ("genetics", "biology", "safety", "clinical")

# Retrieval roles whose queries ask about the target regardless of the disease.
_TARGET_LEVEL_ROLES = frozenset(
    {"target_genetics", "target_human_genetics", "target_safety", "target_drug_trials"}
)
# Publication types that are not evidence about the question.
_EXCLUDED_TYPES = frozenset(
    {
        "published erratum",
        "comment",
        "editorial",
        "letter",
        "news",
        "retraction of publication",
        "expression of concern",
    }
)
_REVIEW_SHARE = 0.25
_FAMILY_SHARE = 0.5
# A family's first papers are placed on merit; the share applies past this many.
_FAMILY_FREE = 4
_COUNTER_SHARE = 0.2
# The counter-evidence floor applies once this many directional in-scope papers are placed.
_COUNTER_AFTER = 4
_TITLE_JACCARD = 0.5

_SCOPE_RANK: dict[str, int] = {"in_scope": 0, "related": 1, "target_level": 2, "none": 3}


def _cue(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


_MR = _cue(r"mendelian randomi[sz]ation")
_BURDEN = _cue(
    r"\bexome",
    r"\bburden\b",
    r"\brare[- ]variant",
    r"\bprotein[- ]truncating",
    r"\bloss[- ]of[- ]function variant",
    r"\bpredicted loss[- ]of[- ]function",
)
_CARRIER = _cue(r"\bcarriers?\b", r"\bheterozygo", r"\bhomozygo")
# What makes carriers a loss- or gain-of-function study rather than a common-variant one.
_FUNCTION_CHANGE = _cue(
    r"\bloss[- ]of[- ]function",
    r"\bgain[- ]of[- ]function",
    r"\btruncat",
    r"\bnull allele",
    r"\bnonsense\b",
    r"\bframeshift",
    r"\bsplice",
    r"\bdeficien",
    r"\brare (?:coding )?variant",
    r"\bprotective variant",
    r"\bknockout\b",
)
_PROTOCOL = _cue(
    r"\brationale and design\b",
    r"\bdesign and rationale\b",
    r"\bstudy protocol\b",
    r"\bprotocol for an?\b",
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_MENDELIAN = _cue(
    r"\bsegregat",
    r"\bpedigree",
    r"\bproband",
    r"\bgermline (?:mutation|variant)",
    r"\bpathogenic variant",
    r"\bfamil(?:y|ies) with\b",
    r"\bautosomal (?:dominant|recessive)",
)
_GWAS = _cue(
    r"genome[- ]wide association",
    r"\bgwas\b",
    r"\bfine[- ]mapping",
    r"\bcolocali[sz]",
    r"\bpolygenic",
    r"\bgenetic correlation",
)
_CANDIDATE = _cue(r"\bpolymorphism", r"\bsnps?\b", r"\brs\d{3,}\b", r"\bgenotyp", r"\balleles?\b")
_RCT = _cue(r"\brandomi[sz]ed\b", r"\bplacebo[- ]controlled\b")
_PHASE3 = _cue(r"\bphase (?:3|iii)\b")
_PHASE2 = _cue(r"\bphase (?:2|ii)\b")
_META = _cue(r"\bmeta[- ]analys")
_COHORT = _cue(r"\bcohort\b", r"\bregistry\b", r"\breal[- ]world\b", r"\bretrospective\b")
_HARM = _cue(
    r"\badverse (?:event|effect|reaction|outcome)s?\b",
    r"\bside effects?\b",
    r"\btoxicit",
    r"\bpharmacovigilance\b",
    r"\bfaers\b",
    r"\bsafety\b",
    r"\bmortality\b",
)
_PERTURB = _cue(
    r"\bknock(?:-| )?(?:out|down|in)\b",
    r"\bknockout\b",
    r"\bknockdown\b",
    r"-/-",
    r"\bdeficien(?:t|cy)\b",
    r"\bnull (?:mice|mutant|allele)",
    r"\bsilenc",
    r"\bsirna\b",
    r"\bshrna\b",
    r"\bcrispr",
    r"\binhibit(?:or|ion|ed)\b",
    r"\bblockade\b",
    r"\bneutrali[sz]",
    r"\bantagonis",
    r"\bagonis",
    r"\boverexpress",
    r"\btransgenic\b",
)
_ANIMAL = _cue(
    r"\bmice\b",
    r"\bmouse\b",
    r"\bmurine\b",
    r"\brats?\b",
    r"\bzebrafish\b",
    r"\bin vivo\b",
    r"\bnon-?human primates?\b",
    r"\bporcine\b",
    r"\bcanine\b",
)
_HUMAN_TISSUE = _cue(r"\bbiops", r"\bpatients?'? (?:tissue|samples?|specimens?)", r"\bex vivo\b")
_ASSOCIATION = _cue(
    r"\bexpress",
    r"\bup-?regulat",
    r"\bdown-?regulat",
    r"\belevated\b",
    r"\bassociated with\b",
    r"\bcorrelat",
    r"\blevels?\b",
)
_IN_SILICO = _cue(
    r"\bin silico\b",
    r"\bmolecular docking\b",
    r"\bbioinformatic",
    r"\bmachine learning\b",
    r"\bnetwork pharmacology\b",
    r"\bmolecular dynamics\b",
    r"\bvirtual screening\b",
)
_QUANTITY = _cue(
    r"\b(?:hr|or|rr|ci)\b",
    r"\bp\s*[<=>≤]",
    r"\bn\s*=\s*\d",
    r"hazard ratio",
    r"odds ratio",
    r"relative risk",
    r"\d+(?:\.\d+)?\s*%",
)
_NCT = re.compile(r"\bNCT\d{8}\b")
_ACRONYM_STUDY = re.compile(r"\b([A-Z][A-Z0-9-]{2,14})\s+(?:randomi[sz]ed\s+)?(?:trial|study)\b")
_ACRONYM_STOP = frozenset(
    {"GWAS", "RCT", "DNA", "RNA", "SNP", "PCR", "HIV", "USA", "MRI", "CT", "II", "III"}
)
_WORD = re.compile(r"[a-z0-9]+")

_MESH_ANIMAL = frozenset(
    {"disease models, animal", "mice, knockout", "mice, transgenic", "mice", "rats", "zebrafish"}
)
_MESH_PERTURB = frozenset(
    {
        "gene knockdown techniques",
        "gene knockout techniques",
        "mice, knockout",
        "rna interference",
        "rna, small interfering",
        "crispr-cas systems",
    }
)
_QUAL_PERTURB = frozenset({"antagonists & inhibitors", "deficiency"})
_MESH_HARM = frozenset(
    {
        "drug-related side effects and adverse reactions",
        "pharmacovigilance",
        "adverse drug reaction reporting systems",
    }
)
_QUAL_HARM = frozenset({"adverse effects", "toxicity", "chemically induced", "poisoning"})
_MESH_COHORT = frozenset(
    {
        "cohort studies",
        "prospective studies",
        "retrospective studies",
        "case-control studies",
        "longitudinal studies",
        "cross-sectional studies",
    }
)
_MESH_SILICO = frozenset(
    {
        "computational biology",
        "molecular docking simulation",
        "computer simulation",
        "machine learning",
    }
)


class Design(BaseModel):
    level: int = 0  # 3 strongest … 0 weakest for this lens
    family: str = "other"


class PaperRecord(BaseModel):
    pmid: str = ""
    year: int | None = None
    scope: Scope = "none"
    excluded: str = ""  # why the paper is never shown, or ""
    review: bool = False
    human: bool | None = None
    rcr: float | None = None
    journal_score: float | None = None
    cluster: str = ""
    design: dict[str, Design] = {}
    # The paper concerns the target: named in the title, or in two or more abstract
    # sentences (a passing mention, such as a cytokine measured as a biomarker, is not).
    about_target: bool = False
    # The paper names one of the target's drugs or a target-directed intervention.
    target_intervention: bool = False


class LiteratureIndex(BaseModel):
    papers: dict[str, PaperRecord] = {}  # by evidence id of the paper row
    target_terms: list[str] = []  # gene symbol and aliases (matched in exact case)
    drug_terms: list[str] = []  # the target's known drugs (any case)
    disease_terms: list[str] = []  # the disease's names and exact synonyms


# ── building the index ──────────────────────────────────────────────────────────


def _lower_set(values: Iterable[Any]) -> set[str]:
    return {" ".join(str(v).lower().split()) for v in values if v}


def _paper_text(ev: Evidence) -> str:
    extra = ev.extra or {}
    return normalize_typography(f"{extra.get('title') or ''} {extra.get('abstract') or ''}")


def _sentences_naming(abstract: str, names: Callable[[str], bool]) -> int:
    return sum(1 for sentence in _SENTENCE.split(abstract) if sentence and names(sentence))


def _scope(
    ev: Evidence,
    title: str,
    abstract: str,
    mesh: set[str],
    mesh_major: set[str],
    *,
    disease_terms: Sequence[str],
    parent_terms: Sequence[str],
    mesh_descriptor: str,
    mesh_is_disease: bool,
    scope: DiseaseScope | None = None,
    model_terms: Sequence[str] = (),
) -> Scope:
    """Whether the paper is about the run's disease (named by the run's DiseaseScope when
    given: spellings, subtypes, abbreviations the abstract defines; else by its names).

    In scope: the title names the disease, or MeSH has the disease's own descriptor as a
    major topic. MeSH is the indexers' judgement of what a paper is about, so for an
    indexed paper a minor heading or the abstract alone makes it at most related (a trial
    in another disease that reports cases of this one as adverse events), unless the
    disease's broader descriptor is a major topic and two or more abstract sentences name
    the disease. A paper not yet indexed is in
    scope when two or more abstract sentences name the disease. A parent disease's name
    makes a paper related, and so does a study indexed under an animal model and one of
    the phenotypes the disease's models are indexed under (``model_terms``).
    """

    source = f"{title} {abstract}"

    def names_disease(text: str) -> bool:
        if scope is not None:
            return scope.match(text, source) == "in_scope"
        return any(mentions_disease(text, t) for t in disease_terms)

    descriptor = mesh_descriptor.lower()
    has_descriptor = bool(descriptor) and descriptor in mesh
    major = bool(descriptor) and descriptor in mesh_major
    if names_disease(title) or (major and mesh_is_disease):
        return "in_scope"
    mentions = _sentences_naming(abstract, names_disease)
    if mesh and descriptor:  # indexed, and the disease has a descriptor to look for
        if major and mentions >= 2:
            return "in_scope"
        if has_descriptor or mentions:
            return "related"
    elif mentions >= 2:
        return "in_scope"
    elif mentions == 1:
        return "related"
    if scope is not None:
        if scope.match(source, source) == "related":
            return "related"
    elif any(mentions_disease(source, t) for t in parent_terms):
        return "related"
    if _ANIMAL_MODELS in mesh and mesh & {t.lower() for t in model_terms}:
        return "related"
    if str((ev.extra or {}).get("retrieval_role") or "") in _TARGET_LEVEL_ROLES:
        return "target_level"
    return "none"


def _symbol_forms(symbol: str) -> list[str]:
    def joined(sym: str) -> str:
        parts = re.split(r"(?<=[A-Za-z])(?=\d)", sym, maxsplit=1)
        return r"[- ]?".join(re.escape(part) for part in parts)

    forms = [joined(symbol)]
    if any(ch.isdigit() for ch in symbol) or len(symbol) >= 5:
        mouse = symbol[:1].upper() + symbol[1:].lower()
        if mouse != symbol:
            forms.append(joined(mouse))
    return forms


def symbol_pattern(symbols: Sequence[str]) -> re.Pattern[str] | None:
    """Gene symbols as papers write them, in exact case: "IL17A" also as "IL-17A" or
    "IL 17A" (a separator where letters meet digits), and the mouse form ("Il17a") for
    symbols unlikely to be an English word (with a digit, or five or more characters).
    Exact case keeps a symbol that is also a common word from matching prose."""
    forms = [f for sym in symbols if sym for f in _symbol_forms(sym)]
    if not forms:
        return None
    return re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(forms)})(?![A-Za-z0-9])")


_INTERVENTION_WORDS = (
    r"(?:inhibit\w*|antagonis\w*|block\w*|antibod\w*|neutrali[sz]\w*|agonis\w*|activat\w*"
    r"|degrad\w*|silenc\w*|antisense|sirna|therap\w*|treatment|targeting)"
)


def _intervention_pattern(symbols: Sequence[str]) -> re.Pattern[str] | None:
    """ "anti-<target>", "<target>-targeting", "<target> inhibitor/antagonist/antibody"."""
    pattern = symbol_pattern(symbols)
    if pattern is None:
        return None
    target = pattern.pattern
    return re.compile(rf"\banti-?\s?{target}|{target}[- ](?:\w+[- ])?{_INTERVENTION_WORDS}")


def drug_pattern(drugs: Sequence[str]) -> re.Pattern[str] | None:
    names = [re.escape(d.lower()) for d in drugs if d]
    if not names:
        return None
    return re.compile(rf"(?<![a-z0-9])(?:{'|'.join(names)})(?![a-z0-9])", re.IGNORECASE)


def _about_target(
    title: str,
    abstract: str,
    target: re.Pattern[str] | None,
    drugs: re.Pattern[str] | None,
    intervention: re.Pattern[str] | None,
) -> tuple[bool, bool]:
    """(about the target, names a target-directed intervention)."""

    def names(text: str) -> bool:
        return bool((target and target.search(text)) or (drugs and drugs.search(text)))

    about = names(title) or _sentences_naming(abstract, names) >= 2
    text = f"{title} {abstract}"
    acts = bool((drugs and drugs.search(text)) or (intervention and intervention.search(text)))
    return about, acts


def _human(text: str, mesh: set[str], metrics: Mapping[str, Any]) -> bool | None:
    human, animal = metrics.get("human"), metrics.get("animal")
    if isinstance(human, (int, float)) and isinstance(animal, (int, float)):
        return bool(human >= 0.5 or human >= animal)
    if "humans" in mesh:
        return True
    if mesh & _MESH_ANIMAL or "animals" in mesh:
        return False
    if _ANIMAL.search(text):
        return False
    return None


def _designs(
    text: str, types: set[str], mesh: set[str], quals: set[str], human: bool | None, review: bool
) -> dict[str, Design]:
    meta = "meta-analysis" in types or bool(_META.search(text))
    rct = "randomized controlled trial" in types or bool(_RCT.search(text))
    phase3 = "clinical trial, phase iii" in types or bool(_PHASE3.search(text))
    phase2 = "clinical trial, phase ii" in types or bool(_PHASE2.search(text))
    trial = (
        any(t.startswith("clinical trial") for t in types) or "controlled clinical trial" in types
    )
    case_report = "case reports" in types
    cohort = (
        "observational study" in types or bool(mesh & _MESH_COHORT) or bool(_COHORT.search(text))
    )
    animal = bool(mesh & _MESH_ANIMAL) or bool(_ANIMAL.search(text))
    perturb = (
        bool(mesh & _MESH_PERTURB) or bool(quals & _QUAL_PERTURB) or bool(_PERTURB.search(text))
    )
    harm = bool(mesh & _MESH_HARM) or bool(quals & _QUAL_HARM) or bool(_HARM.search(text))
    silico = bool(mesh & _MESH_SILICO) or bool(_IN_SILICO.search(text))
    not_animal = human is not False
    mr = "mendelian randomization analysis" in mesh or bool(_MR.search(text))
    burden = bool(_BURDEN.search(text))
    function_change = bool(_FUNCTION_CHANGE.search(text)) or bool(
        mesh & {"loss of function mutation", "gain of function mutation"}
    )
    carrier = ("heterozygote" in mesh or bool(_CARRIER.search(text))) and function_change
    protocol = "clinical trial protocol" in types or bool(_PROTOCOL.search(text))
    mendelian = "pedigree" in mesh or bool(_MENDELIAN.search(text))
    gwas = "genome-wide association study" in mesh or bool(_GWAS.search(text))
    candidate = (
        "polymorphism, single nucleotide" in mesh
        or "genetic predisposition to disease" in mesh
        or bool(_CANDIDATE.search(text))
    )

    def first(*options: tuple[bool, int, str]) -> Design:
        for ok, level, family in options:
            if ok:
                return Design(level=level, family=family)
        return Design(level=0, family="other")

    return {
        "genetics": first(
            (review, 1, "review"),
            (not_animal and mr, 3, "mendelian_randomisation"),
            (not_animal and mendelian, 3, "mendelian"),
            (not_animal and (burden or carrier), 3, "carrier_or_burden"),
            (not_animal and gwas, 2, "gwas"),
            (not_animal and meta and candidate, 2, "association_meta_analysis"),
            (not_animal and candidate, 1, "candidate_gene"),
        ),
        "clinical": first(
            (protocol, 1, "protocol"),
            (meta and (rct or trial), 3, "meta_analysis"),
            (rct and phase3, 3, "phase3_rct"),
            (rct or (phase2 and trial), 2, "rct"),
            (trial or (cohort and not_animal), 1, "non_randomised"),
            (case_report, 0, "case_report"),
            (review, 0, "review"),
        ),
        "safety": first(
            (review, 0, "review"),
            (harm and (rct or meta or "pharmacovigilance" in mesh), 3, "human_harm"),
            (not_animal and (mr or carrier or burden), 3, "genetic_proxy"),
            (harm and cohort and not_animal, 2, "cohort"),
            (harm and not_animal and not case_report and not animal, 2, "cohort"),
            (animal and (perturb or harm), 1, "animal"),
            (case_report, 0, "case_report"),
        ),
        "biology": first(
            (review, 0, "review"),
            (silico and not perturb, 0, "in_silico"),
            (perturb and animal, 3, "perturbation_in_model"),
            (perturb, 2, "perturbation"),
            (not_animal and bool(_HUMAN_TISSUE.search(text)), 2, "human_tissue"),
            (bool(_ASSOCIATION.search(text)), 1, "association"),
        ),
    }


def _cluster_keys(text: str, title: str) -> list[str]:
    keys = [f"nct:{m}" for m in _NCT.findall(text)]
    keys += [
        f"study:{m}"
        for m in _ACRONYM_STUDY.findall(normalize_typography(title))
        if m not in _ACRONYM_STOP and not m.isdigit()
    ]
    return keys


def _title_words(title: str) -> frozenset[str]:
    """Word bigrams of a title (single words for a one-word title)."""
    words = _WORD.findall(normalize_typography(title).lower())
    if len(words) < 2:
        return frozenset(words)
    return frozenset(f"{a} {b}" for a, b in zip(words, words[1:], strict=False))


def _clusters(rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Study clusters: same NCT id, same named study, or near-identical titles."""
    parent: dict[str, str] = {eid: eid for eid, _, _ in rows}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    owner: dict[str, str] = {}
    shingles: list[tuple[str, frozenset[str]]] = []
    for eid, text, title in rows:
        for key in _cluster_keys(text, title):
            if key in owner:
                union(eid, owner[key])
            else:
                owner[key] = eid
        words = _title_words(title)
        if words:
            for other, other_words in shingles:
                inter = len(words & other_words)
                if inter and inter / len(words | other_words) >= _TITLE_JACCARD:
                    union(eid, other)
            shingles.append((eid, words))
    return {eid: find(eid) for eid in parent}


def _year(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text[:4]) if text[:4].isdigit() else None


def build_literature_index(
    papers: Iterable[Evidence],
    quality_map: Mapping[str, Mapping[str, Any]],
    *,
    disease_terms: Sequence[str],
    parent_terms: Sequence[str] = (),
    mesh_descriptor: str = "",
    mesh_is_disease: bool = False,
    target_terms: Sequence[str] = (),
    drug_terms: Sequence[str] = (),
    scope: DiseaseScope | None = None,
    model_terms: Sequence[str] = (),
) -> LiteratureIndex:
    """One record per literature row, keyed by its evidence id (what claims point to)."""
    records: dict[str, PaperRecord] = {}
    cluster_rows: list[tuple[str, str, str]] = []
    target = symbol_pattern(target_terms)
    drugs = drug_pattern(drug_terms)
    intervention = _intervention_pattern(target_terms)
    for ev in papers:
        eid = str(ev.evidence_id)
        extra = ev.extra or {}
        quality = quality_map.get(eid) or {}
        metrics = quality.get("article_metrics") or {}
        text = _paper_text(ev)
        title = normalize_typography(str(extra.get("title") or ""))
        abstract = normalize_typography(str(extra.get("abstract") or ""))
        lowered = text.lower()
        types = _lower_set(extra.get("publication_types") or [])
        headings = [h for h in extra.get("mesh_headings") or [] if isinstance(h, Mapping)]
        mesh = _lower_set(h.get("descriptor") for h in headings)
        mesh_major = _lower_set(h.get("descriptor") for h in headings if h.get("major"))
        quals = _lower_set(q for h in headings for q in h.get("qualifiers") or [])
        review = (
            bool(types & {"review", "systematic review"}) and "meta-analysis" not in types
        ) or (not types and metrics.get("is_research_article") is False)
        excluded = ""
        if extra.get("retracted") or "retracted publication" in types:
            excluded = "retracted"
        elif types & _EXCLUDED_TYPES:
            excluded = sorted(types & _EXCLUDED_TYPES)[0]
        elif quality.get("predatory_flag") is True:
            excluded = "predatory journal"
        human = _human(lowered, mesh, metrics)
        about, acts = _about_target(title, abstract, target, drugs, intervention)
        year = extra.get("pub_year")
        rcr = metrics.get("rcr")
        score = quality.get("sjr_score")
        records[eid] = PaperRecord(
            pmid=str(extra.get("pmid") or ev.source.removeprefix("PMID:")),
            year=_year(year),
            scope=_scope(
                ev,
                title,
                abstract,
                mesh,
                mesh_major,
                disease_terms=disease_terms,
                parent_terms=parent_terms,
                mesh_descriptor=mesh_descriptor,
                mesh_is_disease=mesh_is_disease,
                scope=scope,
                model_terms=model_terms,
            ),
            excluded=excluded,
            review=review,
            human=human,
            rcr=float(rcr) if isinstance(rcr, (int, float)) else None,
            journal_score=float(score) if isinstance(score, (int, float)) else None,
            design=_designs(lowered, types, mesh, quals, human, review),
            about_target=about,
            target_intervention=acts,
        )
        cluster_rows.append((eid, text, str(extra.get("title") or "")))
    for eid, cluster in _clusters(cluster_rows).items():
        records[eid].cluster = cluster
    return LiteratureIndex(
        papers=records,
        target_terms=[t for t in dict.fromkeys(target_terms) if t],
        drug_terms=[t for t in dict.fromkeys(drug_terms) if t],
        disease_terms=[t for t in dict.fromkeys(disease_terms) if t],
    )


# ── ranking ─────────────────────────────────────────────────────────────────────


def evidence_class(scope: str, level: int, lens: str) -> int:
    """0 strong, 1 moderate, 2 weak, 3 background, from scope and design alone.

    Safety ignores the disease: harm from the target's modulation in any population is
    evidence about its safety.
    """
    if lens == "safety":
        return {3: 0, 2: 1, 1: 2}.get(level, 3)
    if scope == "in_scope":
        return {3: 0, 2: 1, 1: 2}.get(level, 3)
    if scope == "related":
        return {3: 1, 2: 2}.get(level, 3)
    if scope == "target_level":
        return {3: 2, 2: 2}.get(level, 3)
    return 3


def impact_bucket(rcr: float | None) -> int:
    """2 high, 1 typical (or unknown: a paper too new for a ratio is not penalised), 0 low."""
    if rcr is None:
        return 1
    if rcr >= 3:
        return 2
    return 0 if rcr < 0.5 else 1


def paper_class(record: PaperRecord, lens: str) -> int:
    """The paper's evidence class for a lens. A paper that does not concern the target —
    for clinical, one that names none of its drugs or a target-directed intervention —
    is background whatever its design: a trial of something else that measured the
    target as a biomarker says nothing about modulating it."""
    relevant = record.target_intervention if lens == "clinical" else record.about_target
    if not relevant:
        return 3
    design = record.design.get(lens) or Design()
    return evidence_class(record.scope, design.level, lens)


def _paper_key(record: PaperRecord, lens: str) -> tuple[Any, ...]:
    design = record.design.get(lens) or Design()
    return (
        paper_class(record, lens),
        _SCOPE_RANK.get(record.scope, 3),
        -design.level,
        -impact_bucket(record.rcr),
        -(record.journal_score if record.journal_score is not None else 0.5),
        -(record.year or 0),
        record.pmid,
    )


def _names_any_case(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", lowered) for t in terms if t
    )


def _claim_key(claim: CoreClaim, index: LiteratureIndex) -> tuple[Any, ...]:
    text = normalize_typography(claim.claim_text or "")
    target = symbol_pattern(index.target_terms)
    names_target = bool(target and target.search(text)) or _names_any_case(text, index.drug_terms)
    names_disease = any(mentions_disease(text, t) for t in index.disease_terms)
    has_quantity = bool(_QUANTITY.search(text))
    has_direction = claim.direction in (Direction.INHIBIT, Direction.ACTIVATE)
    return (
        -(2 * names_target + names_disease + has_quantity + has_direction),
        -(claim.confidence or 0.0),
        claim.claim_text,
    )


def _direction_of(claims: Sequence[CoreClaim]) -> str | None:
    votes = [c.direction for c in claims if c.direction in (Direction.INHIBIT, Direction.ACTIVATE)]
    if not votes:
        return None
    inhibit = sum(v == Direction.INHIBIT for v in votes)
    activate = len(votes) - inhibit
    if inhibit == activate:
        return None
    return "inhibit" if inhibit > activate else "activate"


def _order_papers(
    ids: list[str],
    records: Mapping[str, PaperRecord],
    lens: str,
    directions: Mapping[str, str | None],
) -> list[str]:
    """Greedy order: each step works inside the best evidence class still remaining, so the
    diversity rules reorder papers within a class and never lift a weaker class above a
    stronger one. Only the counter-evidence floor may reach into another (non-background)
    class."""
    keys = {i: _paper_key(records[i], lens) for i in ids}
    remaining = sorted(ids, key=lambda i: keys[i])
    placed: list[str] = []
    clusters: set[str] = set()
    reviews = 0
    families: dict[str, int] = {}
    leaning = {"inhibit": 0, "activate": 0}

    def family(i: str) -> str:
        return (records[i].design.get(lens) or Design()).family

    def allowed(i: str, n: int) -> bool:
        if records[i].review and reviews + 1 > max(1, int(_REVIEW_SHARE * (n + 1))):
            return False
        return families.get(family(i), 0) + 1 <= max(_FAMILY_FREE, int(_FAMILY_SHARE * (n + 1)))

    while remaining:
        n = len(placed)
        pick: str | None = None
        lean = max(leaning, key=lambda d: leaning[d])
        minority = "activate" if lean == "inhibit" else "inhibit"
        directional = leaning["inhibit"] + leaning["activate"]
        if (
            directional >= _COUNTER_AFTER
            and leaning[lean] > leaning[minority]
            and leaning[minority] < _COUNTER_SHARE * (directional + 1)
        ):
            pick = next(
                (
                    i
                    for i in remaining
                    if directions.get(i) == minority
                    and records[i].scope == "in_scope"
                    and keys[i][0] < 3
                    and records[i].cluster not in clusters
                ),
                None,
            )
        if pick is None:
            best = keys[remaining[0]][0]
            tier = [i for i in remaining if keys[i][0] == best]
            fresh = [i for i in tier if records[i].cluster not in clusters]
            pool = fresh or tier
            pick = next((i for i in pool if allowed(i, n)), None)
            if pick is None:  # the caps conflict: a primary paper before a review
                pick = next((i for i in pool if not records[i].review), pool[0])
        remaining.remove(pick)
        placed.append(pick)
        clusters.add(records[pick].cluster)
        reviews += records[pick].review
        families[family(pick)] = families.get(family(pick), 0) + 1
        direction = directions.get(pick)
        if direction and records[pick].scope == "in_scope":
            leaning[direction] += 1
    return placed


def rank_literature(
    claims: Sequence[CoreClaim], index: LiteratureIndex | None, lens: str
) -> list[CoreClaim]:
    """``claims`` ordered paper by paper (best paper first), claims best first within a
    paper; claims of excluded papers are dropped. Without an index, claims keep a
    deterministic order by confidence and text."""
    if index is None:
        return sorted(claims, key=lambda c: (-(c.confidence or 0.0), c.claim_text))
    by_paper: dict[str, list[CoreClaim]] = {}
    for c in claims:
        by_paper.setdefault(str(c.source_evidence_id or c.evidence_id), []).append(c)
    records = {pid: index.papers.get(pid) or PaperRecord(cluster=pid) for pid in by_paper}
    for pid, rec in records.items():
        if not rec.cluster:
            rec.cluster = pid
    kept = [pid for pid in by_paper if not records[pid].excluded]
    directions = {pid: _direction_of(by_paper[pid]) for pid in kept}
    order = _order_papers(kept, records, lens, directions)
    return [c for pid in order for c in sorted(by_paper[pid], key=lambda c: _claim_key(c, index))]
