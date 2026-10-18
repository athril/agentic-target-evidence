# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helper for building LLM-facing text from Evidence objects.

Clinical trials are special: the gene/target name often appears only in
eligibility_criteria, not in brief_summary. This module builds a
target-first concatenation for CLINICAL_TRIAL evidence so screeners
and claim-extractors can see the gene reference even when it is absent
from the summary field.
"""

from __future__ import annotations

import re

from schemas.evidence import Evidence, EvidenceType

# Eligibility criteria are long; the screener sees the first _ELIGIBILITY_CHARS, plus a window
# around the first target-gene mention when that falls later (e.g. "...gain-of-function
# mutation in the TRPC6 gene" as inclusion criterion 4, ~600 chars in).
_ELIGIBILITY_CHARS = 600
_MENTION_WINDOW = 200

# Drug-development phases as ClinicalTrials.gov reports them (``EARLY_PHASE1`` … ``PHASE4``,
# possibly combined like ``PHASE2/PHASE3``). Observational studies have none; device and
# behavioural trials report "NA".
_DRUG_PHASE_RE = re.compile(r"PHASE[1-4]")
# ClinicalTrials.gov primary purposes of studies that do not test a therapy.
NON_THERAPEUTIC_PURPOSES = frozenset({"DIAGNOSTIC", "SCREENING"})
_THERAPEUTIC_PURPOSES = frozenset({"TREATMENT", "PREVENTION", "SUPPORTIVE_CARE"})
# An imaging or diagnostic agent: a radioisotope label ("[18F]", "(18)F", "11C", "123I",
# "99mTc", "68Ga") or a scan/tracer/imaging modality.
_IMAGING_AGENT = re.compile(
    r"(?:[\[(]\s*)?\b(?:18|11|13|15|123|124|125|99m|68|64|89|111)\s*[\])]?\s*-?\s*"
    r"(?:F|C|N|O|I|Tc|Ga|Cu|Zr|In)\b|"
    r"\b(?:PET|SPECT|MRI|CT|cCTA|scans?|scanning|imaging|tracers?|radiotracers?|"
    r"radioligands?|radiopharmaceutical|contrast\s+agent|DaTscan|ioflupane)\b",
    re.IGNORECASE,
)
_COMPARATOR_ARM = re.compile(
    r"\b(?:placebo|control|vehicle|sham|standard\s+(?:of\s+)?care|usual\s+care)\b",
    re.IGNORECASE,
)


def is_diagnostic_study(primary_purpose: str, interventions: list[str] | tuple[str, ...]) -> bool:
    """A study that tests a diagnostic or imaging agent rather than a therapy.

    Imaging studies are registered with a phase for their tracer and name the gene in
    their cohort, so they look like trials of the target. Either the registry purpose says
    so (DIAGNOSTIC, SCREENING), or — when the purpose is not stated as therapeutic (OTHER,
    BASIC_SCIENCE and blank are used for both kinds) — every non-comparator intervention
    is an imaging or diagnostic agent. A stated TREATMENT/PREVENTION purpose is trusted
    (radioligand therapy is a treatment).
    """
    purpose = primary_purpose.upper()
    if purpose in NON_THERAPEUTIC_PURPOSES:
        return True
    if purpose in _THERAPEUTIC_PURPOSES:
        return False
    agents = [str(i) for i in interventions if i and not _COMPARATOR_ARM.search(str(i))]
    return bool(agents) and all(_IMAGING_AGENT.search(a) for a in agents)


# Typographic variants that source text (labels, abstracts, registry records) uses
# interchangeably with the ASCII character a term is written with: a label's
# "Parkinson’s disease" (U+2019) never matched "Parkinson's disease".
_TYPOGRAPHY = str.maketrans(
    {
        **dict.fromkeys("‘’‛ʼʻ′＇´`", "'"),
        **dict.fromkeys("‐‑‒–—−﹣－", "-"),
        **dict.fromkeys("     ", " "),
    }
)


def normalize_typography(text: str) -> str:
    """Map typographic apostrophes, dashes and spaces to their ASCII forms."""
    return (text or "").translate(_TYPOGRAPHY)


def _term_pattern(term: str) -> str:
    """Escaped term in which each space or hyphen between words matches either."""
    words = re.split(r"[\s-]+", term)
    return r"[\s-]+".join(re.escape(w) for w in words if w)


def mentions_term(text: str, term: str) -> bool:
    """Case-insensitive whole-token match: "ANTI-PCSK9" mentions PCSK9, "PCSK90" does not.

    Typographic apostrophes, dashes and spaces count as their ASCII forms, and a space
    or hyphen between a multi-word term's words matches either ("non-small cell" /
    "non small cell").
    """
    term = normalize_typography(term).strip()
    if not term or not text:
        return False
    pattern = r"(?<![A-Za-z0-9])" + _term_pattern(term) + r"(?![A-Za-z0-9])"
    return re.search(pattern, normalize_typography(text), re.IGNORECASE) is not None


_EPONYM_RE = re.compile(
    r"^([A-Z][a-z]+(?:-[A-Z][a-z]+)?)(?:'s|')?\s+(disease|syndrome|lymphoma|sarcoma|disorder|"
    r"palsy|anomaly|ataxia|dystrophy|chorea)\b(.*)$",
    re.IGNORECASE,
)


def disease_name_variants(disease: str) -> list[str]:
    """The disease name plus the other eponym forms labels and papers use.

    Ontologies drop the possessive ("Parkinson disease", "Crohn disease"); FDA labels
    write "Parkinson's disease", so a literal match missed every Parkinson's label. A
    name given in possessive form also yields the bare form, and an eponym ending in
    "s" also yields the bare-apostrophe possessive ("Graves' disease").
    """
    disease = normalize_typography(disease).strip()
    if not disease:
        return []
    out = [disease]
    m = _EPONYM_RE.match(disease)
    if m:
        stem, noun, rest = m.group(1), m.group(2), m.group(3)
        forms = [f"{stem} {noun}{rest}", f"{stem}'s {noun}{rest}"]
        if stem.lower().endswith("s"):
            forms.append(f"{stem}' {noun}{rest}")
        out += [f for f in forms if f.lower() != disease.lower()]
    return out


# Outcomes that matter to patients in any therapeutic area. A programme that met a
# surrogate primary and failed one of these failed; a primary that is one of these is not
# a surrogate.
HARD_ENDPOINT = re.compile(
    r"\b(?:overall survival|survival|mortality|deaths?|died|major adverse|mace\b|"
    r"cardiovascular events?|coronary events?|infarctions?|stroke|"
    r"hospitali[sz]ation|end[- ]stage|kidney failure|renal failure|dialysis|"
    r"transplant|amputation|liver[- ]related events?|decompensation)",
    re.IGNORECASE,
)

_HEADING_PART = re.compile(r"^[^,;()]{1,40}$")


def uninvert_heading(text: str) -> str:
    """A MeSH-style inverted heading in natural order: "Leukemia, Myeloid, Acute" →
    "Acute Myeloid Leukemia", "Arthritis, Rheumatoid" → "Rheumatoid Arthritis".

    Registries and MeSH write conditions this way; matching only the natural order missed
    an outcome trial registered under its own indication. Text that is not a short
    comma-separated heading (two to four parts of a few words) is returned unchanged.
    """
    parts = [p.strip() for p in normalize_typography(text).split(",")]
    if not 2 <= len(parts) <= 4 or not all(p and _HEADING_PART.match(p) for p in parts):
        return text
    if any(len(p.split()) > 4 for p in parts):
        return text
    return " ".join(reversed(parts))


def mentions_disease(text: str, disease: str) -> bool:
    """``mentions_term`` for a disease, accepting its eponym forms."""
    return any(mentions_term(text, v) for v in disease_name_variants(disease))


_POSSESSIVE = re.compile(r"(?<=[a-z])'s?\b")
_SEPARATORS = re.compile(r"[\s-]+")


def fold_name(text: str) -> str:
    """Lower-cased name with typography normalised, possessives dropped and spaces /
    hyphens collapsed: "Parkinson’s Disease" and "parkinson disease" fold alike."""
    lowered = normalize_typography(text).lower()
    return _SEPARATORS.sub(" ", _POSSESSIVE.sub("", lowered)).strip()


def trait_matches(trait: str, terms: list[str]) -> bool:
    """A trait/disease label contains one of ``terms`` (substring, after ``fold_name``).

    Used to scope GWAS, colocalisation and knowledge-graph associations to the run's
    disease; a raw substring test missed "Parkinson's disease" for "Parkinson disease".
    """
    folded = fold_name(trait)
    return any(t and fold_name(t) in folded for t in terms)


# A clause that names a condition only to deny or limit its use: "efficacy in X has not
# been established", "not indicated for X", "Limitations of Use: … not studied in X".
_DENIED_USE = re.compile(
    r"\bnot\s+(?:been\s+|yet\s+been\s+)?(?:established|recommended|indicated|approved|"
    r"studied|shown|demonstrated|evaluated|determined|known|intended|for\s+use)\b|"
    r"\b(?:should|must|may)\s+not\s+be\s+used\b|"
    r"\blimitations?\s+of\s+use\b|"
    r"\bcontraindicated\b|"
    r"\bno\s+(?:data|evidence|established\s+(?:efficacy|benefit))\b|"
    r"\b(?:efficacy|safety|effectiveness|benefit)\s+(?:[\w'-]+\s+){0,12}(?:is|are|was|were|has|"
    r"have)\s+(?:not|unknown|unclear|uncertain)\b",
    re.IGNORECASE,
)
# Sentence and clause boundaries inside label prose (bullets, "; ", ". ", ", but ").
_USE_CLAUSE_SPLIT = re.compile(r"(?<=[.;:])\s+|\s*•\s*|,?\s+but\s+", re.IGNORECASE)


def indicates_term(text: str, term: str) -> bool:
    """``term`` is named in ``text`` in at least one clause that does not deny or limit
    its use.

    Label indication text also names conditions a drug is *not* for ("The efficacy … in
    stroke … and <disease> has not been established … not recommended"); a plain
    mention match counted such a label as an approval in that disease.
    """
    for clause in _USE_CLAUSE_SPLIT.split(normalize_typography(text)):
        if mentions_term(clause, term) and not _DENIED_USE.search(clause):
            return True
    return False


def indicates_disease(text: str, disease: str) -> bool:
    """``indicates_term`` for a disease, accepting its eponym forms."""
    return any(indicates_term(text, v) for v in disease_name_variants(disease))


def _eligibility(ev: Evidence) -> str:
    pc = ev.extra.get("participation_criteria") or {}
    return str((pc.get("eligibility_criteria") if isinstance(pc, dict) else None) or "")


def _trial_record_text(ev: Evidence) -> str:
    """Every free-text field of a trial record, untruncated — for deterministic gene matching."""
    fields = [
        ev.extra.get("title") or "",
        " ".join(str(i) for i in ev.extra.get("interventions") or []),
        " ".join(str(c) for c in ev.extra.get("conditions") or []),
        _eligibility(ev),
        ev.extra.get("brief_summary") or "",
        str(ev.extra.get("design_details") or ""),
    ]
    return "\n".join(fields)


def is_target_drug_trial(ev: Evidence) -> bool:
    """A phased (drug-development) clinical trial whose record names the target gene, or
    that was retrieved as a trial of one of the target's drugs.

    Such a trial tests a modality aimed at the target in humans — the strongest clinical
    evidence the pipeline can retrieve — so it is kept deterministically rather than left to
    LLM screening, which can miss a drug-code-only trial (e.g. "BI 764198") whose gene mention
    sits deep in the eligibility criteria.
    """
    if ev.evidence_type != EvidenceType.CLINICAL_TRIAL or not ev.gene:
        return False
    if not _DRUG_PHASE_RE.search(str(ev.extra.get("phase") or "").upper()):
        return False
    # A diagnostic/screening study is registered with a phase for its imaging or diagnostic
    # agent and names the gene in its cohort; it is not a test of a modality on the target.
    purpose = str((ev.extra.get("study_plan") or {}).get("primary_purpose") or "")
    if is_diagnostic_study(purpose, [str(i) for i in ev.extra.get("interventions") or []]):
        return False
    # Retrieved by one of the target's drugs (see services/retrieval/clinical_trial):
    # the record names the drug, which is the link to the target.
    if str(ev.extra.get("matched_by") or "").startswith("drug:"):
        return True
    return mentions_term(_trial_record_text(ev), ev.gene)


# Trial registry identifiers (WHO ICTRP primary registries and ClinicalTrials.gov), as
# they appear in registry records and in abstracts ("ClinicalTrials.gov number, …").
_REGISTRY_ID_RE = re.compile(
    r"\b(?:NCT\d{8}|ISRCTN\d{8}|EUCTR\d{4}-\d{6}-\d{2}|\d{4}-\d{6}-\d{2}|"
    r"ChiCTR[-A-Za-z0-9]{6,}|ACTRN\d{14}|CTRI/\d{4}/\d{2,3}/\d{6}|jRCT[a-z]?\d{6,}|"
    r"JPRN-[A-Za-z]+\d+|UMIN\d{9}|DRKS\d{8}|KCT\d{7}|IRCT[A-Za-z0-9]{6,}|PACTR\d{12,}|"
    r"NTR\d{3,5}|NL-OMON\d+|RBR-[a-z0-9]{6,}|TCTR\d{11}|SLCTR/\d{4}/\d{3}|"
    r"LBCTR\d{10}|ITMCTR\d+|CTIS\d{4}-\d{6}-\d{2}-\d{2})\b"
)


def registry_ids(text: str) -> set[str]:
    """Trial registry identifiers named in ``text`` (upper-cased)."""
    return {m.group(0).upper() for m in _REGISTRY_ID_RE.finditer(normalize_typography(text))}


FDA_LABEL_PREFIX = "fda:label:"
FDA_FAERS_PREFIX = "fda:faers:"


def label_acts_on_target(ev: Evidence) -> bool:
    """An FDA label of a drug acting on the target: retrieval stamped it as one of the
    target's known drugs (``target_drug``), or its mechanism-of-action text names the gene.

    MoA text names a biologic's target by protein ("IL-17A", "estrogen receptor"), so the
    gene-symbol test alone called every approved IL-17A antibody off-target.
    """
    if ev.evidence_type != EvidenceType.REGULATORY or not ev.source.startswith(FDA_LABEL_PREFIX):
        return False
    return bool(ev.extra.get("target_drug")) or mentions_term(
        str(ev.extra.get("mechanism_of_action") or ""), ev.gene
    )


def is_on_target_fda_label(ev: Evidence) -> bool:
    """An FDA drug label whose MoA names the target gene or whose indication names the disease.

    Either match is direct regulatory evidence for the lens — a drug acting on the target,
    or approval precedent in the indication — but neither is "the gene's role in the
    disease", so the LLM screener would drop a label like sparsentan for TRPC6 × FSGS.
    Labels that matched only on a looser indication term go to the LLM.
    """
    if ev.evidence_type != EvidenceType.REGULATORY or not ev.source.startswith(FDA_LABEL_PREFIX):
        return False
    indication = str(ev.extra.get("indications_and_usage") or "")
    return label_acts_on_target(ev) or indicates_disease(indication, ev.disease)


# Section cross-references inside a label bullet: "( 1.1 , 12.1 , 14.1 )".
_SECTION_REFS_RE = re.compile(r"\(\s*\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*\s*\)")
# Where the per-indication subsections ("1.1 Immunoglobulin A Nephropathy ...") begin.
_SUBSECTION_RE = re.compile(r"\s1\.1\s+[A-Z]")
_LABEL_HEADER_RE = re.compile(r"^\s*1\s+INDICATIONS\s+(?:AND|&)\s+USAGE\s*", re.IGNORECASE)


def label_indications(indications_and_usage: str, max_chars: int = 300) -> list[str]:
    """Split an FDA label's INDICATIONS AND USAGE text into one entry per indication.

    Labels list indications as "•" bullets before the numbered subsections; each bullet
    is returned without its section cross-references. A label with no bullets yields its
    opening statement. Slicing the raw text at a fixed length instead drops every
    indication after the first — it hid sparsentan's FSGS approval behind its IgAN one.
    """
    text = _LABEL_HEADER_RE.sub("", indications_and_usage or "")
    # Cross-references first, so "( 1.1 , 12.1 )" is not taken for subsection 1.1.
    text = " ".join(_SECTION_REFS_RE.sub("", text).split())
    if not text:
        return []
    m = _SUBSECTION_RE.search(text)
    head = text[: m.start()] if m else text
    if "•" not in head:
        return [head[:max_chars]]
    bullets = head.split("•")[1:]
    out: list[str] = []
    for b in bullets:
        clean = b.strip().rstrip(" .;")
        if clean:
            out.append(clean[:max_chars])
    return out


def is_on_target_patent(ev: Evidence) -> bool:
    """A patent whose title or abstract names the target gene.

    A patent on the target is intellectual-property evidence for the commercial lens
    whatever indication it claims, but it is not "the gene's role in the disease", so
    the LLM screener leaves it uncertain — and patents never get the PMC full text that
    lets the second pass resolve an uncertain item.
    """
    if ev.evidence_type != EvidenceType.PATENT:
        return False
    text = f"{ev.extra.get('title') or ''}\n{ev.extra.get('abstract') or ''}"
    return mentions_term(text, ev.gene)


def screenable_text(ev: Evidence) -> str:
    """Return the best available LLM-facing text for an Evidence item.

    For CLINICAL_TRIAL: target-first ordering (interventions → conditions →
    eligibility snippet → brief_summary → design_details) so gene mentions
    buried in eligibility criteria reach the model.

    For REGULATORY (openFDA labels and FAERS bundles) and DRUGGABILITY (UniProt,
    ChEMBL, DGIdb, TTD): the record's pre-rendered ``text`` summary.

    For all other types: standard fallback chain identical to the previous
    _screenable_text helper in the screening agent.
    """
    if ev.evidence_type == EvidenceType.CLINICAL_TRIAL:
        return _clinical_text(ev)
    if ev.evidence_type in (EvidenceType.REGULATORY, EvidenceType.DRUGGABILITY):
        return str(ev.extra.get("text") or "")
    role = _ROLE_NOTES.get(str(ev.extra.get("retrieval_role") or ""))
    body = (
        ev.extra.get("abstract")
        or ev.extra.get("brief_summary")
        or ev.extra.get("assoc_text")
        or ev.extra.get("tract_text")
        or ev.extra.get("description")
        or ""
    )
    if role and body and ev.gene:
        return f"[{role.format(gene=ev.gene)}]\n{body}"
    return body


# Papers fetched by the disease-independent target-level queries
# (agents/retrieval/literature): tell the screener why an off-indication paper is here.
_ROLE_NOTES = {
    "target_genetics": "Retrieved as human-genetics evidence about {gene} in any indication",
    "target_human_genetics": (
        "Retrieved as genetic-proxy (Mendelian randomisation / loss-of-function carrier) "
        "evidence about {gene} in any indication"
    ),
    "target_drug_trials": "Retrieved as a trial or meta-analysis of a drug acting on {gene}",
    "target_safety": "Retrieved as safety or knockout evidence about {gene}",
    "indication_drug_trials": (
        "Retrieved as a trial, in this indication, of a drug acting on {gene}; positive or "
        "negative results are both direct clinical evidence"
    ),
}


def _clinical_text(ev: Evidence) -> str:
    parts: list[str] = []

    # A trial found by one of the target's drugs rarely names the gene; say why it is
    # here so relevance is judged on the drug–target link, not on a gene keyword.
    matched_by = str(ev.extra.get("matched_by") or "")
    if matched_by.startswith("drug:") and ev.gene:
        parts.append(
            f"Retrieved as a trial of {matched_by[5:]}, a drug that acts on {ev.gene} "
            f"(from {ev.gene}'s known drugs or {ev.gene}-matched trials)."
        )
    elif matched_by.startswith("registry:") and ev.gene:
        parts.append(
            f"Returned by the registry's synonym search for {matched_by[9:]} (a drug that acts "
            f"on {ev.gene}); the record does not name it — keep only if the tested product is "
            f"{matched_by[9:]} under another name."
        )

    interventions = ev.extra.get("interventions") or []
    if interventions:
        parts.append("Interventions: " + ", ".join(str(i) for i in interventions))

    conditions = ev.extra.get("conditions") or []
    if conditions:
        parts.append("Conditions: " + ", ".join(str(c) for c in conditions))

    # participation_criteria may be a nested dict (as stored by fetch_trials)
    eligibility = _eligibility(ev)
    if eligibility:
        snippet = eligibility[:_ELIGIBILITY_CHARS]
        late = _late_gene_mention(eligibility, ev.gene)
        if late:
            snippet += " … " + late
        parts.append("Eligibility: " + snippet)

    brief = ev.extra.get("brief_summary") or ""
    if brief:
        parts.append(brief)

    design = ev.extra.get("design_details") or ""
    if design:
        parts.append(str(design))

    return "\n\n".join(parts)


def _late_gene_mention(eligibility: str, gene: str) -> str:
    """Window around the first gene mention past the eligibility cut, or "" if none."""
    if not gene or mentions_term(eligibility[:_ELIGIBILITY_CHARS], gene):
        return ""
    pattern = r"(?<![A-Za-z0-9])" + re.escape(gene) + r"(?![A-Za-z0-9])"
    m = re.search(pattern, eligibility, re.IGNORECASE)
    if not m:
        return ""
    start = max(_ELIGIBILITY_CHARS, m.start() - _MENTION_WINDOW)
    return eligibility[start : m.end() + _MENTION_WINDOW]
