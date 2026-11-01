# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Effect sizes the abstracts state for the gene's key variants, parsed verbatim.

Deterministic, no LLM. Reads the PubTator documents of the gene's grounded papers
(profile_literature) and, for each key variant of the allelic series:

* Selection — a passage mentions the variant by, in order of preference: a PubTator variant
  tag with its rs id; its alleles as recorded (the protein change in the spellings
  ``profile_tables.variant_pattern`` derives from the record, or an HGVS c. name);
  PubTator's VariantGroup coreference with such a mention; the gene named with an allele
  word, in a paper with no other variant of the gene; a genotype, in a paper whose only
  tagged variant of the gene is this one. Each estimate records its rule.
* Attribution — the nearest preceding variant or gene mention owns an estimate, or the
  matching one under "respectively"; a mention naming a stratum ("in carriers of …") is
  skipped; an estimate between which and its owner a combined score is named is not the
  variant's. An estimate no mention of this variant owns is excluded and counted.
* Numbers — OR / HR / RR (per-allele forms flagged), the CI and p from the verbatim
  sentence. Significance is computed from the CI (excludes 1) or else p < 0.05. A CI that
  does not contain its estimate is flagged and not used.
* Outcome — the PubTator disease tag the estimate refers to (its MeSH id), skipping a
  disease that names a stratum ("in patients with …"); an untagged outcome keeps the
  sentence.

Every pattern is generic; none names a gene, variant or disease. Abstracts are English and
follow reporting conventions only loosely, so every result carries the sentence it came
from and the counts of what was excluded.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# ── patterns (one fixture each in tests/gene_profile/test_profile_research.py) ───────

_NUM = r"\d+(?:\.\d+)?"
MEASURE_RE = re.compile(
    # abbreviations are upper case ("or" and genotypes like "RR" are not ratios); the
    # spelled-out forms any case
    r"(?P<measure>\b(?:(?-i:a?ORs?(?:allelic|allele)?|allelic OR|a?HRs?|RRs?)|odds ratios?"
    r"|hazard ratios?|relative risks?|risk ratios?))\b"
    # between the measure and its number only connectors: "(OR)", "[HR]:", "of", "=",
    # "(95% CI):", "per allele"
    r"(?P<qual>(?:\s|[,:=]|\((?:(?-i:a?OR|a?HR|RR)|95\s?%\s*CI)\)|\[(?-i:a?OR|a?HR|RR)\]"
    r"|\b(?:of|was|were|is)\b|95\s?%\s*CI|per[- ](?:allele|copy)){0,8}?)"
    rf"(?<![\d.])(?P<value>{_NUM})(?![\d.]|\s?%)"
    rf"(?:\s*[\[(,;]?\s*(?:95\s?%\s*)?(?:CI|confidence interval)?\s*(?:\[CI\]|\(CI\))?\s*[:=,]?\s*"
    rf"(?P<lo>{_NUM})\s*(?:-|–|—|to|,)\s*(?P<hi>{_NUM}))?",
    re.IGNORECASE,
)
P_RE = re.compile(
    r"\b[Pp]\s*(?:[-‐]?\s*values?)?\s*(?P<op>[=<≤])\s*"
    r"(?P<p>\d*\.?\d+(?:\s*[x×]\s*10\s*[-−–]\s*\d+|[eE]\s*[-−–]\s*\d+)?)"
)
PER_ALLELE_RE = re.compile(r"per[- ](?:allele|copy)|allelic|additive", re.IGNORECASE)
RESPECTIVELY_RE = re.compile(r"\brespectively\b", re.IGNORECASE)
COMBINED_RE = re.compile(
    r"\b(?:risk scores?|polygenic|genetic scores?|combined|haplotypes?|interaction)\b",
    re.IGNORECASE,
)
STRATUM_BEFORE_RE = re.compile(
    r"(?:\b(?:in|among|within)\s+(?:the\s+)?(?:\w+\s+){0,2}"
    r"(?:carriers|non-?carriers|homozygotes|heterozygotes|patients|subjects|individuals|people)"
    r"\s+(?:of|with|carrying)\s+(?:the\s+)?)$",
    re.IGNORECASE,
)
DISEASE_STRATUM_BEFORE_RE = re.compile(
    r"\b(?:in|among|within)\s+(?:\w+\s+){0,2}(?:patients|subjects|individuals|people|those|participants)?"
    r"\s*(?:with|without|having)\s+(?:\w+\s+){0,2}$",
    re.IGNORECASE,
)
DISEASE_STRATUM_AFTER_RE = re.compile(
    r"^\s*(?:patients|subjects|cases|individuals)\b", re.IGNORECASE
)
ALLELE_WORD_RE = re.compile(
    r"^.{0,40}?\b(?:carriers?|alleles?|variants?|polymorphisms?|genotypes?|mutations?|SNPs?)\b",
    re.IGNORECASE,
)
GENOTYPE_RE = re.compile(
    r"\b([ACGT])/?([ACGT])\s+(?:genotypes?|carriers|homozygotes|heterozygotes)\b"
)
# A sentence ends at . ! ? followed by space and a capital (not a decimal point or "e.g.").
SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_VARIANT_GROUP_RE = re.compile(r"VariantGroup:(\d+)")
_VARIANT_TYPES = {"variant", "dnamutation", "proteinmutation", "snp"}


