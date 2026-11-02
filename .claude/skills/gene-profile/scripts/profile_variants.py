# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Variant harmonisation: one record per allelic-series variant, joining its alleles and
gnomAD frequencies, its GWAS associations with the effect allele each was reported for, and
what the variant does to the protein.

Deterministic, no LLM. An association's direction is expressed for the variant's own (alt)
allele: an effect reported for the reference allele is flipped (beta sign, OR inverted,
increase ↔ decrease). When the variant's molecular effect is typed (gain or loss of
function), the direction reads "per loss-of-function allele"; otherwise it stays "per
effect allele", marked that the allele's function is not typed. An association whose
effect allele the catalogue does not record is kept, unharmonised.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

_FLIP = {"increase": "decrease", "decrease": "increase"}
_FUNCTION_WORDS = {
    "gain": "gain-of-function",
    "loss": "loss-of-function",
    "truncating": "loss-of-function",
    "dominant negative": "dominant-negative",
    "higher expression": "higher-expression",
    "lower expression": "lower-expression",
}


def effect_class(effect: str) -> str:
    """gain / loss / truncating / mixed / "" (not typed) from a series row's effect text."""
    from profile_tables import effect_class as series_class

    kind = series_class(effect or "")
    return "" if kind in ("not typed", "") else kind


def harmonise_association(
    hit: Mapping[str, Any], ref: str, alts: Sequence[str], function: str
) -> dict[str, Any]:
    """One GWAS association expressed for the variant's alt allele where the effect allele
    allows it (``harmonised``: alt, ref-flipped, or unknown)."""
    allele = str(hit.get("effect_allele") or "")
    beta, direction = hit.get("beta_num"), hit.get("beta_direction")
    odds = hit.get("or_per_copy")
    lo, hi = hit.get("ci_lower"), hit.get("ci_upper")
    if allele and allele in alts:
        harmonised, flipped = "alt", False
    elif allele and allele == ref:
        harmonised, flipped = "ref (flipped)", True
    else:
        harmonised, flipped = "unknown", False
    if flipped:
        direction = _FLIP.get(direction or "", direction)
        if odds:
            odds = 1 / odds
            lo, hi = (1 / hi if hi else None), (1 / lo if lo else None)
    if odds and not direction:
        direction = "increase" if odds > 1 else "decrease" if odds < 1 else None
    if harmonised != "unknown" and function in _FUNCTION_WORDS:
        per = f"per {_FUNCTION_WORDS[function]} allele {alts[0] if len(alts) == 1 else '/'.join(alts)}"
    elif harmonised != "unknown":
        per = f"per alt allele {'/'.join(alts)} (function of the allele not typed)"
    elif allele:
        per = f"per effect allele {allele} (not matched to the variant's alleles)"
    else:
        per = "effect allele not recorded"
    p = None
    if hit.get("pvalue_mantissa"):
        p = f"{hit['pvalue_mantissa']}e{hit.get('pvalue_exponent')}"
    return {
        "trait": hit.get("trait", ""),
        "efo_id": hit.get("efo_id", ""),
        "study": hit.get("study_accession", ""),
        "pmid": hit.get("pmid", ""),
        "effect_allele": allele or None,
        "harmonised": harmonised,
        "beta": beta,
        "beta_unit": hit.get("beta_unit"),
        "odds_ratio": round(odds, 4) if odds else None,
        "ci": [lo, hi] if lo is not None and hi is not None else None,
        "direction": direction,
        "p": p,
        "log10_p": (math.log10(hit["pvalue_mantissa"]) + int(hit.get("pvalue_exponent") or 0))
        if hit.get("pvalue_mantissa")
        else None,
        "per": per,
    }


