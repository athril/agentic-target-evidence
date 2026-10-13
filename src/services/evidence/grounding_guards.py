# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Guards that tie lens prose to the target's own structured data.

Two failures seen in real runs, both of a small model importing content that is not
in this gene's data:

* phenotype grounding — the PCSK9 safety lens wrote that knockout mice show
  "increased vasoconstriction" (the TRPC6 phenotype used as a prompt example); no
  PCSK9 mouse phenotype mentions vascular tone.
* tissue claims — the TRPC6 safety lens wrote "high specificity to the kidney" when
  Kidney_Cortex ranks 29/54 in GTEx and lung is the top tissue.

Both checks are vocabulary-based and conservative: they only judge phenotype terms and
tissue words they recognise, and annotate rather than rewrite.
"""

from __future__ import annotations

import re

from core.evidence_text import disease_name_variants
from services.evidence.guard_core import (
    Violation,
    is_negated,
    split_sentences,
    strip_annotations,
)
from services.evidence.organ_systems import group_by_system, prose_names_system

PHENOTYPE_MARKER = "PHENOTYPE GROUNDING GUARD"
TISSUE_MARKER = "TISSUE CLAIM GUARD"

# A sentence talks about animal-model findings.
_MOUSE_CONTEXT = re.compile(
    r"\b(?:mouse|mice|murine|knock[- ]?out|KO|null (?:mice|allele)|-/-|deficient|"
    r"MGI|IMPC|animal model)\b",
    re.IGNORECASE,
)

# (phenotype named in prose, label stems that support it). Terms not listed here are
# not checked — the guard never judges wording it does not understand.
_PHENOTYPE_VOCAB: tuple[tuple[str, str, str], ...] = (
    ("vasoconstriction", r"vasoconstrict", r"vasoconstrict|vascular|vasodilat"),
    (
        "blood pressure",
        r"blood pressure|hypertensi|hypotensi",
        r"blood pressure|hypertens|hypotens",
    ),
    ("lethality", r"\blethal\w*|embryonic death|perinatal death", r"lethal|death|surviv|mortal"),
    (
        "fertility",
        r"\binfertil\w*|\bfertility\b|\bsteril\w*",
        r"fertil|steril|reproduct|sperm|testis|ovar|litter",
    ),
    ("seizures", r"seizure|epilep|convuls", r"seizure|epilep|convuls"),
    ("glucose / insulin", r"hypoglyc|hyperglyc|glucose|insulin", r"glucose|insulin|glyc"),
    (
        "body weight / obesity",
        r"\bobes\w*|body weight|weight gain|adiposity",
        r"weight|obes|adipos|fat\b|body mass",
    ),
    (
        "cardiac",
        r"cardiac hypertroph|cardiomyopath|heart failure|arrhythm|enlarged heart|cardiac function",
        r"heart|cardi",
    ),
    ("bone", r"\bbone\b|osteopor|osteopen|skeletal abnormal", r"bone|osteo|skelet"),
    (
        "immune",
        r"immun\w*|lymphocyt|t[- ]cells?\b|b[- ]cells?\b|neutrophil|infection",
        r"immun|lymph|t cell|b cell|t-helper|neutrophil|leukocyte|thymus|spleen|infect|cd4|cd8",
    ),
    ("liver", r"\bliver\b|hepat\w*", r"liver|hepat|bile"),
    (
        "lipids",
        r"cholesterol|lipid|triglycerid|\bldl\b|\bhdl\b",
        r"cholesterol|lipid|triglycerid|ldl|hdl|lipoprotein",
    ),
    (
        "kidney",
        r"\bkidney|renal\b|proteinuri|albuminuri|glomerul",
        r"kidney|renal|proteinuri|albuminuri|glomerul|nephr",
    ),
    ("hearing", r"hearing|deafness", r"hearing|deaf|\bear\b|auditory"),
    ("vision", r"\bvision\b|retina\w*|blindness", r"retin|eye|vision|lens"),
    ("nociception", r"nocicept|pain sensitiv", r"nocicept|pain|sensory|thermal"),
    (
        "behaviour",
        r"behavio\w*|anxiety|locomot|cognit\w*|learning|memory",
        r"behavio|anxiety|locomot|explor|cognit|learning|memory|vocaliz|activity",
    ),
    ("tumours", r"tumou?r|cancer|neoplas", r"tumou?r|neoplas|cancer|carcinoma"),
)


def check_phenotype_grounding(text: str, mouse_phenotypes: list[str]) -> list[Violation]:
    """Animal-model phenotypes named in prose that the gene's mouse data do not contain."""
    labels = " | ".join(p.lower() for p in mouse_phenotypes)
    out: list[Violation] = []
    seen: set[str] = set()
    for sentence in split_sentences(strip_annotations(text)):
        if not _MOUSE_CONTEXT.search(sentence):
            continue
        for name, claim_re, label_re in _PHENOTYPE_VOCAB:
            m = re.search(claim_re, sentence, re.IGNORECASE)
            if not m or name in seen or is_negated(sentence, m.start(), lookback=40):
                continue
            if labels and re.search(label_re, labels):
                continue
            seen.add(name)
            data = (
                f"its mouse phenotypes are: {', '.join(mouse_phenotypes[:8])}"
                + (" …" if len(mouse_phenotypes) > 8 else "")
                if mouse_phenotypes
                else "no mouse phenotype was retrieved for this gene"
            )
            out.append(
                Violation(
                    rule_id="grounding.mouse_phenotype",
                    kind="contradiction",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {PHENOTYPE_MARKER}: '{m.group(0)}' is not among this gene's "
                        f"mouse knockout phenotypes — {data}.]"
                    ),
                )
            )
    return out


