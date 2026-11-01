# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The somatic mutation pattern of a gene in the TCGA PanCancer Atlas (cBioPortal records).

Deterministic, no LLM. Per gene: the share of sequenced tumours mutated, overall and per
cancer type; the share of mutations that are missense at recurrent residues, naming them; the
share that are truncating; amplification and deep-deletion rates.

A residue is recurrent when its missense count is improbable under a uniform spread of the
gene's missense mutations over its protein length: a one-sided binomial test, Bonferroni-
corrected for the number of residues. The bar scales with gene length and mutation count, so
a long gene collecting passenger mutations does not pass it by chance, and a small cohort is
not held to a fixed count.

Pattern labels follow the published 20/20 rule (Vogelstein et al., Science 2013;
339:1546–58): "oncogene-like" when ≥ 20% of mutations are missense at recurrent residues,
"tumour-suppressor-like" when ≥ 20% are truncating, "no driver pattern" otherwise, whatever
the mutated share. Fewer than ``MIN_MUTATIONS`` mutations: "too few mutations to classify".
The label describes the TCGA mutation pattern; it is not a curated driver call.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

MIN_MUTATIONS = 20
RULE_SHARE = 0.20  # Vogelstein et al. 2013, the 20/20 rule
ALPHA = 0.05
TRUNCATING = {
    "Nonsense_Mutation", "Frame_Shift_Del", "Frame_Shift_Ins", "Splice_Site",
    "Nonstop_Mutation", "Translation_Start_Site",
}  # fmt: skip
MISSENSE = "Missense_Mutation"


