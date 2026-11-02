# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""One list of a target's drugs, built from every source that names them.

Sources key drugs differently and lag each other: an aggregator may not list a new approval,
a label may be filed under its brand name, and a biologic carries a four-letter suffix in one
source and none in another. Each drug's stage is set here, by a fixed rule, so nothing
downstream has to reconcile them: the regulator's record outranks the aggregator's, and a
lookup that failed is reported as not checked, never as no approval.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_SALTS = {
    "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM", "HYDROCHLORIDE", "HCL", "HYDROBROMIDE", "BROMIDE",
    "CHLORIDE", "ANHYDROUS", "MONOHYDRATE", "DIHYDRATE",
}  # fmt: skip
_BIOLOGIC_SUFFIX_RE = re.compile(r"-[A-Z]{4}$")  # FDA's four-letter suffix: "NAME-LIGA"
_WORD_RE = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"
_SECTION_HEAD_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:\w+ ){0,3}mechanism of action\s*:?\s*", re.I
)
_SENTENCE_RE = re.compile(r"(?<=[.;])\s+(?=[A-Z])")
_REASON_CHARS = 90
_MAX_ROWS = 25

STAGE_US = "US approved"
STAGE_OTHER = "Approved, no US application found"
STAGE_LABEL = "US label, no application found"
STAGE_APPROVED_UNCHECKED = "Approved, US status not checked"
STAGE_NONE = "Stage not recorded"


def drug_key(name: str) -> str:
    """The name sources share: upper case, without a salt or ester or a biologic suffix."""
    words = str(name).upper().split()
    while len(words) > 1 and (words[-1] in _SALTS or words[-1].endswith("ATE")):
        words.pop()
    return _BIOLOGIC_SUFFIX_RE.sub("", " ".join(words))


def _names(text: str, name: str) -> bool:
    return bool(re.search(_WORD_RE.format(re.escape(name)), text, re.IGNORECASE))


def on_target(label: Mapping[str, Any], symbols: Iterable[str]) -> bool:
    """A label whose drug acts on the gene. A mechanism section opens with what the drug does
    and goes on to pathways and biomarkers, so naming the gene anywhere is not enough: an
    inhibitor of one kinase can name a tumour suppressor whose loss activates it. The gene
    must be in the first sentence, or in the second beside the drug's own name."""
    text = _SECTION_HEAD_RE.sub("", str(label.get("mechanism_of_action") or ""))
    first, second = [*_SENTENCE_RE.split(text, maxsplit=2), "", ""][:2]
    drug = drug_key(str(label.get("drug_name") or ""))
    for symbol in filter(None, symbols):
        if _names(first, symbol) or (_names(second, symbol) and drug and _names(second, drug)):
            return True
    return False


def names_target_first(label: Mapping[str, Any], symbols: Iterable[str]) -> bool:
    """A label whose mechanism's first sentence names the gene: the drug acts on it. A
    combination (Trikafta) is named by its ingredients' product, not by the gene's name."""
    text = _SECTION_HEAD_RE.sub("", str(label.get("mechanism_of_action") or ""))
    first = _SENTENCE_RE.split(text, maxsplit=1)[0]
    return any(_names(first, s) for s in symbols if s)


def _stops(entry: Mapping[str, Any]) -> list[str]:
    """Why the drug's stopped trials stopped, in the record's own words."""
    return [
        str(r.get("why"))
        for r in entry.get("stop_reports") or []
        if r.get("why") and str(r.get("status") or "").upper() in ("TERMINATED", "WITHDRAWN")
    ]