# Prose words for GTEx tissue labels (prefix/word match against the label).
_TISSUE_WORDS: dict[str, tuple[str, ...]] = {
    "kidney": ("kidney",),
    "renal": ("kidney",),
    "liver": ("liver",),
    "hepatic": ("liver",),
    "heart": ("heart",),
    "cardiac": ("heart",),
    "lung": ("lung",),
    "pulmonary": ("lung",),
    "brain": ("brain",),
    "neural": ("brain", "nerve"),
    "muscle": ("muscle",),
    "skin": ("skin",),
    "pancreas": ("pancreas",),
    "pancreatic": ("pancreas",),
    "blood": ("whole_blood",),
    "spleen": ("spleen",),
    "colon": ("colon",),
    "intestine": ("intestine", "colon"),
    "intestinal": ("intestine", "colon"),
    "stomach": ("stomach",),
    "esophagus": ("esophagus",),
    "oesophagus": ("esophagus",),
    "thyroid": ("thyroid",),
    "testis": ("testis",),
    "adipose": ("adipose",),
    "artery": ("artery",),
    "arterial": ("artery",),
    "vascular": ("artery",),
    "substantia nigra": ("substantia_nigra",),
    "striatum": ("putamen", "caudate"),
    "putamen": ("putamen",),
    "caudate": ("caudate",),
    "hippocampus": ("hippocampus",),
    "cerebellum": ("cerebell",),
    "cortex": ("brain_cortex", "frontal_cortex"),
    "hypothalamus": ("hypothalamus",),
}
_TISSUE_ALT = "|".join(sorted(_TISSUE_WORDS, key=len, reverse=True))
# Up to eight plain words may sit between the predicate and the tissue, within one clause
# (no comma, semicolon or full stop; one apposition colon/dash is allowed): "enriched in
# the disease-relevant cell type of dopaminergic neurons within the substantia nigra",
# "specificity for the disease-relevant tissue: the heart left ventricle".
_SPECIFIC_CLAIM = re.compile(
    r"(?:\b(?:specific(?:ally)?|specificity|enriched|enrichment|restricted|confined|"
    r"selective(?:ly)?|predominantly|mainly|primarily|highly|abundantly)\s+"
    r"(?:expressed\s+|expression\s+)?(?:to|for|in|within)\s+"
    r"(?:[\w-]+\s+){0,8}?(?:[\w-]+\s*[:\u2013\u2014]\s+)?(?:the\s+)?(?P<t1>" + _TISSUE_ALT + r")\b)"
    r"|(?:\b(?P<t2>" + _TISSUE_ALT + r")[- ](?:specific|enriched|restricted)\b)"
    r"|(?:\bhigh(?:est)?\s+(?:levels?\s+of\s+)?expression\s+in\s+(?:the\s+)?(?P<t3>"
    + _TISSUE_ALT
    + r")\b)",
    re.IGNORECASE,
)