def parse_p(text: str) -> float | None:
    t = text.replace("−", "-").replace("–", "-").replace("×", "x").replace(" ", "")
    m = re.match(r"^(\d*\.?\d+)(?:x10-(\d+)|[eE]-(\d+))?$", t)
    if not m:
        return None
    exp = m.group(2) or m.group(3)
    return float(m.group(1)) * (10 ** -int(exp) if exp else 1)


def parse_estimates(sentence: str) -> list[dict[str, Any]]:
    """Every OR / HR / RR the sentence states, with its CI, p, per-allele flag, computed
    significance and whether the CI fails to contain the estimate."""
    out = []
    matches = list(MEASURE_RE.finditer(sentence))
    for i, m in enumerate(matches):
        value = float(m.group("value"))
        lo = float(m.group("lo")) if m.group("lo") else None
        hi = float(m.group("hi")) if m.group("hi") else None
        stop = matches[i + 1].start() if i + 1 < len(matches) else min(len(sentence), m.end() + 80)
        pm = P_RE.search(sentence, m.end(), stop)
        p = parse_p(pm.group("p")) if pm else None
        flagged = lo is not None and hi is not None and not (lo <= value <= hi)
        if lo is not None and hi is not None and not flagged:
            significant: bool | None = not (lo <= 1 <= hi)
        elif p is not None:
            significant = p < 0.05 or (pm is not None and pm.group("op") in "<≤" and p <= 0.05)
        else:
            significant = None
        measure = m.group("measure")
        out.append(
            {
                "start": m.start(),
                "end": m.end(),
                "measure": "HR"
                if "h" in measure.lower()[:2] or measure.lower().startswith("hazard")
                else "RR"
                if measure.lower().startswith(("rr", "relative", "risk"))
                else "OR",
                "value": value,
                "ci": [lo, hi] if lo is not None and hi is not None else None,
                "p": p,
                "per_allele": bool(PER_ALLELE_RE.search(m.group(0)) or "allel" in measure.lower()),
                "significant": significant,
                "flagged": flagged,
                "text": m.group(0),
            }
        )
    return out


# ── mentions ────────────────────────────────────────────────────────────────────────


def _ids(identifier: str) -> set[str]:
    return {i.strip() for i in re.split(r"[;,]", identifier or "") if i.strip()}


def _kind(a: Any) -> str:
    return a.type.lower().replace(" ", "")


