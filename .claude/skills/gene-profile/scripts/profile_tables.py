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
  verbatim, never typed;
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
) -> dict[str, Any]:
    """ClinVar P/LP by consequence class and missense P/LP by protein region; gnomAD
    high-confidence pLoF carriers."""
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


def _effect(effects: Sequence[Mapping[str, Any]], consequence: str) -> tuple[str, str]:
    """(effect text, source) for the variant: truncating, else Open Targets curated, else
    the variant's own QTL for the alternate allele."""
    from services.evidence.variant_effect import curated_change, qtl_change

    if consequence_class(consequence) == "truncating":
        return "loss (truncating)", "consequence"
    for e in effects:
        data = _Obj(e)
        change = curated_change(data)
        if change is not None:
            word = {"decrease": "loss of function", "increase": "gain of function"}[change]
            return f"{word} (alt {e.get('alt') or '?'}, curated)", "curated"
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


def allelic_series(
    *,
    uniprot: Mapping[str, Any],
    clinvar: Mapping[str, Any],
    lof: Mapping[str, Any],
    gwas: Mapping[str, Any],
    literature: Mapping[str, Any],
    effects: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """One row per variant, joined on protein change or rs id; the rows most informative
    for direction first (disease variants, then the most studied)."""
    features = uniprot.get("features") or []
    disease_names = {
        d.get("acronym"): d.get("name") for d in uniprot.get("diseases") or [] if d.get("acronym")
    }
    rows: dict[str, dict[str, Any]] = {}
    by_rsid: dict[str, str] = {}

    def row(key: str, rsid: str = "") -> dict[str, Any]:
        if rsid and rsid in by_rsid:
            key = by_rsid[rsid]
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
            },
        )
        if rsid:
            r["rsid"] = rsid
            by_rsid[rsid] = key
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
            r["disease_variant"] = True
        r["refs"] += ["uniprot_extra", *(f"pmid:{p}" for p in (v.get("pmids") or [])[:2])]

    for v in plp_variants(clinvar):
        c = protein_change(v.get("hgvsp") or "")
        if c is None or consequence_class(v.get("major_consequence") or "") == "synonymous":
            continue
        key = f"{c[0]}{c[1]}{c[2]}"
        r = row(key)
        r["position"] = c[1]
        r["consequence"] = v.get("major_consequence") or r["consequence"]
        stars = v.get("gold_stars") or 0
        r["origins"].append(f"ClinVar {v.get('clinical_significance')} ({stars}★)")
        r["disease_variant"] = True
        r["refs"].append("gnomad_clinvar")

    for v in (lof.get("reported_variants") or [])[:5]:
        c = protein_change(v.get("hgvsp") or "")
        if c is None:
            continue
        r = row(f"{c[0]}{c[1]}{c[2]}")
        r["position"] = c[1]
        r["consequence"] = v.get("consequence") or r["consequence"]
        hom = v.get("homozygote_count") or 0
        r["origins"].append(
            f"gnomAD pLoF, {v.get('ac')} carriers" + (f", {hom} homozygous" if hom else "")
        )
        r["refs"].append("gnomad_lof")

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
        diseases = [x for x in v.get("related") or [] if x.get("kind") == "disease"]
        for rel in diseases[:1]:
            kinds = ", ".join(rel.get("relations") or {})
            r["phenotypes"].append(f"{rel['name']} ({kinds}, {rel['papers']} papers)")
        r["refs"] += [f"pmid:{p}" for p in (v.get("pmids") or [])[:2]] + ["pubtator_map"]

    for h in gwas.get("hits") or []:
        rsid = str(h.get("rs_id") or "").lower()
        if not rsid:
            continue
        r = row(rsid, rsid)
        if "GWAS" not in " ".join(r["origins"]):
            r["origins"].append("GWAS lead")
            r["phenotypes"].append(f"{h.get('trait')} (GWAS)")
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
        r["effect"], r["effect_source"] = _effect(found, r["consequence"])
        if r["effect_source"] in ("curated", "qtl"):
            r["refs"].append("allelic_series")
        r["region"] = region_of(r["position"], features) if r["position"] else ""
        r["refs"] = list(dict.fromkeys(r["refs"]))

    ordered = sorted(
        rows.values(),
        key=lambda r: (-evidence_weight(r), -r["literature_papers"], r["position"] or 10**9),
    )
    return {"total": len(ordered), "rows": ordered}


def evidence_weight(r: Mapping[str, Any]) -> int:
    """How much a row tells a reader about the variant's effect: curated disease variants
    with a functional note and multi-star ClinVar calls first, a single submitter's call
    last among the disease variants."""
    stars = max(
        (int(m.group(1)) for o in r["origins"] if (m := re.search(r"\((\d)★\)", o))), default=0
    )
    weight = 0
    if "UniProt" in r["origins"] and r["disease_variant"]:
        weight += 4 + (1 if r["uniprot_note"] else 0)
    weight += 3 if stars >= 2 else 1 if stars == 1 else 0
    weight += min(3, r["literature_papers"] // 3)
    weight += 1 if r["effect_source"] in ("curated", "qtl") else 0
    weight += 1 if any(o == "GWAS lead" for o in r["origins"]) else 0
    return weight


# ── markdown ────────────────────────────────────────────────────────────────────────


def _cell(text: Any) -> str:
    return str(text or "—").replace("|", "/").replace("\n", " ")


def _short(text: str, n: int = _NOTE_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _cite(keys: Iterable[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


def allelic_series_md(data: Mapping[str, Any], available: set[str]) -> str:
    rows = list(data.get("rows") or [])[:MAX_ROWS]
    if not rows:
        return ""
    out = [
        "| Variant | Region | Consequence | Effect on the target | UniProt note | Phenotype / trait | Evidence | Refs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        name = r["change"] or r["rsid"]
        if r["change"] and r["rsid"]:
            name = f"{r['change']} ({r['rsid']})"
        out.append(
            "| "
            + " | ".join(
                _cell(x)
                for x in (
                    name,
                    r.get("region"),
                    consequence_class(r["consequence"]) if r["consequence"] else "",
                    r.get("effect"),
                    _short(r.get("uniprot_note") or ""),
                    "; ".join(r["phenotypes"][:2]),
                    "; ".join(r["origins"][:3]),
                )
            )
            + f" | {_cite(r['refs'], available)} |"
        )
    total = data.get("total") or len(rows)
    if total > len(rows):
        out.append("")
        out.append(f"Top {len(rows)} of {total} variants [@allelic_series].")
    return "\n".join(out)


def variant_spectrum_md(data: Mapping[str, Any], available: set[str]) -> str:
    if not data:
        return ""
    by_class = data.get("plp_by_class") or {}
    refs = _cite(["gnomad_clinvar", "uniprot_extra"], available)
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