def _best_rank(word: str, tissue_ranks: dict[str, int]) -> tuple[int | None, str]:
    stems = _TISSUE_WORDS.get(word.lower(), (word.lower(),))
    best: tuple[int | None, str] = (None, "")
    for label, rank in tissue_ranks.items():
        if any(s in label.lower() for s in stems) and (best[0] is None or rank < best[0]):
            best = (rank, label)
    return best


def check_tissue_claims(
    text: str,
    tissue_ranks: dict[str, int],
    hpa_specificity: str = "",
    *,
    top_k: int = 5,
) -> list[Violation]:
    """ "Specific to / enriched in / high expression in <tissue>" when the tissue is not
    among the gene's top-``top_k`` GTEx tissues and HPA does not name it as enriched."""
    if not tissue_ranks:
        return []
    total = len(tissue_ranks)
    top = [t for t, _ in sorted(tissue_ranks.items(), key=lambda kv: kv[1])[:3]]
    out: list[Violation] = []
    seen: set[str] = set()
    for sentence in split_sentences(strip_annotations(text)):
        for m in _SPECIFIC_CLAIM.finditer(sentence):
            word = (m.group("t1") or m.group("t2") or m.group("t3") or "").lower()
            if not word or word in seen or is_negated(sentence, m.start(), lookback=30):
                continue
            stems = _TISSUE_WORDS.get(word, (word,))
            if any(s.split("_")[0] in hpa_specificity.lower() for s in stems):
                continue
            rank, label = _best_rank(word, tissue_ranks)
            if rank is None or rank <= top_k:
                continue
            seen.add(word)
            out.append(
                Violation(
                    rule_id="grounding.tissue_specificity",
                    kind="contradiction",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {TISSUE_MARKER}: expression is not {word}-specific — {label} "
                        f"ranks {rank}/{total} in GTEx; the top tissues are {', '.join(top)}.]"
                    ),
                )
            )
    return out


APPROVAL_MARKER = "DRUG APPROVAL GUARD"
_APPROVED_RE = re.compile(r"\bapproved\b|\bapproval\b|\bmarketed\b|\blicensed\b", re.IGNORECASE)
# Wording that is about approval without asserting it ("not approved", "seeking approval").
_APPROVAL_HEDGE = re.compile(
    r"\b(?:not|never|un|pending|seeking|awaiting|before|prior to|towards?|candidate for|"
    r"would|could|may|if|until|filed for|submitted for)\b[^.]{0,25}$",
    re.IGNORECASE,
)


def check_drug_approval(text: str, drug_approval: dict[str, bool]) -> list[Violation]:
    """A drug the target's known-drug data marks as not approved, called approved.

    Open Targets lists one row per drug × trial indication; a row's disease is where the
    drug was *tested*, and its phase is not an approval. The investigator wrote
    "bococizumab … approved for chronic obstructive pulmonary disease" from a row with
    ``is_approved: false``.
    """
    names = {k.lower(): v for k, v in drug_approval.items() if k}
    if not names:
        return []
    out: list[Violation] = []
    seen: set[str] = set()
    for sentence in split_sentences(strip_annotations(text)):
        low = sentence.lower()
        for name, approved in names.items():
            if approved or name in seen:
                continue
            i = low.find(name)
            if i < 0:
                continue
            # the approval word must belong to this drug: within the same clause after it
            tail = low[i + len(name) : i + len(name) + 120].split(";")[0]
            m = _APPROVED_RE.search(tail)
            if not m:
                continue
            before = tail[: m.start()]
            if _APPROVAL_HEDGE.search(before) or is_negated(tail, m.start(), lookback=25):
                continue
            # another drug named between this one and "approved" owns the word
            if any(other in before for other in names if other != name):
                continue
            seen.add(name)
            out.append(
                Violation(
                    rule_id="grounding.drug_approval",
                    kind="contradiction",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {APPROVAL_MARKER}: the target's known-drug data do not mark "
                        f"{name} as approved; a listed disease is a trial indication, and a "
                        f"phase is not an approval.]"
                    ),
                )
            )
    return out


READOUT_MARKER = "TRIAL READOUT GUARD"
_ONGOING = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}
_RESULT_VERB = re.compile(
    r"\b(?:showed|shown|demonstrated|reduced|lowered|improved|met|achieved|reported|"
    r"found|significant(?:ly)?)\b",
    re.IGNORECASE,
)