def binomial_sf(k: int, n: int, p: float) -> float:
    """P(X ≥ k) for X ~ Binomial(n, p), summed in log space."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0

    def term(i: int) -> float:
        return math.exp(
            math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p)
        )  # fmt: skip

    if k < n / 2:
        return max(0.0, 1.0 - sum(term(i) for i in range(k)))
    return min(1.0, sum(term(i) for i in range(k, n + 1)))


def recurrent_residues(missense: Sequence[int], length: int) -> dict[int, int]:
    """Residues whose missense count is improbable under a uniform spread over ``length``
    residues (binomial, Bonferroni over the residues), with their counts."""
    counts = Counter(p for p in missense if p and 1 <= p <= length)
    n = sum(counts.values())
    if not n or not length:
        return {}
    bar = ALPHA / length
    return {
        pos: k for pos, k in counts.most_common() if k >= 2 and binomial_sf(k, n, 1 / length) < bar
    }


def somatic_pattern(data: Mapping[str, Any], length: int) -> dict[str, Any]:
    """The pattern of one gene from the cBioPortal bundle (``mcp_servers.cbioportal``)."""
    studies = [s for s in data.get("studies") or [] if not s.get("error")]
    sequenced = sum(int(s.get("sequenced_samples") or 0) for s in studies)
    cna = sum(int(s.get("cna_samples") or 0) for s in studies)
    muts = [m for s in studies for m in s.get("mutations") or []]
    mutated = {m["sample_id"] for m in muts}
    missense = [m.get("position") for m in muts if m.get("mutation_type") == MISSENSE]
    recurrent = recurrent_residues([p for p in missense if p], length)
    at_recurrent = sum(
        1 for m in muts if m.get("mutation_type") == MISSENSE and m.get("position") in recurrent
    )
    truncating = sum(1 for m in muts if m.get("mutation_type") in TRUNCATING)
    n = len(muts)
    hotspot_share = at_recurrent / n if n else 0.0
    trunc_share = truncating / n if n else 0.0
    if n < MIN_MUTATIONS:
        label = "too few mutations to classify"
    elif hotspot_share >= RULE_SHARE and trunc_share >= RULE_SHARE:
        label = "oncogene-like and tumour-suppressor-like"
    elif hotspot_share >= RULE_SHARE:
        label = "oncogene-like"
    elif trunc_share >= RULE_SHARE:
        label = "tumour-suppressor-like"
    else:
        label = "no driver pattern"
    names: dict[int, str] = {}
    for m in muts:
        pos = m.get("position")
        change = str(m.get("protein_change") or "")
        if pos in recurrent and pos not in names and change[:1].isalpha():
            names[pos] = f"{change[0]}{pos}"  # "E545K" -> "E545"
    by_type = []
    for s in studies:
        seq = int(s.get("sequenced_samples") or 0)
        hit = {m["sample_id"] for m in s.get("mutations") or []}
        by_type.append(
            {
                "study": s.get("study_id"),
                "name": str(s.get("name") or "").removesuffix(" (TCGA, PanCancer Atlas)"),
                "cancer_type": s.get("cancer_type"),
                "sequenced": seq,
                "mutated": len(hit),
                "share": len(hit) / seq if seq else 0.0,
                "amplified": len(s.get("amplified_samples") or []),
                "deep_deleted": len(s.get("deep_deleted_samples") or []),
                "cna_samples": int(s.get("cna_samples") or 0),
            }
        )
    return {
        "studies": len(studies),
        "failed_studies": [s.get("study_id") for s in data.get("studies") or [] if s.get("error")],
        "sequenced": sequenced,
        "mutations": n,
        "mutated_samples": len(mutated),
        "mutated_share": len(mutated) / sequenced if sequenced else 0.0,
        "missense": len(missense),
        "hotspot_share": hotspot_share,
        "truncating_share": trunc_share,
        "recurrent": {names.get(p, str(p)): k for p, k in recurrent.items()},
        "recurrent_positions": recurrent,
        "recurrent_names": {p: names.get(p, str(p)) for p in recurrent},
        "amplified_share": sum(len(s.get("amplified_samples") or []) for s in studies) / cna
        if cna
        else 0.0,
        "deep_deleted_share": sum(len(s.get("deep_deleted_samples") or []) for s in studies) / cna
        if cna
        else 0.0,
        "label": label,
        "by_type": sorted(by_type, key=lambda r: (-r["share"], r["name"])),
    }


def residue_counts(data: Mapping[str, Any]) -> dict[int, int]:
    """Somatic mutations per residue (every mutation with a protein position)."""
    return dict(
        Counter(
            m["position"]
            for s in data.get("studies") or []
            for m in s.get("mutations") or []
            if m.get("position")
        )
    )


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _cite(keys: Sequence[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


MIN_TYPE_SAMPLES = 50
TOP_TYPES = 5


def somatic_md(data: Mapping[str, Any], length: int, available: set[str]) -> str:
    """Is it a cancer gene, in which cancers, gain or loss: the pattern label with the
    shares it rests on, the cancer types with the highest mutated share, recurrent residues,
    and copy-number rates; or the explicit "no event" sentence."""
    if not data.get("studies"):
        return ""
    s = somatic_pattern(data, length)
    ref = _cite(["somatic"], available)
    scope = f"{s['studies']} TCGA PanCancer Atlas studies, {s['sequenced']:,} sequenced tumours" + (
        f"; not checked: {', '.join(s['failed_studies'])}" if s["failed_studies"] else ""
    )
    if not s["mutations"] and not s["amplified_share"] and not s["deep_deleted_share"]:
        return f"*No mutation or copy-number event in the {scope}.* {ref}".rstrip()
    recurrent = ", ".join(f"{k} ({v})" for k, v in list(s["recurrent"].items())[:8]) or "none"
    types = [t for t in s["by_type"] if t["sequenced"] >= MIN_TYPE_SAMPLES and t["mutated"]]
    top = "; ".join(f"{t['name']} {_pct(t['share'])}" for t in types[:TOP_TYPES]) or "—"
    rows = [
        ("Pattern (20/20 rule)", s["label"]),
        ("Tumours mutated", f"{s['mutated_samples']:,} of {s['sequenced']:,} ({_pct(s['mutated_share'])})"),
        ("Highest mutated share by cancer type", top),
        ("Missense at recurrent residues", f"{_pct(s['hotspot_share'])} of {s['mutations']:,} mutations; residues: {recurrent}"),
        ("Truncating", f"{_pct(s['truncating_share'])} of mutations"),
        ("Amplified / deep deletion (GISTIC)", f"{_pct(s['amplified_share'])} / {_pct(s['deep_deleted_share'])} of tumours with copy number"),
    ]  # fmt: skip
    out = ["| Somatic evidence | TCGA PanCancer Atlas | Refs |", "|---|---|---|"]
    out += [f"| {a} | {b.replace('|', '/')} | {ref} |" for a, b in rows]
    out += [
        "",
        f"Scope: {scope}. A residue is recurrent when its missense count is improbable under a "
        "uniform spread over the protein (binomial, Bonferroni-corrected); labels follow the "
        "20/20 rule (Vogelstein et al. 2013): ≥ 20% missense at recurrent residues is "
        "oncogene-like, ≥ 20% truncating tumour-suppressor-like, and a high mutated share alone "
        f"is not a driver pattern (long genes collect passengers). Fewer than {MIN_MUTATIONS} "
        "mutations are not classified. The label describes the mutation pattern, not a curated "
        "driver call; TCGA tumours are primary and mostly untreated, so mutations acquired under "
        f"therapy are under-represented, and rare cancer types are absent {ref}.",
    ]
    return "\n".join(out)