def paper_mentions(
    doc: Any, variant: Mapping[str, Any], target_ids: set[str]
) -> list[dict[str, Any]]:
    """Mentions in one document, by document offset: of this variant (``mine``, with the
    selection rule), of other variants or other genes (owners that block attribution), of
    the target gene itself (transparent), and of diseases."""
    from profile_tables import variant_pattern

    rsid = str(variant.get("rsid") or "").lower()
    pattern = variant_pattern(variant.get("change") or "") if variant.get("change") else None
    hgvs = [h for h in variant.get("hgvsc") or [] if h]
    gene_variants = [
        a
        for p in doc.passages
        for a in p.annotations
        if _kind(a) in _VARIANT_TYPES and _ids(a.gene_id) & target_ids
    ]
    out: list[dict[str, Any]] = []
    groups: set[str] = set()
    for p in doc.passages:
        for a in p.annotations:
            kind = _kind(a)
            mention = {"start": a.offset, "end": a.offset + a.length, "text": a.text}
            if kind in _VARIANT_TYPES:
                text_hit = bool(pattern and pattern.search(a.text)) or any(
                    h in a.text for h in hgvs
                )
                if rsid and a.rsid == rsid:
                    out.append({**mention, "role": "mine", "rule": "rs id tag"})
                elif text_hit:
                    out.append({**mention, "role": "mine", "rule": "alleles as recorded"})
                else:
                    out.append(
                        {
                            **mention,
                            "role": "other",
                            "group": _VARIANT_GROUP_RE.findall(a.identifier),
                        }
                    )
                    continue
                groups.update(_VARIANT_GROUP_RE.findall(a.identifier))
            elif kind == "gene":
                role = "target" if _ids(a.identifier) & target_ids else "other"
                out.append({**mention, "role": role})
            elif kind == "disease":
                out.append(
                    {**mention, "role": "disease", "id": a.identifier, "name": a.name or a.text}
                )
        if pattern:  # spellings PubTator did not tag
            for m in pattern.finditer(p.text or ""):
                start = p.offset + m.start()
                if not any(x["start"] <= start < x["end"] for x in out):
                    out.append({"start": start, "end": p.offset + m.end(), "text": m.group(0),
                                "role": "mine", "rule": "alleles as recorded"})  # fmt: skip
    for x in out:  # VariantGroup coreference with a mention of this variant
        if x["role"] == "other" and groups & set(x.get("group") or []):
            x.update(role="mine", rule="VariantGroup")
    others = [a for a in gene_variants if not any(
        x["role"] == "mine" and x["start"] == a.offset for x in out)]  # fmt: skip
    # the gene with an allele word ("PCSK9 carriers") means this variant only in a paper
    # that names this variant and no other variant of the gene
    if any(x["role"] == "mine" for x in out) and not others:
        for p in doc.passages:
            for x in [m for m in out if m["role"] == "target"]:
                rel = x["end"] - p.offset
                if 0 <= rel <= len(p.text or "") and ALLELE_WORD_RE.match((p.text or "")[rel:]):
                    x.update(role="mine", rule="gene with an allele word")
    if (
        gene_variants
        and len({(a.rsid or a.text) for a in gene_variants}) == 1
        and any(x["role"] == "mine" for x in out)
    ):
        for p in doc.passages:
            for m in GENOTYPE_RE.finditer(p.text or ""):
                start = p.offset + m.start()
                if not any(x["start"] <= start < x["end"] for x in out):
                    out.append({"start": start, "end": p.offset + m.end(), "text": m.group(0),
                                "role": "mine", "rule": "genotype"})  # fmt: skip
    return sorted(out, key=lambda x: x["start"])


def _sentences(passage: Any) -> Iterable[tuple[int, str]]:
    text = passage.text or ""
    start = 0
    for m in [*SENTENCE_BREAK_RE.finditer(text), None]:
        end = m.start() if m else len(text)
        if text[start:end].strip():
            yield passage.offset + start, text[start:end]
        start = m.end() if m else end


def _is_stratum(sentence: str, start: int, mention: Mapping[str, Any]) -> bool:
    before = sentence[: mention["start"] - start]
    return bool(STRATUM_BEFORE_RE.search(before[-80:]))