_STOPPED_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
_VALIDATION_CLAIM = re.compile(
    r"\b(?:validat\w*|evidence\s+for\s+(?:the\s+)?(?:therapeutic|efficacy|benefit)|"
    r"therapeutic\s+(?:benefit|potential)|supports?\s+(?:the\s+)?efficacy)\b",
    re.IGNORECASE,
)


def check_stopped_trial_validation(
    text: str, trials: list[tuple[str, str, bool]]
) -> list[Violation]:
    """A terminated or withdrawn trial without posted results presented as validation
    (LRRK2: the stopped BIIB122 phase 3 "still provides evidence for therapeutic
    validation")."""
    stopped = {n.upper(): s for n, s, res in trials if s.upper() in _STOPPED_STATUSES and not res}
    out: list[Violation] = []
    for sentence in split_sentences(strip_annotations(text)):
        named = [n for n in stopped if n in sentence.upper()]
        if named and _VALIDATION_CLAIM.search(sentence):
            n = named[0]
            out.append(
                Violation(
                    rule_id="grounding.stopped_trial_validation",
                    kind="overstatement",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {READOUT_MARKER}: {n} was {stopped[n].lower()} without posted "
                        "results — it shows the target was tested, not that it works.]"
                    ),
                )
            )
            break
    return out


def check_trial_readout(text: str, trials: list[tuple[str, str, bool]]) -> list[Violation]:
    """A still-running trial without posted results described as having shown an effect.

    ``trials`` is (nct_id, registry status, has posted results). PCSK9 × MI: CORALreef
    Outcomes (NCT06008756, active, no results) was cited as having "shown significant
    reductions … in major adverse cardiovascular events".
    """
    ongoing = {
        n.upper(): s for n, s, has_results in trials if s.upper() in _ONGOING and not has_results
    }
    if not ongoing:
        return []
    out: list[Violation] = []
    seen: set[str] = set()
    for sentence in split_sentences(strip_annotations(text)):
        named = [n for n in ongoing if n in sentence.upper()]
        if not named or not _RESULT_VERB.search(sentence):
            continue
        if (
            re.search(
                r"\b(?:ongoing|recruiting|will|expected|planned|is testing|are testing|to assess|designed to)\b",
                sentence,
                re.IGNORECASE,
            )
            and len(named) == 1
        ):
            continue
        for n in named:
            if n in seen:
                continue
            seen.add(n)
            status = ongoing[n].replace("_", " ").lower()
            out.append(
                Violation(
                    rule_id="grounding.trial_readout",
                    kind="contradiction",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {READOUT_MARKER}: {n} is {status} with no posted results — "
                        "it has not shown an effect yet.]"
                    ),
                )
            )
    return out


MODALITY_MARKER = "MODALITY GUARD"
# International non-proprietary name stems → modality.
_STEM_MODALITY = (
    (re.compile(r"\b\w+mab\b", re.IGNORECASE), "monoclonal antibody"),
    (
        re.compile(r"\b\w+(?:siran|rsen|virsen)\b", re.IGNORECASE),
        "oligonucleotide (siRNA/antisense)",
    ),
    (re.compile(r"\b\w+cept\b", re.IGNORECASE), "fusion protein"),
)
_SMALL_MOLECULE = re.compile(r"\bsmall[- ]molecules?\b", re.IGNORECASE)


def check_modality(text: str) -> list[Violation]:
    """A biologic named as an example of a small molecule ("multiple small-molecule
    inhibitors, including evolocumab and alirocumab" — both monoclonal antibodies)."""
    out: list[Violation] = []
    for sentence in split_sentences(strip_annotations(text)):
        for clause in re.split(r"[;:]|\bwhile\b|\bwhereas\b|\bbut\b", sentence):
            sm = _SMALL_MOLECULE.search(clause)
            if not sm:
                continue
            tail = clause
            # "small molecules and antibodies" names both modalities — not a mislabel
            if re.search(
                r"\b(?:antibod|biologic|oligonucleotide|sirna|antisense)", clause, re.IGNORECASE
            ):
                continue
            for pattern, modality in _STEM_MODALITY:
                m = pattern.search(tail)
                if m:
                    out.append(
                        Violation(
                            rule_id="grounding.modality",
                            kind="contradiction",
                            excerpt=sentence,
                            note=(
                                f"[⚠ {MODALITY_MARKER}: {m.group(0)} is a {modality}, not a "
                                "small molecule.]"
                            ),
                        )
                    )
                    break
    return out