def harmonise(
    rows: Sequence[Mapping[str, Any]],
    gwas_hits: Sequence[Mapping[str, Any]],
    frequencies: Mapping[str, Any],
    paper_direction: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One record per series row with an rs id or allele ids (top rows, as the series
    table): alleles with gnomAD counts, molecular effect (records, else the writer's
    functional studies), and its GWAS associations harmonised to the alt allele."""
    paper_direction = paper_direction or {}
    by_rsid: dict[str, list[Mapping[str, Any]]] = {}
    for h in gwas_hits:
        if h.get("rs_id"):
            by_rsid.setdefault(str(h["rs_id"]).lower(), []).append(h)
    alleles_of = frequencies.get("by_variant") or {}
    counts = frequencies.get("alleles") or {}
    failed = set(frequencies.get("failed") or [])
    out = []
    for r in rows:
        key = r.get("change") or r.get("rsid") or ""
        rsid = str(r.get("rsid") or "").lower()
        ids = list(alleles_of.get(key) or [a["id"] for a in r.get("alleles") or [] if a.get("id")])
        if not ids and not rsid:
            continue
        alleles = [
            counts.get(i) or {"variant_id": i, "found": False, "failed": i in failed} for i in ids
        ]
        parts = [i.split("-") for i in ids if len(i.split("-")) == 4]
        ref = parts[0][2] if parts else ""
        alts = [p[3] for p in parts]
        function = effect_class(r.get("effect") or "")
        source = "records" if function else ""
        if not function and paper_direction.get(r.get("change") or ""):
            function, source = paper_direction[r["change"]], "paper"
        out.append(
            {
                "variant": key,
                "change": r.get("change") or "",
                "rsid": rsid,
                "position": r.get("position"),
                "consequence": r.get("consequence") or "",
                "disease_variant": bool(r.get("disease_variant")),
                "ref": ref,
                "alts": alts,
                "alleles": alleles,
                "function": function,
                "function_source": source,
                "associations": [
                    harmonise_association(h, ref, alts, function) for h in by_rsid.get(rsid, [])
                ],
            }
        )
    return out


def coverage(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    assoc = [a for r in records for a in r["associations"]]
    return {
        "variants": len(records),
        "typed": sum(1 for r in records if r["function"]),
        "associations": len(assoc),
        "allele_known": sum(1 for a in assoc if a["effect_allele"]),
        "harmonised": sum(1 for a in assoc if a["harmonised"] != "unknown"),
        "with_ci": sum(1 for a in assoc if a["ci"]),
        "in_gnomad": sum(1 for r in records if any(x.get("found") for x in r["alleles"])),
    }


# ── table ───────────────────────────────────────────────────────────────────────────

MAX_VARIANTS = 10


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ").strip()


def _cite(keys: Sequence[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


def _af(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:.2g}" if x >= 0.001 else f"{x:.1e}"


def _estimate(a: Mapping[str, Any]) -> str:
    if a.get("odds_ratio"):
        value = f"OR {a['odds_ratio']:.3g}"
    elif a.get("beta") is not None:
        value = f"β {a['beta']:.3g}" + (f" {a['beta_unit']}" if a.get("beta_unit") else "")
        value += f" {a['direction']}" if a.get("direction") else ""
    else:
        return "no estimate recorded"
    if a.get("ci"):
        value += f" [{a['ci'][0]:.3g}–{a['ci'][1]:.3g}]"
    return value


def lead_association(assoc: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The association a row leads with: the most significant one that is harmonised and
    has an estimate, else the most significant with an estimate, else the most significant."""

    def p(a: Mapping[str, Any]) -> float:
        return a["log10_p"] if a.get("log10_p") is not None else 0.0

    def estimated(a: Mapping[str, Any]) -> bool:
        return a.get("odds_ratio") is not None or a.get("beta") is not None

    for pool in (
        [a for a in assoc if estimated(a) and a["harmonised"] != "unknown"],
        [a for a in assoc if estimated(a)],
        list(assoc),
    ):
        if pool:
            return min(pool, key=p)
    return None


def frequency_text(alleles: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """("AF overall (lowest–highest ancestry)", "homozygotes") across a variant's alleles."""
    found = [a for a in alleles if a.get("found")]
    if not found:
        unchecked = any(a.get("failed") for a in alleles)
        return ("Not checked: lookup failed" if unchecked else "Not in gnomAD"), "—"
    parts, hom = [], 0
    for a in found:
        pops = {k: v for k, v in (a.get("populations") or {}).items() if (v.get("an") or 0) >= 2000}
        span = ""
        if pops:
            lo = min(pops, key=lambda k: pops[k].get("af") or 0)
            hi = max(pops, key=lambda k: pops[k].get("af") or 0)
            span = f" ({lo} {_af(pops[lo].get('af'))} – {hi} {_af(pops[hi].get('af'))})"
        label = f"{a.get('alt')}: " if len(found) > 1 else ""
        parts.append(f"{label}{_af(a.get('af'))}{span}")
        hom += int(a.get("homozygote_count") or 0)
    return "; ".join(parts), str(hom)


def variant_effects_md(
    records: Sequence[Mapping[str, Any]],
    available: set[str],
    stated: Mapping[str, Any] | None = None,
) -> str:
    """Per key variant: alleles, molecular effect, the strongest harmonised GWAS association
    and how many there are, gnomAD frequency across ancestries and homozygotes; with
    ``stated`` (profile_effects), the effect sizes the abstracts state."""
    shown = [r for r in records if r["associations"] or any(a.get("found") for a in r["alleles"])]
    if not shown:
        return ""
    stated = stated or {}
    out = [
        "| Variant | Alleles | Molecular effect | Strongest GWAS association | GWAS associations "
        "| Stated in abstracts | gnomAD AF (lowest – highest ancestry) | Homozygotes | Refs |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in shown[:MAX_VARIANTS]:
        assoc = r["associations"]
        lead = lead_association(assoc)
        lead_text = (
            f"{lead['trait']}: {_estimate(lead)}, p {lead['p']}; {lead['per']}" if lead else "—"
        )
        n_known = sum(1 for a in assoc if a["effect_allele"])
        count = f"{len(assoc)} ({n_known} with effect allele)" if assoc else "0"
        af, hom = frequency_text(r["alleles"])
        alleles = f"{r['ref']}>{'/'.join(r['alts'])}" if r["ref"] else "—"
        name = r["change"] + (
            f" ({r['rsid']})" if r["rsid"] and r["change"] else r["rsid"] if not r["change"] else ""
        )
        function = r["function"] or "not typed"
        if r["function_source"] == "paper":
            function += " [paper]"
        s = stated.get(r["variant"]) or {}
        stated_text = (
            f"{s['estimates']} estimate(s) in {s['papers']} paper(s)" if s.get("estimates") else "—"
        )
        refs = ["allelic_series", *(["gwas"] if assoc else []), "variant_frequencies"]
        out.append(
            f"| {_cell(name)} | {alleles} | {function} | {_cell(lead_text)} | {count} "
            f"| {stated_text} | {_cell(af)} | {hom} | {_cite(refs, available)} |"
        )
    c = coverage(records)
    out += [
        "",
        f"Effect allele known for {c['allele_known']} of {c['associations']} GWAS associations of "
        f"these variants ({c['harmonised']} matched to the variant's alleles, {c['with_ci']} with a "
        f"CI); molecular effect typed for {c['typed']} of {c['variants']} variants; "
        f"{c['in_gnomad']} found in gnomAD. Directions are for the variant's alt allele; an "
        "effect reported for the reference allele is flipped "
        + _cite(["gwas", "variant_frequencies"], available)
        + ".",
    ]
    return "\n".join(out)


# ── phenome ─────────────────────────────────────────────────────────────────────────


def trait_groups(
    efo_ids: Sequence[str], ontology: Mapping[str, Mapping[str, Any]]
) -> dict[str, str]:
    """A display group per trait from the EFO grouping: its therapeutic area, or for a
    measurement (most GWAS traits) "measurement: <parent>", the parent shared by the most of
    the gene's measurement traits (ties alphabetical) so related traits fall together; traits
    Open Targets does not hold are "other traits"."""
    from collections import Counter

    measured = [i for i in efo_ids if (ontology.get(i) or {}).get("areas") == ["measurement"]]
    share = Counter(p for i in set(measured) for p in (ontology.get(i) or {}).get("parents") or [])
    out = {}
    for i in efo_ids:
        entry = ontology.get(i) or {}
        areas = list(entry.get("areas") or [])
        if not areas:
            out[i] = "other traits"
        elif areas == ["measurement"]:
            parents = sorted(entry.get("parents") or [], key=lambda p: (-share[p], p))
            parents = [x for x in parents if x != "measurement"]  # the root names nothing
            out[i] = f"measurement: {parents[0]}" if parents else "measurement"
        else:
            # a disease in several areas: the area most specific to it (not the catch-alls)
            specific = [a for a in areas if a not in ("phenotype", "measurement")] or areas
            out[i] = specific[0]
    return out


def phenome_points(
    hits: Sequence[Mapping[str, Any]], support: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """One point per GWAS association: its group, −log10 p, the direction for the effect
    allele where recorded, and whether Open Targets ties it to this gene (L2G or
    colocalisation) or it is mapped by position only."""
    import math

    groups = trait_groups(
        [h.get("efo_id", "") for h in hits], support.get("therapeutic_areas") or {}
    )
    assoc = support.get("associations") or {}
    out = []
    for h in hits:
        if not h.get("pvalue_mantissa"):
            continue
        s = assoc.get(f"{h.get('study_accession')}|{str(h.get('rs_id') or '').lower()}") or {}
        direction = h.get("beta_direction")
        if not direction and h.get("or_per_copy"):
            direction = "increase" if h["or_per_copy"] > 1 else "decrease"
        out.append(
            {
                "group": groups.get(h.get("efo_id", ""), "other traits"),
                "trait": h.get("trait", ""),
                "mlog10p": -(math.log10(h["pvalue_mantissa"]) + int(h.get("pvalue_exponent") or 0)),
                "direction": direction if h.get("effect_allele") else None,
                "supported": bool(s.get("supported")),
            }
        )
    return out