def _outcome(
    sentence: str, start: int, est: Mapping[str, Any], diseases: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    inside = [d for d in diseases if start <= d["start"] < start + len(sentence)]

    def stratum(d: Mapping[str, Any]) -> bool:
        before = sentence[: d["start"] - start]
        after = sentence[d["end"] - start :]
        return bool(
            DISEASE_STRATUM_BEFORE_RE.search(before[-60:]) or DISEASE_STRATUM_AFTER_RE.match(after)
        )

    usable = [d for d in inside if not stratum(d)]
    before = [d for d in usable if d["end"] - start <= est["start"]]
    pick = before[-1] if before else (usable[0] if usable else None)
    return {"id": pick["id"], "name": pick["name"]} if pick else None


def extract(
    docs: Sequence[Any],
    variants: Sequence[Mapping[str, Any]],
    target_ids: set[str],
    meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The estimates the abstracts of ``docs`` state for each of ``variants`` (series rows:
    change, rsid, hgvsc), with the counts of estimates excluded and why."""
    meta = meta or {}
    estimates: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    # An rs id naming several series variants (two alleles at one codon) cannot tell them
    # apart: those variants are selected by their recorded allele spellings only.
    shared = {
        r
        for r, n in Counter(str(v.get("rsid") or "").lower() for v in variants).items()
        if r and n > 1
    }
    variants = [
        {**v, "rsid": ""} if str(v.get("rsid") or "").lower() in shared else v for v in variants
    ]
    for doc in docs:
        title = " ".join(p.text for p in doc.passages if p.kind == "title")
        types = set((meta.get(doc.pmid) or {}).get("publication_types") or [])
        is_meta = "Meta-Analysis" in types or "meta-analysis" in title.lower()
        for v in variants:
            mentions = paper_mentions(doc, v, target_ids)
            if not any(m["role"] == "mine" for m in mentions):
                continue
            diseases = [m for m in mentions if m["role"] == "disease"]
            owners = [m for m in mentions if m["role"] in ("mine", "other")]
            for passage in doc.passages:
                for start, sentence in _sentences(passage):
                    found = parse_estimates(sentence)
                    if not found:
                        continue
                    here = [
                        m for m in owners
                        if start <= m["start"] < start + len(sentence)
                        and not _is_stratum(sentence, start, m)
                    ]  # fmt: skip
                    if not any(m["role"] == "mine" for m in here):
                        continue
                    respectively = RESPECTIVELY_RE.search(sentence) and len(here) == len(found)
                    for k, est in enumerate(found):
                        if respectively:
                            owner = here[k]
                        else:
                            prior = [m for m in here if m["end"] - start <= est["start"]]
                            owner = prior[-1] if prior else None
                        if owner is None:
                            excluded["no mention before the estimate"] += 1
                            continue
                        if owner["role"] != "mine":
                            excluded["another variant or gene owns it"] += 1
                            continue
                        between = sentence[owner["end"] - start : est["start"]]
                        if COMBINED_RE.search(between):
                            excluded["a combined score or haplotype"] += 1
                            continue
                        if est["flagged"]:
                            excluded["CI does not contain the estimate"] += 1
                        estimates.append(
                            {
                                "variant": v.get("change") or v.get("rsid"),
                                "pmid": doc.pmid,
                                "year": (doc.date or "")[:4],
                                "rule": owner["rule"],
                                "meta_analysis": is_meta,
                                "outcome": _outcome(sentence, start, est, diseases),
                                "sentence": sentence.strip(),
                                **{
                                    k2: est[k2]
                                    for k2 in (
                                        "measure",
                                        "value",
                                        "ci",
                                        "p",
                                        "per_allele",
                                        "significant",
                                        "flagged",
                                        "text",
                                    )
                                },
                            }
                        )
    return {"estimates": estimates, "excluded": dict(excluded), "papers_read": len(docs)}


# ── outcomes: tally and forest ──────────────────────────────────────────────────────

MIN_FOREST_PAPERS = 3


def stated_summary(data: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Per variant, how many usable estimates the abstracts state and in how many papers."""
    out: dict[str, dict[str, Any]] = {}
    for e in data.get("estimates") or []:
        if e.get("flagged"):
            continue
        s = out.setdefault(e["variant"], {"estimates": 0, "papers": set()})
        s["estimates"] += 1
        s["papers"].add(e["pmid"])
    return {v: {"estimates": s["estimates"], "papers": len(s["papers"])} for v, s in out.items()}


def outcome_tally(estimates: Sequence[Mapping[str, Any]], variant: str) -> list[dict[str, Any]]:
    """Per outcome of one variant: papers, and estimates significant above 1, below 1, and
    not significant or without CI and p; meta-analyses counted apart."""
    rows: dict[str, dict[str, Any]] = {}
    for e in estimates:
        if e["variant"] != variant or e.get("flagged"):
            continue
        o = e.get("outcome") or {}
        key = o.get("id") or "untagged"
        r = rows.setdefault(key, {"outcome": o.get("name") or "outcome not tagged", "id": o.get("id", ""),
                                  "papers": set(), "up": 0, "down": 0, "ns": 0, "unknown": 0, "meta": 0})  # fmt: skip
        r["papers"].add(e["pmid"])
        if e.get("meta_analysis"):
            r["meta"] += 1
        if e.get("significant") is None:
            r["unknown"] += 1
        elif not e["significant"]:
            r["ns"] += 1
        elif e["value"] > 1:
            r["up"] += 1
        else:
            r["down"] += 1
    out = [{**r, "papers": len(r["papers"])} for r in rows.values()]
    return sorted(out, key=lambda r: (-r["papers"], r["outcome"]))


def forest_rows(estimates: Sequence[Mapping[str, Any]], variant: str) -> list[dict[str, Any]]:
    """Estimates with a CI for the outcomes of ``variant`` stated in ≥ MIN_FOREST_PAPERS
    papers with a CI or p."""
    usable = [e for e in estimates if e["variant"] == variant and not e.get("flagged")]
    papers: dict[str, set[str]] = {}
    for e in usable:
        if e.get("ci") or e.get("p") is not None:
            papers.setdefault((e.get("outcome") or {}).get("id") or "untagged", set()).add(
                e["pmid"]
            )
    keep = {k for k, v in papers.items() if len(v) >= MIN_FOREST_PAPERS and k != "untagged"}
    return [e for e in usable if e.get("ci") and (e.get("outcome") or {}).get("id") in keep]


def _cite(keys: Sequence[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


def outcomes_md(data: Mapping[str, Any], available: set[str], max_variants: int = 3) -> str:
    """The outcome tally of the variants with the most stated estimates, citing the papers."""
    summary = stated_summary(data)
    if not summary:
        return ""
    estimates = data.get("estimates") or []
    out = []
    for variant in sorted(summary, key=lambda v: -summary[v]["estimates"])[:max_variants]:
        out += [
            f"**{variant}**: {summary[variant]['estimates']} estimate(s) in "
            f"{summary[variant]['papers']} paper(s).",
            "",
            "| Outcome | Papers | Significant, ratio > 1 | Significant, ratio < 1 | Not significant "
            "| No CI or p | Meta-analyses | Refs |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in outcome_tally(estimates, variant)[:8]:
            pmids = sorted({e["pmid"] for e in estimates if e["variant"] == variant
                            and ((e.get("outcome") or {}).get("id") or "untagged") == (r["id"] or "untagged")})  # fmt: skip
            out.append(
                f"| {r['outcome']} | {r['papers']} | {r['up']} | {r['down']} | {r['ns']} | "
                f"{r['unknown']} | {r['meta']} | {_cite([f'pmid:{p}' for p in pmids[:6]], available)} |"
            )
        out.append("")
    ex = data.get("excluded") or {}
    out.append(
        f"Estimates parsed from the abstracts of {data.get('papers_read', 0)} grounded papers "
        "(verbatim sentences in raw/stated_effects.json); significance from the CI (excludes 1) "
        "or p < 0.05. Excluded: "
        + (", ".join(f"{n} {why}" for why, n in ex.items()) or "none")
        + ". A tally counts statements, not independent cohorts."
    )
    return "\n".join(out)