OMISSION_MARKER = "PHENOTYPE OMISSION GUARD"
_OMISSION_MIN_CHARS = 600


def check_phenotype_omission(
    text: str, mouse_phenotypes: list[str], disease: str = ""
) -> list[Violation]:
    """Organ systems named in the gene's mouse phenotypes that the prose never mentions.

    Safety Rule 14 asks for each organ-level phenotype to be named; LRRK2's safety lens
    omitted its lung phenotype (abnormal type II pneumocyte morphology). Judged on the
    whole text, since an omission has no sentence of its own.
    """
    body = strip_annotations(text).lower()
    # Guards run on every prose field; an omission is only meaningful in the full
    # narrative, not in a one-sentence summary or axis rationale.
    if len(body) < _OMISSION_MIN_CHARS or not mouse_phenotypes:
        return []
    # Naming the disease is not naming a liability ("myocardial infarction" contains the
    # heart vocabulary): mentions of the disease name are removed before the check.
    for name in disease_name_variants(disease) if disease else []:
        body = body.replace(name.lower(), " ")
    out: list[Violation] = []
    for organ, hits in group_by_system(mouse_phenotypes).items():
        if not prose_names_system(body, organ):
            out.append(
                Violation(
                    rule_id="grounding.phenotype_omission",
                    kind="overstatement",
                    excerpt="",
                    note=(
                        f"[⚠ {OMISSION_MARKER}: the mouse knockout data include {organ} "
                        f"phenotypes ({'; '.join(hits[:3])}) that the text does not name as a "
                        "candidate on-target liability.]"
                    ),
                )
            )
    return out


CELL_TYPE_MARKER = "CELL TYPE CLAIM GUARD"
_EXPRESSION_WORD = re.compile(
    r"\b(?:express\w*|enrich\w*|specific\w*|locali[sz]\w*|abundan\w*|TPM)\b", re.IGNORECASE
)
# A sentence stating the limitation is what the prompts ask for, not a claim.
_CELL_TYPE_HEDGE = re.compile(
    r"\b(?:cannot|can't|unable|whether|unknown|uncertain|unresolved|not\s+(?:established|"
    r"resolved|reported|known|available|measured))\b",
    re.IGNORECASE,
)


def cell_type_pattern(cell_type: str) -> re.Pattern[str]:
    """A disease cell type as prose writes it: any case, spaces or hyphens, plural."""
    words = [re.escape(w) for w in cell_type.split()]
    return re.compile(r"\b" + r"[\s-]+".join(words) + r"s?\b", re.IGNORECASE)


def cell_types_reported(cell_types: list[str], claim_texts: list[str]) -> frozenset[str]:
    """The disease cell types some claim names (the evidence a cell-type statement needs)."""
    joined = "\n".join(claim_texts)
    return frozenset(ct for ct in cell_types if cell_type_pattern(ct).search(joined))


def check_cell_type_claims(
    text: str, cell_types: list[str], reported: frozenset[str]
) -> list[Violation]:
    """Expression attributed to a disease cell type that no claim reports.

    Bulk GTEx cannot resolve a minor cell population, yet the biology and safety lenses
    turned the disease's cell type into a finding ("expressed in the disease-relevant
    dopaminergic neurons ... median TPM 3.33") in two runs of one gene. A sentence that
    ties expression to a disease cell type needs a claim naming that cell type.
    """
    out: list[Violation] = []
    seen: set[str] = set()
    for sentence in split_sentences(strip_annotations(text)):
        if not _EXPRESSION_WORD.search(sentence) or _CELL_TYPE_HEDGE.search(sentence):
            continue
        for ct in cell_types:
            if ct in reported or ct in seen:
                continue
            m = cell_type_pattern(ct).search(sentence)
            if not m or is_negated(sentence, m.start(), lookback=40):
                continue
            seen.add(ct)
            out.append(
                Violation(
                    rule_id="grounding.cell_type_claim",
                    kind="overstatement",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {CELL_TYPE_MARKER}: no retrieved claim reports expression in "
                        f"{ct}; bulk tissue data cannot resolve a cell type, so this is the "
                        "disease's cell type, not a measured expression finding.]"
                    ),
                )
            )
    return out
