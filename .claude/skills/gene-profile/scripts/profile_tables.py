# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Programmatic evidence tables: the allelic series and the variant spectrum.

Direction of effect was the writer's reading of the papers it chose. These tables are
built from records instead, one row per variant, so the prose about mechanism rests on
rows a reader can check:

* where the variant sits: UniProt's positional features (domains, repeats, transmembrane
  segments, topological domains, regions);
* what it does to the target, typed only from data: a truncating consequence lowers the
  target; Open Targets' curated direction (``directionOnTarget``) or the variant's own
  eQTL/pQTL beta give the change carried by the alternate allele (pipeline rules in
  ``services.evidence.variant_effect``: no QTL for a protein-altering variant);
* UniProt's curated note on the variant ("in FSGS2; increases calcium ion transport"),
  verbatim, and the direction it states for the protein's own activity or amount
  (``note_effect``: "increases … activity" is a gain, "abolishes", "decreases … transport"
  or "increases … degradation" a loss; clauses that disagree give "mixed"; modifications by
  other enzymes, double mutants and phenotypes are not typed);
* the phenotype: UniProt's disease, ClinVar's classification, the GWAS trait, and the
  disease the literature relates it to (PubTator).

The spectrum counts ClinVar pathogenic / likely pathogenic variants (≥ 1 review star,
conflicting and synonymous classifications left out) by consequence class, and the missense
ones by protein region: a gain-of-function or dominant-negative disease gene typically
shows clustered missense and no truncating variants; a loss-of-function one shows
truncating variants spread along the protein. That reading is the writer's, in an
*Interpretation* block; the counts are data.

``draft.md`` names a table with a line ``{{table:<name>}}``; ``render.py`` replaces it with
the markdown below, whose rows carry their own citations. Pure module.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MAX_ROWS = 15
_NOTE_CHARS = 90

_AA3 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E",
    "Gly": "G", "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F",
    "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "Ter": "*",
}  # fmt: skip
_PROTEIN = re.compile(
    r"^p\.\(?(?P<ref>[A-Z][a-z]{2}|[A-Z])(?P<pos>\d+)(?P<alt>[A-Z][a-z]{2}|[A-Z*]|fs.*|=)?"
)
_TRUNCATING = {
    "stop_gained",
    "frameshift_variant",
    "splice_acceptor_variant",
    "splice_donor_variant",
    "start_lost",
}
_INFRAME = {"inframe_insertion", "inframe_deletion", "protein_altering_variant", "stop_lost"}
_CLASSES = ("truncating", "missense", "in-frame", "splice region", "synonymous", "other")
# Positional UniProt features that name a part of the protein, most specific first.
REGION_TYPES = (
    "Active site",
    "Binding site",
    "Motif",
    "Zinc finger",
    "Repeat",
    "Domain",
    "Transmembrane",
    "Intramembrane",
    "Coiled coil",
    "Region",
    "Signal",
    "Propeptide",
    "Topological domain",
)


# ── parsing ─────────────────────────────────────────────────────────────────────────


def one_letter(code: str) -> str:
    return _AA3.get(code, code)


def protein_change(hgvsp: str) -> tuple[str, int, str] | None:
    """(reference, position, variant) residues of an HGVS protein change, one-letter
    ("p.Pro112Gln" → ("P", 112, "Q"); a frameshift's variant is "fs")."""
    h = (hgvsp or "").strip()
    m = _PROTEIN.match(h)
    if not m:
        return None
    # "p.Ser364LysfsTer12": the first changed residue, then a frameshift
    alt = "fs" if "fs" in h[m.end("pos") :] else one_letter(m.group("alt") or "")
    return one_letter(m.group("ref")), int(m.group("pos")), alt


def short_change(hgvsp: str) -> str:
    c = protein_change(hgvsp)
    return f"{c[0]}{c[1]}{c[2]}" if c else ""


def consequence_class(consequence: str) -> str:
    c = (consequence or "").lower()
    if c in _TRUNCATING:
        return "truncating"
    if c == "missense_variant":
        return "missense"
    if c in _INFRAME:
        return "in-frame"
    if "splice" in c:
        return "splice region"
    if c == "synonymous_variant":
        return "synonymous"
    return "other"


def parse_uniprot_variant(description: str) -> dict[str, Any]:
    """UniProt's natural-variant note split into diseases ("in FSGS2"), dbSNP id and the
    remaining note, kept verbatim ("increases calcium ion transport")."""
    diseases: list[str] = []
    rsid = ""
    note: list[str] = []
    for part in (p.strip() for p in (description or "").split(";")):
        if not part:
            continue
        snp = re.search(r"dbSNP:(rs\d+)", part)
        if snp:
            rsid = snp.group(1)
            part = part.replace(snp.group(0), "").strip()
            if part in ("", "in"):
                continue
        if part.startswith("in ") and re.match(r"in [A-Z0-9][A-Z0-9-]+\b", part):
            diseases.extend(d.strip() for d in re.split(r",| and ", part[3:]) if d.strip())
        else:
            note.append(part)
    return {"diseases": diseases, "rsid": rsid.lower(), "note": "; ".join(note)}


def region_of(position: int, features: Sequence[Mapping[str, Any]]) -> str:
    """The most specific named feature containing ``position``, with its topological
    domain ("ANK 2 · Cytoplasmic"); "" outside every feature."""
    inside = [
        f for f in features if f.get("type") in REGION_TYPES and f["start"] <= position <= f["end"]
    ]
    if not inside:
        return ""
    topo = next((f for f in inside if f["type"] == "Topological domain"), None)
    named = sorted(
        (f for f in inside if f["type"] != "Topological domain"),
        key=lambda f: (REGION_TYPES.index(f["type"]), f["end"] - f["start"]),
    )
    label = ""
    if named:
        f = named[0]
        label = f.get("description") or f["type"]
        if f["type"] in ("Transmembrane", "Intramembrane") and not f.get("description"):
            label = f["type"].lower()
    if topo is not None:
        label = (
            f"{label} · {topo.get('description') or 'topological'}"
            if label
            else (topo.get("description") or "")
        )
    return label


# ── ClinVar ─────────────────────────────────────────────────────────────────────────