def candidates(
    known: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    symbols: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Every drug named for the target, by shared name: Open Targets rows merged across
    indications and salts, then on-target labels Open Targets does not list."""
    out: dict[str, dict[str, Any]] = {}

    def entry(name: str) -> dict[str, Any]:
        return out.setdefault(
            drug_key(name),
            {"name": drug_key(name), "modality": "", "max_phase": None, "approved": False,
             "labelled": False, "target_linked": False, "brands": [], "stops": []},
        )  # fmt: skip

    for d in known:
        if not d.get("drug_name"):
            continue
        e = entry(d["drug_name"])
        e["target_linked"] = True
        e["modality"] = e["modality"] or str(d.get("drug_type") or "")
        phase = d.get("max_phase")
        if isinstance(phase, int | float) and (e["max_phase"] is None or phase > e["max_phase"]):
            e["max_phase"] = phase
        e["approved"] = e["approved"] or bool(d.get("is_approved"))
        e["stops"] += _stops(d)
    symbols = list(symbols)
    for label in labels:
        if not label.get("drug_name") or not on_target(label, symbols):
            continue
        e = entry(label["drug_name"])
        e["labelled"] = True
        e["target_linked"] = e["target_linked"] or names_target_first(label, symbols)
        e["brands"] += [str(b).upper() for b in label.get("brand_names") or []]
    return out


def _stage(e: Mapping[str, Any]) -> str:
    approval = e.get("approval") or {}
    if approval:
        stage = f"{STAGE_US} {approval.get('approval_date')}"
        return f"{stage}; discontinued" if approval.get("discontinued") else stage
    if e.get("lookup_failed"):
        return STAGE_APPROVED_UNCHECKED if e.get("approved") else _phase(e)
    if e.get("approved"):
        return STAGE_OTHER
    return STAGE_LABEL if e.get("labelled") else _phase(e)


def _phase(e: Mapping[str, Any]) -> str:
    phase = e.get("max_phase")
    return f"Phase {phase:g}" if isinstance(phase, int | float) and phase > 0 else STAGE_NONE


def _rank(row: Mapping[str, Any]) -> tuple[int, float, str, str]:
    approved = row["us_approved"]
    return (
        0 if approved else 1 if row["approved"] else 2,
        -(row.get("max_phase") or 0),
        str(row.get("approval_date") or ""),
        row["name"],
    )


def rows(
    entries: Mapping[str, Mapping[str, Any]],
    approvals: Mapping[str, Mapping[str, Any] | None],
    failed: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """One row per drug: entries that resolve to the same US application are one drug, named
    by the shared name of its ingredient. ``approvals`` is by entry key, None for no US
    application; ``failed`` holds the keys whose lookup failed."""
    failed = set(failed)
    merged: dict[str, dict[str, Any]] = {}
    for key, entry in entries.items():
        approval = dict(approvals.get(key) or {})
        ingredient = drug_key((approval.get("ingredients") or [key])[0])
        group = str(approval.get("application_number") or ingredient)
        into = merged.setdefault(
            group,
            {**entry, "name": ingredient, "brands": [], "stops": [], "modality": "",
             "max_phase": None, "approved": False, "labelled": False, "target_linked": False,
             "approval": {}},
        )  # fmt: skip
        into["approval"] = into["approval"] or approval
        into["lookup_failed"] = into.get("lookup_failed") or key in failed
        into["modality"] = into["modality"] or entry.get("modality") or ""
        into["approved"] = into["approved"] or bool(entry.get("approved"))
        into["labelled"] = into["labelled"] or bool(entry.get("labelled"))
        into["target_linked"] = into["target_linked"] or bool(entry.get("target_linked"))
        if entry.get("max_phase") is not None:
            into["max_phase"] = max(into["max_phase"] or 0, entry["max_phase"])
        into["brands"] += list(entry.get("brands") or [])
        into["stops"] += list(entry.get("stops") or [])
    out = []
    for e in merged.values():
        approval = e["approval"]
        out.append(
            {
                "name": e["name"],
                "brands": sorted({*e["brands"], *(approval.get("brand_names") or [])}),
                "modality": e["modality"],
                "route": approval.get("route") or "",
                "stage": _stage(e),
                "target_linked": e["target_linked"],
                "us_approved": bool(approval),
                "approved": bool(approval) or bool(e["approved"]),
                "approval_date": approval.get("approval_date") or "",
                "application": approval.get("application_number") or "",
                "sponsor": approval.get("sponsor") or "",
                "max_phase": e["max_phase"],
                "stops": list(dict.fromkeys(e["stops"])),
                "source_link": approval.get("source_link") or "",
            }
        )
    return sorted(out, key=_rank)


def approved_names(data: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """(name, brands) of the approved drugs, US approvals first."""
    return [(r["name"], r["brands"]) for r in data.get("drugs") or [] if r.get("approved")]


def named(row: Mapping[str, Any], text: str) -> bool:
    """Whether the text names the drug, by its name or a brand."""
    names = [row.get("name"), *(row.get("brands") or [])]
    return any(n and _names(text, n) for n in names)


def _display(row: Mapping[str, Any]) -> str:
    brands = ", ".join(b.title() for b in row.get("brands") or [])
    return f"{row['name'].title()} ({brands})" if brands else row["name"].title()


def landscape_md(data: Mapping[str, Any], cite: str) -> str:
    """Every drug of the target with its stage, from the records."""
    drugs = list(data.get("drugs") or [])
    if not drugs:
        return ""
    out = [
        "| Drug | Modality | Route | Stage | Sponsor | Application | Trial stopped | Refs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in drugs[:_MAX_ROWS]:
        stops = [] if r.get("approved") else r.get("stops") or []
        stop = stops[0][:_REASON_CHARS].replace("|", "/") if stops else "—"
        if len(stops) > 1:
            stop += f" (+{len(stops) - 1} more)"
        cells = [
            _display(r),
            r.get("modality") or "not in Open Targets",
            (r.get("route") or "—").title(),
            r["stage"],
            (r.get("sponsor") or "—").title(),
            r.get("application") or "—",
            stop,
        ]
        out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + f" | {cite} |")
    if len(drugs) > _MAX_ROWS:
        out += ["", f"Top {_MAX_ROWS} of {len(drugs)} drugs, most advanced first. {cite}"]
    out += [
        "",
        "US approval and its date come from Drugs@FDA, the original application of the drug's "
        "ingredient; other stages from Open Targets. 'No US application found' means the "
        "ingredient has no NDA or BLA there under this name, which leaves open where else it "
        "is approved. A drug marked 'not in Open Targets' was matched from the opening of its "
        "label, which can name a partner of the real target, so confirm it acts on this gene "
        "before relying on the row. 'Trial stopped' is a stopped trial's own reason, not the "
        f"fate of the programme. {cite}",
    ]
    return "\n".join(out)


# ── what the gene is to a therapy ───────────────────────────────────────────────────

RELATIONS = {
    "direct": "Direct target modulation",
    "biomarker": "Biomarker-selected therapy",
    "pathway": "Pathway modulation",
    "genotype": "Genotype-selected safety (boxed warning)",
    "investigation": "Clinical investigation",
}
_CLAUSE_RE = re.compile(r"(?<=[.;•])\s+|\s+•\s+")
# Words that make a gene a criterion for choosing patients, close beside its name.
_STATUS_RE = re.compile(
    r"deficien\w*|mutat\w*|mutant|alter(?:ed|ation\w*)|wild[- ]type|deletion\w*|\bloss\b|"
    r"\bpositive\b|\bnegative\b|variant\w*|rearrang\w*|fusion\w*|amplif\w*|overexpress\w*|"
    r"\bnull\b|deleterious",
    re.IGNORECASE,
)
# A label that names a gene to say the use is untested there does not select by it.
_NOT_SELECTING_RE = re.compile(
    r"not (?:been )?(?:established|studied|evaluated|known|demonstrated|indicated)|"
    r"limitations? of use|no data",
    re.IGNORECASE,
)
_NEAR_CHARS = 40
_CLAUSE_CHARS = 320
_MAX_CLAUSES = 3
_INDICATION_HEAD_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)?\s+)?INDICATIONS? (?:AND|&) USAGE\s*:?\s*", re.I
)
_FAMILY_STEM_RE = re.compile(r"^([A-Za-z]{3,})\d+$")
# Trial interventions that are procedures or comparators, not therapies.
_NOT_THERAPY_RE = re.compile(
    r"placebo|standard of care|best supportive|usual care|biospecimen|monitoring|questionnaire"
    r"|quality[- ]of[- ]life|biopsy|imaging|tomography|survey",
    re.IGNORECASE,
)
_LABEL_REFERENCE_RE = re.compile(r"\s*\(\s*\d+(?:\.\d+)*\s*\)\s*")
_LATE_PHASES = ("PHASE3", "PHASE4")
_MAX_DIRECT, _MAX_INVESTIGATION = 5, 6


def _selects(clause: str, symbols: Sequence[str]) -> bool:
    if _NOT_SELECTING_RE.search(clause):
        return False
    for symbol in symbols:
        for m in re.finditer(_WORD_RE.format(re.escape(symbol)), clause, re.IGNORECASE):
            if _STATUS_RE.search(clause[max(0, m.start() - _NEAR_CHARS) : m.end() + _NEAR_CHARS]):
                return True
    return False


def _with_family_stem(symbols: Iterable[str]) -> list[str]:
    """The symbols and each one's family stem: a label can select by the family's name, as
    "ABC-mutated" for ABC1 and ABC2. Beside a status word the stem is specific enough."""
    symbols = [s for s in symbols if s]
    stems = [m.group(1) for s in symbols if (m := _FAMILY_STEM_RE.match(s))]
    return list(dict.fromkeys([*symbols, *stems]))


def selection_clause(text: str, symbols: Iterable[str]) -> str:
    """The label's words making the gene's status a criterion for treatment, "" if none: each
    clause naming the gene beside a status word and not saying the use is untested. A label
    can select by the gene in several indications, so every one is kept."""
    symbols = _with_family_stem(symbols)
    clauses = dict.fromkeys(
        _INDICATION_HEAD_RE.sub("", _LABEL_REFERENCE_RE.sub(" ", re.sub(r"\s+", " ", c))).strip()[
            :_CLAUSE_CHARS
        ]
        for c in _CLAUSE_RE.split(text or "")
        if _selects(c, symbols)
    )
    return " · ".join(list(clauses)[:_MAX_CLAUSES])


def relate_labels(
    labels: Sequence[Mapping[str, Any]], symbols: Iterable[str], direct: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(biomarker-selected, pathway) labels. An indication selecting patients by the gene makes
    a drug biomarker-selected, whether or not it acts on the gene. A drug that acts on the gene
    belongs to the drug list; a mechanism section naming the gene without acting on it makes
    it pathway modulation."""
    symbols, direct = list(symbols), {drug_key(d) for d in direct}
    selected: dict[str, dict[str, Any]] = {}
    pathway: dict[str, dict[str, Any]] = {}
    for label in labels:
        name = drug_key(str(label.get("drug_name") or ""))
        if not name:
            continue
        row = {"name": name, "brands": [str(b).upper() for b in label.get("brand_names") or []]}
        # A direct drug can also select patients by the gene (ivacaftor: "at least one mutation
        # in the CFTR gene"), so its indications are read whether or not it acts on the gene.
        clause = selection_clause(str(label.get("indications_and_usage") or ""), symbols)
        if clause:
            selected.setdefault(name, {**row, "basis": clause})
            continue
        if name in direct or on_target(label, symbols):
            continue
        text = str(label.get("mechanism_of_action") or "")
        if any(_names(text, s) for s in symbols if s):
            sentence = next(
                (x for x in _CLAUSE_RE.split(text) if any(_names(x, s) for s in symbols if s)), ""
            )
            pathway.setdefault(name, {**row, "basis": sentence.strip()[:_CLAUSE_CHARS]})
    pathway = {k: v for k, v in pathway.items() if k not in selected}
    return list(selected.values()), list(pathway.values())


def _symbol_excerpt(text: str, symbols: Iterable[str]) -> str:
    """The words around the first gene symbol in the text, in the label's own words. Case is
    kept: "eGFR" (kidney function) is not EGFR."""
    spans = [
        m.span() for s in symbols if s for m in re.finditer(_WORD_RE.format(re.escape(s)), text)
    ]
    if not spans:
        return ""
    start, end = min(spans)
    head, tail = max(0, start - _CLAUSE_CHARS // 2), min(len(text), end + _CLAUSE_CHARS // 2)
    return ("…" if head else "") + text[head:tail].strip() + ("…" if tail < len(text) else "")


def genotype_rows(
    labels: Sequence[Mapping[str, Any]], symbols: Iterable[str]
) -> list[dict[str, Any]]:
    """Drugs whose boxed warning names the gene: a pharmacogenetic safety statement (abacavir
    and HLA-B*5701). The excerpt around the gene is the label's own wording."""
    symbols = [s for s in symbols if s]
    rows: dict[str, dict[str, Any]] = {}
    for label in labels:
        name = drug_key(str(label.get("drug_name") or ""))
        basis = _symbol_excerpt(str(label.get("boxed_warning") or ""), symbols)
        if not name or name in rows or not basis:
            continue
        rows[name] = {
            "name": name,
            "brands": [str(b).upper() for b in label.get("brand_names") or []],
            "basis": basis,
        }
    return list(rows.values())


def attach_approval(row: dict[str, Any], approval: Mapping[str, Any] | None) -> dict[str, Any]:
    """The regulator's dates beside a label-derived row."""
    approval = approval or {}
    return {
        **row,
        "brands": sorted({*row.get("brands", []), *(approval.get("brand_names") or [])}),
        "application": approval.get("application_number") or "",
        "approval_date": approval.get("approval_date") or "",
        "efficacy_supplements": list(approval.get("efficacy_supplements") or []),
        "approved": bool(approval),
    }


def diagnostics_for(
    devices: Sequence[Mapping[str, Any]], symbols: Iterable[str], drugs: Iterable[str]
) -> list[dict[str, Any]]:
    """Devices whose approval order names the gene and one of the therapy's drugs: an order
    naming only the gene may be any assay, and one naming only the drug may detect another
    marker. Each keeps the drugs it names, for its row."""
    symbols, drugs = [s for s in symbols if s], [d for d in drugs if d]
    out = []
    for d in devices:
        text = str(d.get("statement") or "")
        if not any(_names(text, s) for s in symbols):
            continue
        named_drugs = [n for n in drugs if _names(text, n)]
        if named_drugs:
            out.append({**d, "drugs": named_drugs})
    return sorted(out, key=lambda d: str(d.get("decision_date") or ""), reverse=True)


def investigation_rows(trials: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Trials enrolling by the gene's status, late phases first, then by size."""

    def late(t: Mapping[str, Any]) -> bool:
        return any(p in _LATE_PHASES for p in t.get("phases") or [])

    ranked = sorted(trials, key=lambda t: (not late(t), -(t.get("enrollment") or 0)))
    return [
        {
            "nct_id": t.get("nct_id", ""),
            "acronym": t.get("acronym", ""),
            "title": t.get("title", ""),
            "phases": list(t.get("phases") or []),
            "status": t.get("status", ""),
            "enrollment": t.get("enrollment"),
            "drugs": [i for i in t.get("interventions") or [] if not _NOT_THERAPY_RE.search(i)][:3],
        }
        for t in ranked[:_MAX_INVESTIGATION]
    ]


def trial_names_drug(trial: Mapping[str, Any], names: Iterable[str]) -> bool:
    """Whether a trial's interventions name one of the drugs."""
    text = " ".join(str(i) for i in trial.get("interventions") or [])
    return any(n and _names(text, drug_key(n)) for n in names)


def _dates(row: Mapping[str, Any]) -> str:
    bits = []
    if row.get("approval_date"):
        bits.append(f"US approved {row['approval_date']}")
    if row.get("efficacy_supplements"):
        bits.append(f"efficacy supplements {', '.join(row['efficacy_supplements'])}")
    return "; ".join(bits) or "FDA label; no US application found"


def relevance_md(
    landscape: Mapping[str, Any], data: Mapping[str, Any], cite: str, trial_cite: Any
) -> str:
    """Every kind of clinical link between the gene and a therapy, one row per link."""
    # A label can name the gene without the drug acting on it (talazoparib's mechanism names
    # BRCA1 as a defect in the cells it kills, not as its target): direct needs the target link.
    direct_all = list(landscape.get("drugs") or [])
    direct = [r for r in direct_all if r.get("target_linked", True)]
    labelled = [r for r in direct_all if not r.get("target_linked", True)]
    selected = list(data.get("biomarker") or [])
    pathway = list(data.get("pathway") or [])
    genotype = list(data.get("genotype") or [])
    trials = list(data.get("investigation") or [])
    if not any((direct_all, selected, pathway, genotype, trials)):
        return ""
    out = [
        "| Relation | Therapy | What the record says | Status | Diagnostic | Refs |",
        "|---|---|---|---|---|---|",
    ]
    for r in direct[:_MAX_DIRECT]:
        out.append(
            f"| {RELATIONS['direct']} | {_display(r)} | acts on the gene's product "
            f"| {r['stage']} | — | {cite} |"
        )
    if len(direct) > _MAX_DIRECT:
        out.append(
            f"| {RELATIONS['direct']} | … and {len(direct) - _MAX_DIRECT} more in the drug table "
            f"| — | — | — | {cite} |"
        )
    for r in labelled:
        out.append(
            f"| {RELATIONS['pathway']} | {_display(r)} | the label's mechanism names the gene; "
            f"Open Targets does not list it as a target | {r['stage']} | — | {cite} |"
        )
    devices = list(data.get("diagnostics") or [])
    for r in selected:
        found = [d for d in devices if r["name"] in d.get("drugs", [])]
        test = "; ".join(
            f"{d['device']} ({d['pma_number']}{' ' + d['supplement'] if d.get('supplement') else ''}"
            f", {d['decision_date']})"
            for d in found[:3]
        )
        basis = str(r.get("basis") or "").replace("|", "/")
        out.append(
            f"| {RELATIONS['biomarker']} | {_display(r)} | {basis} | {_dates(r)} "
            f"| {test or 'none named in a PMA order'} | {cite} |"
        )
    for r in pathway:
        basis = str(r.get("basis") or "").replace("|", "/")
        out.append(
            f"| {RELATIONS['pathway']} | {_display(r)} | {basis} | {_dates(r)} | — | {cite} |"
        )
    for r in genotype:
        basis = str(r.get("basis") or "").replace("|", "/")
        out.append(f"| {RELATIONS['genotype']} | {_display(r)} | {basis} | — | — | {cite} |")
    for t in trials:
        name = t.get("acronym") or t.get("nct_id")
        drugs = ", ".join(t.get("drugs") or []) or "—"
        phase = "/".join(p.replace("PHASE", "Phase ") for p in t.get("phases") or []) or "—"
        size = f", n = {t['enrollment']:,}" if t.get("enrollment") else ""
        status = f"{phase}, {str(t.get('status') or '').lower().replace('_', ' ')}{size}"
        out.append(
            f"| {RELATIONS['investigation']} | {drugs} | {name}: "
            f"{str(t.get('title') or '').replace('|', '/')} | {status} | — "
            f"| {trial_cite(t.get('nct_id'))} |"
        )
    out += [
        "",
        "A gene with no drug on it can still decide who is treated. Direct rows come from the "
        "drug table. A biomarker-selected row is a label naming the gene beside a status word "
        "in its indication; a pathway row is a label naming the gene in its mechanism section "
        "without acting on it; both are matched on the label's words, so read the label. Dates "
        "are Drugs@FDA's: an efficacy supplement changes what a drug is indicated for, but the "
        "record does not say which indication it added. Diagnostics are PMA orders naming both "
        "the gene and the drug. Investigation rows match status phrases in the trial record, "
        f"not its eligibility criteria. {cite}",
    ]
    return "\n".join(out)