def clinvar_label(v: Mapping[str, Any]) -> str:
    """One ClinVar classification as the series shows it: significance, the condition(s)
    it is for, stars and submissions ("ClinVar Pathogenic for Hypercholesterolemia,
    familial, 1 (1★, 1 submission)"). A mirror row without conditions shows stars only."""
    stars = v.get("gold_stars") or 0
    conditions = [c.get("name") or "" for c in v.get("conditions") or []]
    label = f"ClinVar {v.get('clinical_significance')}"
    if conditions:
        label += " for " + "; ".join(conditions[:2])
        if len(conditions) > 2:
            label += f" (+{len(conditions) - 2} more)"
    n = v.get("submissions")
    if n:
        return f"{label} ({stars}★, {n} submission{'s' if n != 1 else ''})"
    return f"{label} ({stars}★)"


# ── spectrum ────────────────────────────────────────────────────────────────────────


def plp_variants(clinvar: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [*(clinvar.get("pathogenic") or []), *(clinvar.get("likely_pathogenic") or [])]
    return [
        v
        for v in rows
        if "conflicting" not in str(v.get("clinical_significance") or "").lower()
        and (v.get("gold_stars") or 0) >= 1
    ]


def variant_spectrum(
    clinvar: Mapping[str, Any],
    lof: Mapping[str, Any],
    features: Sequence[Mapping[str, Any]],
    clinvar_key: str = "gnomad_clinvar",
) -> dict[str, Any]:
    """ClinVar P/LP by consequence class and missense P/LP by protein region; gnomAD
    high-confidence pLoF carriers. ``clinvar_key`` names the ClinVar record counted."""
    plp = plp_variants(clinvar)
    classes = Counter(consequence_class(v.get("major_consequence") or "") for v in plp)
    reviewed = Counter(
        consequence_class(v.get("major_consequence") or "")
        for v in plp
        if (v.get("gold_stars") or 0) >= 2
    )
    by_region: Counter[str] = Counter()
    unplaced = 0
    for v in plp:
        if consequence_class(v.get("major_consequence") or "") != "missense":
            continue
        c = protein_change(v.get("hgvsp") or "")
        region = region_of(c[1], features) if c else ""
        if region:
            by_region[region] += 1
        else:
            unplaced += 1
    reported = lof.get("reported_variants") or []
    return {
        "clinvar_key": clinvar_key,
        "plp_total": len(plp),
        "plp_by_class": {k: classes.get(k, 0) for k in _CLASSES},
        "plp_2star_by_class": {k: reviewed.get(k, 0) for k in _CLASSES},
        "missense_by_region": dict(by_region.most_common()),
        "missense_outside_features": unplaced,
        "synonymous_excluded": classes.get("synonymous", 0),
        "lof_hc_count": lof.get("hc_lof_count"),
        "lof_any_homozygous": lof.get("any_homozygous"),
        "lof_homozygotes": sum(int(v.get("homozygote_count") or 0) for v in reported),
        "lof_max_af": lof.get("max_af"),
    }


# ── allelic series ──────────────────────────────────────────────────────────────────


# UniProt's variant and mutagenesis notes state a change as a verb on the protein's
# activity ("increases calcium ion transport", "abolishes kinase activity", "loss of
# function"). Only clauses about the protein's own readout are typed: its activity, or its
# amount (stability, degradation, secretion, expression). A clause about a modification
# another enzyme makes to it ("loss of cleavage by furin" — for a protein that cleavage
# inactivates, that is a gain), a double mutant ("when associated with Y-374") or a
# phenotype ("correlated with lower plasma LDL") says nothing typable about the variant's
# own effect. A clause about what removes or switches off the protein flips the verb:
# "increases degradation" and "decreases inactivation" say the opposite of their verb.
_GLOBAL_GAIN_RE = re.compile(
    r"\b(gain[- ]of[- ]function|constitutive(ly)?[- ](active|activ\w*|open)|hyperactiv\w*)\b",
    re.IGNORECASE,
)
_GLOBAL_LOSS_RE = re.compile(
    r"\b(loss[- ]of[- ]function|non-?functional|(no|abolishe[sd]|loss of|lack of) "
    r"(detectable |residual |\w+ )?(activity|function))\b",
    re.IGNORECASE,
)
_GAIN_RE = re.compile(
    r"\b(increase[sd]?|enhance[sd]?|potentiate[sd]?|stimulate[sd]?|prolong(s|ed)?)\b",
    re.IGNORECASE,
)
_LOSS_RE = re.compile(
    r"\b(loss of|lack of|abolish(es|ed)?|abrogate[sd]?|decrease[sd]?|reduce[sd]?|impair(s|ed)?"
    r"|diminish(es|ed)?|disrupt(s|ed)?|inhibit(s|ed)?|inactive|unstable|destabiliz\w*"
    r"|misfold\w*|not secreted|retained in the (er|endoplasmic))\b",
    re.IGNORECASE,
)
_DOMINANT_NEGATIVE_RE = re.compile(r"\bdominant[- ]negative\b", re.IGNORECASE)
_NEUTRAL_RE = re.compile(
    r"\b(no effect|does not|did not|not (change|affect|alter)|no change|unchanged"
    r"|uncertain significance|benign)\b",
    re.IGNORECASE,
)
# Clauses that do not state the variant's own effect on the protein.
_UNTYPED_RE = re.compile(
    r"\b(when associated with|in combination with|correlated with|associated with|plasma"
    r"|serum|patients?|risk|cleavage by|cleaved by|phosphorylat\w*|glycosylat\w*|sulfat\w*"
    r"|acetylat\w*|methylat\w*|sumoylat\w*|palmitoylat\w*|uptake)\b",
    re.IGNORECASE,
)
_FLIP_RE = re.compile(
    r"\b(degradation|ubiquitination|turnover|inactivation|desensiti[sz]ation|internali[sz]ation"
    r"|endocytosis|autoinhibition|clearance|proteolysis)\b",
    re.IGNORECASE,
)
# The protein's own readout: what it does, then how much of it there is.
_ACTIVITY_RE = re.compile(
    r"\b(activity|transport|current|conductance|function|signal\w*|catalytic|binding|affinity"
    r"|gating|opening|inactivation|desensiti[sz]ation)\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"\b(stability|stable|unstable|destabiliz\w*|degradation|ubiquitination|turnover|secret\w*"
    r"|expression|protein levels?|misfold\w*|retained in the|folding)\b",
    re.IGNORECASE,
)


def note_effect(note: str) -> str:
    """The change UniProt's note says the variant makes to the protein: "gain", "loss",
    "dominant negative", "mixed" (clauses on its activity disagree) or "" (no change to
    its own activity or amount stated)."""
    if _DOMINANT_NEGATIVE_RE.search(note or ""):
        return "dominant negative"
    on_activity: set[str] = set()
    on_amount: set[str] = set()
    for clause in re.split(r"[;,.]| and ", note or ""):
        if not clause.strip() or _NEUTRAL_RE.search(clause) or _UNTYPED_RE.search(clause):
            continue
        if _GLOBAL_GAIN_RE.search(clause):
            on_activity.add("gain")
            continue
        if _GLOBAL_LOSS_RE.search(clause):
            on_activity.add("loss")
            continue
        gain, loss = bool(_GAIN_RE.search(clause)), bool(_LOSS_RE.search(clause))
        if gain == loss:  # neither, or both in one clause
            continue
        direction = "gain" if gain else "loss"
        if _FLIP_RE.search(clause):
            direction = "loss" if direction == "gain" else "gain"
        if _ACTIVITY_RE.search(clause):
            on_activity.add(direction)
        elif _AMOUNT_RE.search(clause):
            on_amount.add(direction)
    decisive = on_activity or on_amount
    return decisive.pop() if len(decisive) == 1 else "mixed" if decisive else ""


def _effect(
    effects: Sequence[Mapping[str, Any]], consequence: str, note: str = ""
) -> tuple[str, str]:
    """(effect text, source) for the variant: truncating, else Open Targets curated, else
    UniProt's note, else the variant's own QTL for the alternate allele."""
    from services.evidence.variant_effect import curated_change, qtl_change

    if consequence_class(consequence) == "truncating":
        return "loss (truncating)", "consequence"
    for e in effects:
        data = _Obj(e)
        change = curated_change(data)
        if change is not None:
            word = {"decrease": "loss of function", "increase": "gain of function"}[change]
            return f"{word} (alt {e.get('alt') or '?'}, curated)", "curated"
    stated = note_effect(note)
    if stated:
        word = {"gain": "gain of function", "loss": "loss of function"}.get(stated, stated)
        return f"{word} (UniProt note)", "uniprot_note"
    for e in effects:
        data = _Obj(e)
        change = qtl_change(data)
        if change is not None:
            kinds = sorted({q["study_type"] for q in e.get("qtls") or []})
            word = {"decrease": "lower", "increase": "higher"}[change]
            what = "protein" if kinds == ["pqtl"] else "expression"
            return f"{word} {what} (alt {e.get('alt') or '?'}, own {'/'.join(kinds)})", "qtl"
    return "", ""


class _Obj:
    """Attribute access over a stored ``VariantTargetEffect`` dict (the pure rules in
    ``variant_effect`` read attributes)."""

    def __init__(self, d: Mapping[str, Any]) -> None:
        self.curated = list(d.get("curated") or [])
        self.consequences = list(d.get("consequences") or [])
        self.qtls = [_Qtl(q) for q in d.get("qtls") or []]


class _Qtl:
    def __init__(self, q: Mapping[str, Any]) -> None:
        self.study_type = q.get("study_type", "")
        self.beta = q.get("beta") or 0.0
        self.lead_missense_in_target = bool(q.get("lead_missense_in_target"))


def series_rsids(
    uniprot: Mapping[str, Any],
    gwas: Mapping[str, Any],
    literature: Mapping[str, Any],
    limit: int = 20,
) -> list[str]:
    """The rs ids whose Open Targets records the series looks up: UniProt disease
    variants, the most-studied literature variants, GWAS leads (in that order)."""
    out: list[str] = []
    for v in uniprot.get("variants") or []:
        if v.get("rsid") and v.get("diseases"):
            out.append(v["rsid"])
    for v in literature.get("variants") or []:
        if v.get("rsid"):
            out.append(v["rsid"])
    for h in gwas.get("hits") or []:
        if h.get("rs_id"):
            out.append(str(h["rs_id"]).lower())
    return list(dict.fromkeys(out))[:limit]


_CHANGE_RE = re.compile(r"^([A-Z*])(\d+)([A-Z*]+)$")


def same_change(a: str, b: str) -> bool:
    """Two protein changes name one variant: equal, or written from the other allele
    (UniProt's G670E is the literature's E670G)."""
    if a == b:
        return True
    ma, mb = _CHANGE_RE.match(a), _CHANGE_RE.match(b)
    return bool(ma and mb and ma[2] == mb[2] and ma[1] == mb[3] and ma[3] == mb[1])


def allelic_series(
    *,
    uniprot: Mapping[str, Any],
    clinvar: Mapping[str, Any],
    lof: Mapping[str, Any],
    gwas: Mapping[str, Any],
    literature: Mapping[str, Any],
    effects: Mapping[str, Sequence[Mapping[str, Any]]],
    clinvar_key: str = "gnomad_clinvar",
) -> dict[str, Any]:
    """One row per variant, joined on protein change or rs id; the rows most informative
    for direction first (disease variants, then the most studied).

    Each row lists its nucleotide alleles (GRCh38 'chrom-pos-ref-alt', HGVS c. and the
    transcript when known): one rs id can carry several alleles with different protein
    changes (D374Y and D374H), and one protein change can come from several alleles.
    A ClinVar classification names the condition it is for (``clinvar_key`` is the record
    the rows cite: ClinVar itself, or gnomAD's mirror, which carries no condition)."""
    features = uniprot.get("features") or []
    disease_names = {
        d.get("acronym"): d.get("name") for d in uniprot.get("diseases") or [] if d.get("acronym")
    }
    rows: dict[str, dict[str, Any]] = {}
    by_rsid: dict[str, str] = {}

    def row(key: str, rsid: str = "") -> dict[str, Any]:
        # An rs id can carry several alleles (D374Y and D374H share one): join on it only
        # when one side has no protein change, never two different changes.
        change = "" if key.startswith("rs") else key
        if rsid and rsid in by_rsid and key not in rows:
            joined = rows[by_rsid[rsid]]
            if not change or not joined["change"] or same_change(change, joined["change"]):
                key = by_rsid[rsid]
                joined["change"] = joined["change"] or change
        r = rows.setdefault(
            key,
            {
                "change": key if not key.startswith("rs") else "",
                "position": None,
                "rsid": "",
                "consequence": "",
                "origins": [],
                "uniprot_note": "",
                "phenotypes": [],
                "refs": [],
                "literature_papers": 0,
                "disease_variant": False,
                "alleles": [],
                # the table's columns, one property each (the same facts as origins and
                # phenotypes): curated classification for a condition, population
                # association, literature
                "clinical": [],
                "population": [],
                "literature": [],
            },
        )
        if rsid:
            r["rsid"] = rsid
            by_rsid.setdefault(rsid, key)
        return r

    for v in uniprot.get("variants") or []:
        key = f"{v['ref']}{v['position']}{v['alt']}"
        r = row(key, v.get("rsid", ""))
        r["position"] = v["position"]
        r["consequence"] = r["consequence"] or (
            "missense_variant" if len(v["ref"]) == len(v["alt"]) == 1 else ""
        )
        r["origins"].append("UniProt")
        r["uniprot_note"] = v.get("note", "")
        for d in v.get("diseases") or []:
            name = disease_names.get(d)
            r["phenotypes"].append(f"{name} ({d})" if name else d)
            r["clinical"].append(f"UniProt: {name} ({d})" if name else f"UniProt: {d}")
            r["disease_variant"] = True
        r["refs"] += ["uniprot_extra", *(f"pmid:{p}" for p in (v.get("pmids") or [])[:2])]

    for v in plp_variants(clinvar):
        if consequence_class(v.get("major_consequence") or "") == "synonymous":
            continue
        c = protein_change(v.get("hgvsp") or "")
        key = f"{c[0]}{c[1]}{c[2]}" if c else v.get("hgvsc") or ""  # splice: c.399+1G>A
        if not key:
            continue
        r = row(key)
        r["position"] = c[1] if c else r["position"]
        r["consequence"] = v.get("major_consequence") or r["consequence"]
        r["origins"].append(clinvar_label(v))
        r["clinical"].append(clinvar_label(v))
        r["disease_variant"] = True
        r["refs"].append(clinvar_key)
        _add_allele(r, v.get("variant_id"), v.get("hgvsc"), v.get("transcript"))

    for v in (lof.get("reported_variants") or [])[:5]:
        c = protein_change(v.get("hgvsp") or "")
        if c is None:
            continue
        r = row(f"{c[0]}{c[1]}{c[2]}")
        r["position"] = c[1]
        r["consequence"] = v.get("consequence") or r["consequence"]
        hom = v.get("homozygote_count") or 0
        carriers = f"gnomAD pLoF, {v.get('ac')} carriers" + (f", {hom} homozygous" if hom else "")
        r["origins"].append(carriers)
        r["population"].append(carriers)
        r["refs"].append("gnomad_lof")
        _add_allele(r, v.get("variant_id"), v.get("hgvsc"))

    for v in literature.get("variants") or []:
        rsid = v.get("rsid") or ""
        hgvs = v.get("hgvs") or ""
        change = short_change(hgvs) if hgvs.startswith("p.") else ""
        key = change or rsid or v.get("id", "")
        if not key:
            continue
        r = row(key, rsid)
        r["literature_papers"] = v.get("papers") or 0
        r["origins"].append(f"named in {v.get('papers')} papers")
        r["literature"].append(f"named in {v.get('papers')} papers")
        diseases = [x for x in v.get("related") or [] if x.get("kind") == "disease"]
        for rel in diseases[:1]:
            kinds = ", ".join(rel.get("relations") or {})
            r["phenotypes"].append(f"{rel['name']} ({kinds}, {rel['papers']} papers)")
            r["literature"].append(f"{rel['name']} ({kinds} tags, {rel['papers']} papers)")
        r["refs"] += [f"pmid:{p}" for p in (v.get("pmids") or [])[:2]] + ["pubtator_map"]

    for h in gwas.get("hits") or []:
        rsid = str(h.get("rs_id") or "").lower()
        if not rsid:
            continue
        r = row(rsid, rsid)
        if "GWAS" not in " ".join(r["origins"]):
            r["origins"].append("GWAS lead")
            r["phenotypes"].append(f"{h.get('trait')} (GWAS)")
            r["population"].append(f"GWAS lead: {h.get('trait')}")
            r["refs"].append("gwas")

    for r in rows.values():
        found = effects.get(r["rsid"]) or []
        if not r["consequence"] and found:
            r["consequence"] = next(iter(found[0].get("consequences") or []), "")
        if r["position"] is None and found:
            c = re.match(r"^([A-Z])(\d+)([A-Z*])$", found[0].get("amino_acid_change") or "")
            if c:
                r["position"] = int(c.group(2))
                r["change"] = r["change"] or found[0]["amino_acid_change"]
        if not r["alleles"]:  # UniProt / literature rows: the Open Targets allele of the change
            for e in found:
                if r["change"] and e.get("amino_acid_change") == r["change"]:
                    _add_allele(r, e.get("variant_id"))
        r["effect"], r["effect_source"] = _effect(found, r["consequence"], r["uniprot_note"])
        if r["effect_source"] in ("curated", "qtl"):
            r["refs"].append("allelic_series")
        r["region"] = region_of(r["position"], features) if r["position"] else ""
        r["refs"] = list(dict.fromkeys(r["refs"]))

    ordered = sorted(
        rows.values(),
        key=lambda r: (-evidence_weight(r), -r["literature_papers"], r["position"] or 10**9),
    )
    return {"total": len(ordered), "rows": ordered}


def _add_allele(r: dict[str, Any], vid: Any, hgvsc: Any = "", transcript: Any = "") -> None:
    """Record one nucleotide allele of the row once ('1_55057454_G_T' and '1-55057454-G-T'
    are one allele)."""
    vid = str(vid or "").replace("_", "-")
    for a in r["alleles"]:
        if (vid and a["id"] == vid) or (hgvsc and a["hgvsc"] == hgvsc):
            a["id"] = a["id"] or vid
            a["hgvsc"] = a["hgvsc"] or str(hgvsc or "")
            a["transcript"] = a["transcript"] or str(transcript or "")
            return
    if vid or hgvsc:
        r["alleles"].append(
            {"id": vid, "hgvsc": str(hgvsc or ""), "transcript": str(transcript or "")}
        )


def evidence_weight(r: Mapping[str, Any]) -> int:
    """How much a row tells a reader about the variant's effect: curated disease variants
    with a functional note and multi-star ClinVar calls first, a single submitter's call
    last among the disease variants."""
    stars = max(
        (int(m.group(1)) for o in r["origins"] if (m := re.search(r"\((\d)★", o))), default=0
    )
    weight = 0
    if "UniProt" in r["origins"] and r["disease_variant"]:
        weight += 4 + (1 if r["uniprot_note"] else 0)
    weight += 3 if stars >= 2 else 1 if stars == 1 else 0
    weight += min(3, r["literature_papers"] // 3)
    weight += 1 if r["effect_source"] in ("curated", "qtl", "uniprot_note") else 0
    weight += 1 if any(o == "GWAS lead" for o in r["origins"]) else 0
    return weight


# ── markdown ────────────────────────────────────────────────────────────────────────


def _cell(text: Any) -> str:
    return str(text or "—").replace("|", "/").replace("\n", " ")


def _short(text: str, n: int = _NOTE_CHARS) -> str:
    """``text`` cut to ``n`` characters at a clause ("; ") or, failing that, a word."""
    if len(text) <= n:
        return text
    cut = text[: n - 1]
    stop = cut.rfind("; ")
    stop = stop if stop > n // 3 else cut.rfind(" ")
    return (cut[:stop] if stop > 0 else cut).rstrip(" ;,") + "…"


def variant_name(r: Mapping[str, Any]) -> str:
    """The row's protein change, its nucleotide allele(s) and rs id: "D374Y · c.1120G>T ·
    rs137852912". An allele without HGVS c. shows its GRCh38 position."""
    alleles = [a.get("hgvsc") or a.get("id") or "" for a in r.get("alleles") or []]
    parts = [r.get("change") or "", " / ".join(a for a in alleles if a and a != r.get("change"))]
    return " · ".join(p for p in (*parts, r.get("rsid") or "") if p)


def alleles_caption(rows: Sequence[Mapping[str, Any]]) -> str:
    """Where the alleles are named: the transcript(s) of the HGVS c. names, GRCh38."""
    tx = sorted(
        {a["transcript"] for r in rows for a in r.get("alleles") or [] if a.get("transcript")}
    )
    where = f"HGVS c. on {', '.join(tx)}" if tx else "HGVS c. on the canonical transcript"
    return (
        f"Alleles: {where}; genomic positions (GRCh38) in raw/allelic_series.json. Alleles "
        "sharing an rs id are separate rows; a ClinVar classification is for the condition it names"
    )


def effect_class(effect: str) -> str:
    """The effect column reduced to its class, for the tally under the series."""
    e = (effect or "").lower()
    for word in ("truncating", "dominant negative", "mixed", "gain", "loss", "higher", "lower"):
        if word in e:
            return {"higher": "higher expression", "lower": "lower expression"}.get(word, word)
    return "not typed"


def effect_tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How the disease variants (UniProt disease variants, ClinVar P/LP) change the target,
    counted over every row of the series, not only the rows shown."""
    order = ("gain", "loss", "truncating", "dominant negative", "mixed", "not typed")
    counts = Counter(effect_class(r.get("effect") or "") for r in rows if r.get("disease_variant"))
    return {k: counts[k] for k in order if counts.get(k)}


def _cite(keys: Iterable[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


# ── functional evidence from papers ────────────────────────────────────────────────

# What the writer reads in a functional study of a variant, one entry per finding:
# {"variant": "D374Y", "readout": "LDL receptor binding affinity", "direction": "gain",
#  "system": "purified protein, surface plasmon resonance", "condition": "pH 7.4",
#  "pmid": "17080197", "quote": "<the abstract's own words>"}. check_profile.py verifies
# each entry against the paper's abstract; the table shows it apart from what the records
# type, and the tally counts it separately.
FUNCTION_FILE = "variant_function.json"
FUNCTION_DIRECTIONS = ("gain", "loss", "dominant negative", "neutral", "mixed")
_FUNCTION_FIELDS = ("variant", "readout", "direction", "system", "pmid", "quote")
_AA1_TO_3 = {v: k for k, v in _AA3.items()}
_STOP_FORMS = r"(?:X|\*|Ter|stop)"
_SEP = r"[\s\-–→>]?"


def variant_pattern(change: str) -> re.Pattern[str]:
    """How a paper writes a variant: "D374Y", "p.D374Y", "Asp374Tyr", "Asp-374-Tyr";
    "Y142X" / "Y142*" / "Tyr142Ter"; "L15fs"; an HGVS c. change verbatim."""
    m = re.match(r"^([A-Z])(\d+)([A-Z*]|fs)$", change or "")
    if not m:
        return re.compile(re.escape(change) if change else r"(?!)")
    ref, pos, alt = m.groups()
    stop = alt in ("*", "X")
    one_alt = _STOP_FORMS if stop else alt
    one = rf"(?<![A-Za-z]){ref}{_SEP}{pos}{_SEP}{one_alt}(?![a-z])"
    ref3 = _AA1_TO_3.get(ref)
    alt3 = _STOP_FORMS if stop else "(?:[A-Z][a-z]{2})?fs" if alt == "fs" else _AA1_TO_3.get(alt)
    if not (ref3 and alt3):  # a residue code without a three-letter name: one-letter only
        return re.compile(one)
    three = rf"{ref3}{_SEP}{pos}{_SEP}{alt3}"
    return re.compile(rf"(?:{one}|{three})")


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def function_problems(
    entries: Any,
    rows: Sequence[Mapping[str, Any]],
    abstracts: Mapping[str, str],
    sources: Iterable[str],
) -> list[str]:
    """What is wrong with ``variant_function.json``: an entry must name a row of the
    series, give every field, use a known direction, cite a registered paper whose abstract
    is cached, quote that abstract word for word, and the abstract must name the variant."""
    if not isinstance(entries, list):
        return ["variant_function.json must be a list of entries"]
    names = {r.get("change") for r in rows if r.get("change")} | {
        a.get("hgvsc") for r in rows for a in r.get("alleles") or [] if a.get("hgvsc")
    }
    known = set(sources)
    out: list[str] = []
    for i, e in enumerate(entries, start=1):
        where = f"entry {i} ({e.get('variant') if isinstance(e, dict) else '?'})"
        if not isinstance(e, dict):
            out.append(f"{where}: not an object")
            continue
        missing = [f for f in _FUNCTION_FIELDS if not str(e.get(f) or "").strip()]
        if missing:
            out.append(f"{where}: missing {', '.join(missing)}")
            continue
        pmid = str(e["pmid"]).removeprefix("pmid:")
        if e["variant"] not in names:
            out.append(f"{where}: not a variant of the allelic series")
        if e["direction"] not in FUNCTION_DIRECTIONS:
            out.append(f"{where}: direction must be one of {', '.join(FUNCTION_DIRECTIONS)}")
        if f"pmid:{pmid}" not in known:
            out.append(f"{where}: pmid:{pmid} is not registered (register_source.py pmid)")
            continue
        abstract = abstracts.get(pmid) or ""
        if not abstract:
            out.append(f"{where}: no cached abstract for pmid:{pmid} (register_source.py pmid)")
            continue
        if _norm(e["quote"]) not in _norm(abstract):
            out.append(f"{where}: the quote is not in the abstract of pmid:{pmid}")
        if not variant_pattern(e["variant"]).search(abstract):
            out.append(f"{where}: the abstract of pmid:{pmid} does not name {e['variant']}")
    return out


def functions_by_variant(entries: Any) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and e.get("variant"):
            out.setdefault(str(e["variant"]), []).append(e)
    return out


def _row_functions(
    r: Mapping[str, Any], functions: Mapping[str, Sequence[Mapping[str, Any]]]
) -> list[Mapping[str, Any]]:
    keys = [r.get("change"), *(a.get("hgvsc") for a in r.get("alleles") or [])]
    return [e for k in keys if k for e in functions.get(str(k), [])]


def _paper_direction(found: Sequence[Mapping[str, Any]]) -> str:
    dirs = {str(e.get("direction")) for e in found} - {"neutral"}
    return dirs.pop() if len(dirs) == 1 else "mixed" if dirs else "neutral"


def molecular_effect(
    r: Mapping[str, Any], functions: Mapping[str, Sequence[Mapping[str, Any]]]
) -> str:
    """The molecular-effect cell: what the records type (truncating consequence, Open
    Targets curated direction, UniProt's note, own QTL), then each functional study the
    writer recorded, each labelled by where it came from."""
    parts: list[str] = []
    if r.get("effect"):
        parts.append(f"{r['effect']} [records]")
    for e in _row_functions(r, functions):
        detail = ", ".join(str(x) for x in (e.get("system"), e.get("condition")) if x)
        parts.append(f"{e['direction']}: {e['readout']} ({detail}) [paper]")
    if r.get("uniprot_note"):
        parts.append(f"UniProt: {_short(r['uniprot_note'], 60)}")
    return "; ".join(parts)


def paper_tally(
    rows: Sequence[Mapping[str, Any]], functions: Mapping[str, Sequence[Mapping[str, Any]]]
) -> tuple[dict[str, int], list[str]]:
    """Directions the functional studies give the disease variants the records leave
    untyped, and the papers they come from."""
    counts: Counter[str] = Counter()
    pmids: list[str] = []
    for r in rows:
        if not r.get("disease_variant") or effect_class(r.get("effect") or "") != "not typed":
            continue
        found = _row_functions(r, functions)
        if found:
            counts[_paper_direction(found)] += 1
            pmids += [f"pmid:{str(e['pmid']).removeprefix('pmid:')}" for e in found]
    return dict(counts.most_common()), list(dict.fromkeys(pmids))


def allelic_series_md(
    data: Mapping[str, Any],
    available: set[str],
    functions: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> str:
    """The series, one property per column: what the variant does to the protein
    (molecular effect, by source), the curated classification and the condition it is for,
    the population association, and what the literature relates it to."""
    functions = functions or {}
    rows = list(data.get("rows") or [])[:MAX_ROWS]
    if not rows:
        return ""
    out = [
        "| Variant | Region | Consequence | Molecular effect | Clinical classification "
        "| Population / trait | Literature | Refs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        papers = [
            f"pmid:{str(e['pmid']).removeprefix('pmid:')}" for e in _row_functions(r, functions)
        ]
        # rows built before the columns were split: the old phenotype and evidence lists
        clinical = r.get("clinical", r.get("phenotypes") or [])
        literature = r.get("literature", r.get("origins") or [])
        out.append(
            "| "
            + " | ".join(
                _cell(x)
                for x in (
                    variant_name(r),
                    r.get("region"),
                    consequence_class(r["consequence"]) if r["consequence"] else "",
                    molecular_effect(r, functions),
                    "; ".join(clinical[:3]),
                    "; ".join((r.get("population") or [])[:2]),
                    "; ".join(literature[:2]),
                )
            )
            + f" | {_cite([*r['refs'], *papers], available)} |"
        )
    out.append("")
    out.append(alleles_caption(rows) + " [@allelic_series].")
    total = data.get("total") or len(rows)
    if total > len(rows):
        out.append("")
        out.append(f"Top {len(rows)} of {total} variants [@allelic_series].")
    tally = effect_tally(data.get("rows") or [])
    if tally:
        label = {"gain": "gain of function", "loss": "loss of function",
                 "truncating": "truncating (loss)"}  # fmt: skip
        parts = " · ".join(f"{label.get(k, k)} {n}" for k, n in tally.items())
        out.append("")
        line = (
            f"Effect on the target across all {sum(tally.values())} disease variants "
            f"(UniProt disease variants and ClinVar P/LP), typed from the records: {parts} "
            "[@allelic_series]."
        )
        typed, papers = paper_tally(data.get("rows") or [], functions)
        if typed:
            shown = " · ".join(f"{k} {n}" for k, n in typed.items())
            line += (
                f" Of those the records leave untyped, functional studies give: {shown} "
                f"{_cite(papers, available)}."
            )
        out.append(line)
    return "\n".join(out)


def variant_spectrum_md(data: Mapping[str, Any], available: set[str]) -> str:
    if not data:
        return ""
    by_class = data.get("plp_by_class") or {}
    refs = _cite([data.get("clinvar_key") or "gnomad_clinvar", "uniprot_extra"], available)
    lof_ref = _cite(["gnomad_lof"], available)
    out = [
        "| Class | ClinVar P/LP (≥1★, not conflicting) | of which ≥2★ | Refs |",
        "|---|---|---|---|",
    ]
    reviewed = data.get("plp_2star_by_class") or {}
    for k in _CLASSES:
        if k == "synonymous":
            continue
        out.append(f"| {k} | {by_class.get(k, 0)} | {reviewed.get(k, 0)} | {refs} |")
    if data.get("lof_hc_count") is not None:
        hom = data.get("lof_homozygotes") or 0
        out.append(
            f"| gnomAD high-confidence pLoF | {data['lof_hc_count']} variants | —"
            f" ({hom} homozygous carriers among those reported) | {lof_ref} |"
        )
    regions = data.get("missense_by_region") or {}
    if regions:
        out += ["", "| Missense P/LP by protein region | Variants | Refs |", "|---|---|---|"]
        out += [f"| {_cell(k)} | {n} | {refs} |" for k, n in regions.items()]
        if data.get("missense_outside_features"):
            out.append(
                f"| outside annotated features | {data['missense_outside_features']} | {refs} |"
            )
    return "\n".join(out)


# ── constraint ──────────────────────────────────────────────────────────────────────


def constraint_md(data: Mapping[str, Any], available: set[str]) -> str:
    """gnomAD constraint with the pipeline's band wording (one source for both), the
    release and transcript it was computed on, and what a population measure cannot say."""
    from services.evidence.constraint_interpret import (
        loeuf_band,
        misz_band,
        moeuf_band,
        pli_band,
    )

    if not data or data.get("loeuf") is None and data.get("pli") is None:
        return ""
    ref = _cite(["gnomad_constraint"], available)

    def num(v: Any, fmt: str) -> str:
        return format(v, fmt) if isinstance(v, int | float) else "—"

    rows = [
        ("LOEUF", num(data.get("loeuf"), ".2f"), loeuf_band(data["loeuf"]) if data.get("loeuf") is not None else ""),
        ("pLI", num(data.get("pli"), ".2g"), pli_band(data["pli"]) if data.get("pli") is not None else ""),
        ("Observed / expected pLoF", f"{num(data.get('obs_lof'), 'd')} / {num(data.get('exp_lof'), '.1f')}", ""),
        ("Missense Z", num(data.get("mis_z"), ".2f"), misz_band(data["mis_z"]) if data.get("mis_z") is not None else ""),
        ("Missense OEUF", num(data.get("moeuf"), ".2f"), moeuf_band(data["moeuf"]) if data.get("moeuf") is not None else ""),
    ]  # fmt: skip
    out = ["| Metric | Value | Reading | Refs |", "|---|---|---|---|"]
    out += [f"| {m} | {v} | {_cell(b)} | {ref} |" for m, v, b in rows]
    on = ", ".join(x for x in (data.get("dataset"), data.get("transcript")) if x)
    out += [
        "",
        (f"Computed by {on}. " if on else "")
        + "Population constraint measures depletion of variation in gnomAD; it does not establish "
        "whether a given variant is pathogenic, nor the effect of complete or biallelic loss, and "
        f"a gain-of-function disease gene can show no LoF depletion {ref}.",
    ]
    return "\n".join(out)


# ── pharmacovigilance ───────────────────────────────────────────────────────────────

# MedDRA terms about handling the product rather than a reaction to it (device use,
# dosing errors, storage, exposure): a large share of a self-injected drug's reports.
_PRODUCT_TERM_RE = re.compile(
    r"\b(device|product|technique|dose omission|dose administered|accidental|storage|"
    r"medication error|off label|incorrect|wrong|underdose|expired|no adverse event)\b",
    re.IGNORECASE,
)


def faers_md(data: Mapping[str, Any], retrieved: str, available: set[str]) -> str:
    """FAERS per approved drug as a signal-detection table: report counts, serious and
    fatal-outcome shares of the reports, the top reaction terms with product-handling terms
    apart, and a caption stating what spontaneous reports cannot show."""
    by_drug = data.get("by_drug") or {}
    none = list(data.get("no_faers_reports") or [])
    if not by_drug and not none:
        return ""
    ref = _cite(["faers"], available)
    out = [
        "| Drug | Reports | Serious (share of reports) | Fatal outcome (share of reports) "
        "| Top reaction terms | Product-handling terms | Refs |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, v in sorted(by_drug.items(), key=lambda kv: kv[0]):
        terms = v.get("top_reactions") or []
        reactions = [t for t in terms if not _PRODUCT_TERM_RE.search(t.get("reaction") or "")]
        handling = [t for t in terms if _PRODUCT_TERM_RE.search(t.get("reaction") or "")]

        def fmt(ts: list[Mapping[str, Any]]) -> str:
            return "; ".join(
                f"{str(t.get('reaction')).lower()} ({t.get('count'):,})" for t in ts[:4]
            )

        def share(n: Any, frac: Any) -> str:
            return f"{n:,} ({frac:.1%})" if isinstance(frac, int | float) else _cell(n)

        out.append(
            f"| {_cell(name.title())} | {v.get('total_reports', 0):,} | "
            f"{share(v.get('serious_reports'), v.get('serious_rate'))} | "
            f"{share(v.get('death_reports'), v.get('death_rate'))} | "
            f"{_cell(fmt(reactions))} | {_cell(fmt(handling))} | {ref} |"
        )
    query = 'openFDA drug/event, patient.drug.openfda.generic_name:"<drug>"'
    caption = (
        f"FAERS spontaneous reports ({query}; retrieved {retrieved[:10] or 'date not recorded'}). "
        "Reports are not deduplicated here and have no exposure denominator: counts and shares "
        "are not incidence, a report does not show the drug caused the event, and report "
        "volume does not rank drugs (it follows years on market, use and reporting). "
        "Disproportionality was not computed."
    )
    if none:
        caption += (
            f" No US FAERS reports: {', '.join(n.title() for n in none)} — approved elsewhere or "
            "not marketed in the US; not evidence of safety."
        )
    out += ["", caption + f" {ref}"]
    return "\n".join(out)


# ── structures ──────────────────────────────────────────────────────────────────────


def _resolution(text: str) -> float | None:
    m = re.match(r"\s*(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def _residues(chains: str) -> str:
    """'A/B/C/D=85-931' → '85–931'; several distinct ranges are joined."""
    ranges = re.findall(r"=(\d+)-(\d+)", chains or "")
    return ", ".join(dict.fromkeys(f"{a}–{b}" for a, b in ranges))


def structures_md(
    uniprot: Mapping[str, Any], alphafold: Mapping[str, Any], available: set[str]
) -> str:
    """Experimental structures (UniProt's PDB cross-references, best resolution first) and
    the AlphaFold model, each linked to its record."""
    pdb = sorted(
        uniprot.get("pdb") or [],
        key=lambda e: (_resolution(e.get("resolution") or "") or 99.0, e.get("id") or ""),
    )
    rows = pdb[:10]
    if not rows and not alphafold:
        return ""
    ref = _cite(["uniprot_extra"], available)
    out = ["| Structure | Method | Resolution | Residues | Refs |", "|---|---|---|---|---|"]
    for e in rows:
        pid = e.get("id") or ""
        res = _resolution(e.get("resolution") or "")
        out.append(
            f"| [{pid}](https://www.rcsb.org/structure/{pid}) | {_cell(e.get('method'))} | "
            f"{f'{res:.2f} Å' if res else '—'} | {_cell(_residues(e.get('chains') or ''))} | {ref} |"
        )
    if alphafold:
        acc = uniprot.get("accession") or ""
        length = uniprot.get("length")
        link = f"[AlphaFold {acc}](https://alphafold.ebi.ac.uk/entry/{acc})" if acc else "AlphaFold"
        out.append(
            f"| {link} | predicted | — | {f'1–{length}' if length else '—'} | "
            f"{_cite(['alphafold'], available)} |"
        )
    total = int(uniprot.get("pdb_count") or len(pdb))
    if total > len(rows):
        out += ["", f"Top {len(rows)} of {total} PDB entries by resolution {ref}."]
    return "\n".join(out)


# ── indication ──────────────────────────────────────────────────────────────────────

_ACTIVE_TRIAL = {
    "RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION",
}


def indication_md(
    records: Mapping[str, tuple[str, Any, str]], disease: str, available: set[str]
) -> str:
    """The gene × disease evidence in one table, from the indication-scoped records
    (``key -> (status, data, note)``): association, L2G, approved drugs and trials in the
    disease, the gene's trials and papers in it, prevalence. A record that was not checked
    says so; an empty one says nothing was found."""

    def row(label: str, key: str, value: Any) -> str:
        status, _, note = records.get(key, ("skipped", None, "not retrieved"))
        if status in ("skipped", "error"):
            return f"| {label} | not checked: {_cell(note or status)} | — |"
        text = value if status == "ok" and value else "none found"
        return f"| {label} | {_cell(text)} | {_cite([key], available) or '—'} |"

    def data(key: str) -> Any:
        status, d, _ = records.get(key, ("skipped", None, ""))
        return d if status == "ok" else None

    if not any(records.get(k, ("skipped",))[0] in ("ok", "empty") for k in records):
        return ""
    out = [f"| Evidence for {_cell(disease)} | Value | Refs |", "|---|---|---|"]

    ota = data("ot_association_disease") or {}
    if ota:
        types = sorted(
            ((k.removesuffix("_score").replace("_", " "), v) for k, v in ota.items()
             if k.endswith("_score") and k != "overall_score" and isinstance(v, int | float) and v > 0),
            key=lambda x: -x[1],
        )  # fmt: skip
        detail = "; ".join(f"{k} {v:.2f}" for k, v in types)
        ota_text = f"overall {float(ota.get('overall_score') or 0):.2f}" + (
            f" ({detail})" if detail else ""
        )
    else:
        ota_text = ""
    out.append(row("Open Targets gene–disease association", "ot_association_disease", ota_text))

    hits = (data("ot_l2g") or {}).get("hits") or []
    best = max(hits, key=lambda h: h.get("l2g_score") or 0, default=None)
    l2g_text = (
        f"{len(hits)} GWAS loci; best L2G {best.get('l2g_score', 0):.2f} ({best.get('trait')})"
        if best
        else ""
    )
    out.append(row("Locus-to-gene (GWAS L2G)", "ot_l2g", l2g_text))

    fda = data("openfda_indication") or {}
    drugs = fda.get("drugs") or []
    fda_text = (
        f"{fda.get('approved_drug_count', len(drugs))} ({', '.join(drugs[:5])}"
        + (f", … and {len(drugs) - 5} more" if len(drugs) > 5 else "")
        + ")"
        if drugs
        else ""
    )
    out.append(row("Approved drugs for the disease (FDA labels)", "openfda_indication", fda_text))

    ct = data("ct_condition") or {}
    ct_text = (
        f"{ct.get('total_count')} total; {ct.get('active_count')} active; "
        f"{ct.get('phase3_count')} in phase 3"
        if ct.get("total_count")
        else ""
    )
    out.append(row("Trials in the disease (ClinicalTrials.gov)", "ct_condition", ct_text))

    gene_trials = data("ct_gene_disease")
    gene_trials = (
        gene_trials if isinstance(gene_trials, list) else (gene_trials or {}).get("trials") or []
    )
    active = sum(1 for t in gene_trials if str(t.get("status") or "").upper() in _ACTIVE_TRIAL)
    out.append(
        row("Trials naming the gene and the disease", "ct_gene_disease",
            f"{len(gene_trials)} ({active} active)" if gene_trials else "")
    )  # fmt: skip

    papers = (data("pubmed_disease") or {}).get("total")
    out.append(row("Papers naming the gene and the disease (PubMed)", "pubmed_disease",
                   str(papers) if papers else ""))  # fmt: skip

    prev = [
        f"{r.get('disorder_name')}: {r.get('prevalence_class')} ({r.get('prevalence_type')}, "
        f"{r.get('geographic_area')})"
        for r in (data("orphanet_prevalence") or {}).get("records") or []
    ]
    out.append(row("Prevalence (Orphanet)", "orphanet_prevalence", "; ".join(prev[:3])))
    gbd = data("gbd")
    out.append(row("Burden (GBD)", "gbd", (gbd or {}).get("text") if isinstance(gbd, dict) else ""))
    return "\n".join(out)
