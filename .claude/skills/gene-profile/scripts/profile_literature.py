# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The gene's literature as identifiers: PubTator3 annotations aggregated into a map.

Keyword search finds words, not genes: ``"MET"[tiab]`` returns 345,759 papers, NCBI's
curated gene–PubMed links 2,891. The paper set here is the curated links (gene2pubmed)
plus the keyword hits the profile lists, and a paper is about the target only when
PubTator3 ties a mention to the target's NCBI Gene id or an ortholog's, after the
approved-symbol rule of ``services.evidence.grounding`` ("MET" read as another gene whose
alias it is). PubTator's own relation search spans all of PubMed and maps family names
to one member (generic "17β-hydroxysteroid dehydrogenase" papers from 1979 counted for
HSD17B13), so only the relations inside the papers of this set are read.

From those papers:

* the diseases, chemicals and genes PubTator relates to the target or to a variant of it,
  with paper counts, relation types and the most-cited PMIDs. A relation type
  (Association, Positive_Correlation, …) is a text-mining tag of the co-mention, never a
  finding;
* the target's variants the papers name (tmVar rs id / HGVS), with what they are related to;
* the organisms (NCBI taxonomy) and cell lines (Cellosaurus) the papers mention, and how
  many name the human gene and how many an ortholog: how the knowledge was obtained;
* per listed paper, whether it names the target at all;
* keyword precision: the share of relevance-ranked keyword hits PubTator annotated that
  name the target. Below ``MIN_KEYWORD_PRECISION`` the keyword count is not the gene's
  literature, and the curated count is reported instead.

Grounding (the rule every block built from papers follows): a paper belongs to the gene's
literature only when NCBI gene2pubmed links it to the gene, or PubTator tags a gene (or
variant) mention with the gene's NCBI id or an ortholog's. A symbol, alias or previous
symbol is a search term for recall, never evidence that a paper is about the gene; the
sense table (``token_senses``) shows, per term, what PubTator types the token as in a
sample of its keyword hits, and a term whose hits are mostly another sense is not searched.

Pure module: the caller fetches the documents and the HGNC records the approved-symbol
rule needs.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MIN_KEYWORD_PRECISION = 0.5
# A previous symbol or alias is searched only when at least this share of its sampled
# keyword hits name this gene, from at least MIN_PRECISION_SAMPLE hits holding the token.
MIN_TERM_SHARE = 0.5
SENSES = ("this gene", "another gene", "chemical", "disease", "other entity", "untagged")
# A precision from fewer annotated keyword hits than this is not used.
MIN_PRECISION_SAMPLE = 10
TOP_ENTITIES = 25
TOP_PMIDS = 5

# NCBI taxonomy ids of the organisms papers most often study; others show as "taxon <id>".
TAXA = {
    "9606": "human",
    "10090": "mouse",
    "10116": "rat",
    "7955": "zebrafish",
    "7227": "fruit fly",
    "6239": "C. elegans",
    "4932": "budding yeast",
    "559292": "budding yeast",
    "4896": "fission yeast",
    "8355": "Xenopus laevis",
    "8364": "Xenopus tropicalis",
    "9031": "chicken",
    "9913": "cattle",
    "9823": "pig",
    "9940": "sheep",
    "9615": "dog",
    "9685": "cat",
    "9986": "rabbit",
    "10036": "golden hamster",
    "10029": "Chinese hamster",
    "9544": "rhesus macaque",
    "9541": "crab-eating macaque",
    "3702": "Arabidopsis",
    "562": "E. coli",
}

_CORRESPONDING = re.compile(r"CorrespondingGene:(\d+)")
_RSID = re.compile(r"RS#:(\d+)")
_HGVS = re.compile(r"HGVS:([^;]+)")


def taxon_name(taxon: str) -> str:
    return TAXA.get(taxon, f"taxon {taxon}")


def _ids(identifier: str) -> list[str]:
    return [i.strip() for i in re.split(r"[;,]", identifier or "") if i.strip()]


def _variant_gene(identifier: str) -> str:
    m = _CORRESPONDING.search(identifier or "")
    return m.group(1) if m else ""


def variant_label(identifier: str, name: str = "") -> str:
    """'rs72613567' or 'p.I148M (rs738409)' from a tmVar identifier."""
    rs = _RSID.search(identifier or "")
    hgvs = _HGVS.search(identifier or "")
    rsid = f"rs{rs.group(1)}" if rs else ""
    change = hgvs.group(1) if hgvs else ""
    if change and rsid:
        return f"{change} ({rsid})"
    return change or rsid or name or identifier


def variant_key(identifier: str) -> str:
    rs = _RSID.search(identifier or "")
    if rs:
        return f"rs{rs.group(1)}"
    hgvs = _HGVS.search(identifier or "")
    return hgvs.group(1) if hgvs else identifier


class _Paper:
    """What one annotated paper says about the target."""

    def __init__(
        self,
        doc: Any,
        *,
        target: Any,
        hgnc: Mapping[str, Any],
        drug_keys: set[str],
        rehomed: Any,
        drug_key: Any,
    ) -> None:
        self.pmid = doc.pmid
        own = {target.ncbi_id} - {""}
        orthologs = set(target.ortholog_ids)
        self.target_ids: set[str] = set(own | orthologs)  # identifiers that are the target here
        self.human = self.ortholog = self.variant = self.drug = False
        self.species: set[str] = set()
        self.cell_lines: dict[str, str] = {}
        self.names: dict[str, str] = {}  # identifier -> a text written for it
        symbol_word = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(target.symbol)}(?![A-Za-z0-9])")
        for passage in doc.passages:
            for a in passage.annotations:
                kind = a.type.lower().replace(" ", "")
                if a.identifier and a.identifier not in self.names:
                    self.names[a.identifier] = a.name or a.text
                if kind == "gene":
                    ids = _ids(a.identifier)
                    if own & set(ids):
                        self.human = True
                    elif orthologs & set(ids):
                        self.ortholog = True
                    elif rehomed(a.text, ids, target, hgnc):
                        self.human = True
                        self.target_ids.update(ids)
                elif kind in ("variant", "dnamutation", "proteinmutation", "snp"):
                    if set(_ids(a.gene_id)) & (own | orthologs):
                        self.variant = True
                elif kind == "chemical":
                    if (
                        drug_key(a.text) in drug_keys
                        or drug_key(a.name) in drug_keys
                        or symbol_word.search(a.text)
                    ):
                        self.drug = True
                elif kind == "species" and a.identifier:
                    self.species.update(_ids(a.identifier))
                elif kind == "cellline" and a.identifier:
                    self.cell_lines.setdefault(a.identifier, a.text)

    @property
    def names_target(self) -> bool:
        return self.human or self.ortholog or self.variant or self.drug

    @property
    def tagged(self) -> bool:
        """PubTator ties a gene or variant mention to the gene or an ortholog (a drug
        mention alone is not about the gene)."""
        return self.human or self.ortholog or self.variant

    def is_target(self, identifier: str) -> bool:
        if _variant_gene(identifier):
            return _variant_gene(identifier) in self.target_ids
        return bool(set(_ids(identifier)) & self.target_ids)


def _new_variant(rsid: str) -> dict[str, Any]:
    return {
        "labels": Counter(),
        "rsid": rsid,
        "hgvs": Counter(),
        "pmids": set(),
        "related": defaultdict(lambda: {"types": Counter(), "pmids": set(), "kind": ""}),
    }


def _top_pmids(pmids: Iterable[str], citations: Mapping[str, int]) -> list[str]:
    return sorted(set(pmids), key=lambda p: (-(citations.get(p) or 0), p))[:TOP_PMIDS]


def _entities(
    found: Mapping[str, dict[str, Any]], citations: Mapping[str, int]
) -> list[dict[str, Any]]:
    rows = [
        {
            "id": key,
            "name": e["name"],
            "papers": len(e["pmids"]),
            "relations": dict(e["types"].most_common()),
            "pmids": _top_pmids(e["pmids"], citations),
        }
        for key, e in found.items()
    ]
    return sorted(rows, key=lambda r: (-r["papers"], r["name"]))[:TOP_ENTITIES]


def build_map(
    docs: Sequence[Any],
    target: Any,
    *,
    hgnc: Mapping[str, Any] | None = None,
    drug_names: Iterable[str] = (),
    citations: Mapping[str, int] | None = None,
    listed: Iterable[str] = (),
    keyword_sample: Sequence[str] = (),
    requested: int = 0,
    curated: Iterable[str] = (),
) -> dict[str, Any]:
    """The literature map of ``docs`` (PubTator documents) for ``target`` (a
    ``TargetIdentity``). ``listed`` are the PMIDs of the profile's PubMed lists (flagged one
    by one); ``keyword_sample`` the relevance-ranked keyword hits (for precision);
    ``curated`` the gene's gene2pubmed PMIDs, grounded whatever PubTator tags."""
    from services.evidence.drug_precedent import drug_key
    from services.evidence.grounding import rehomed

    cites = dict(citations or {})
    drug_keys = {drug_key(d) for d in drug_names if d}
    papers = {
        d.pmid: _Paper(
            d,
            target=target,
            hgnc=hgnc or {},
            drug_keys=drug_keys,
            rehomed=rehomed,
            drug_key=drug_key,
        )
        for d in docs
    }
    naming = {p for p, x in papers.items() if x.names_target}
    linked = set(curated)
    grounded = {p for p, x in papers.items() if x.tagged} | (linked & set(papers))

    groups: dict[str, dict[str, dict[str, Any]]] = {
        "diseases": {},
        "chemicals": {},
        "genes": {},
    }
    variants: dict[str, dict[str, Any]] = {}
    for doc in docs:
        paper = papers[doc.pmid]
        if doc.pmid not in naming:
            continue
        for p in doc.passages:
            for a in p.annotations:
                kind = a.type.lower().replace(" ", "")
                if kind in ("variant", "dnamutation", "proteinmutation", "snp") and (
                    set(_ids(a.gene_id)) & paper.target_ids
                ):
                    v = variants.setdefault(a.rsid or a.hgvs or a.text, _new_variant(a.rsid))
                    v["labels"][a.text] += 1
                    if a.hgvs:
                        v["hgvs"][a.hgvs] += 1
                    v["pmids"].add(doc.pmid)
        for r in doc.relations:
            one, two = paper.is_target(r.role1), paper.is_target(r.role2)
            if one == two:
                continue
            mine, other = (r.role1, r.role2) if one else (r.role2, r.role1)
            other_type = (r.role2_type if one else r.role1_type).lower()
            other_name = (r.role2_name if one else r.role1_name) or paper.names.get(other, "")
            if _variant_gene(mine):
                key = variant_key(mine)
                v = variants.setdefault(key, _new_variant(key if key.startswith("rs") else ""))
                v["pmids"].add(doc.pmid)
                label = variant_label(other, other_name) if _variant_gene(other) else other_name
                rel = v["related"][label or other]
                rel["types"][r.type] += 1
                rel["pmids"].add(doc.pmid)
                rel["kind"] = other_type
                if other_type not in ("disease", "chemical"):
                    continue
            group = {
                "disease": "diseases",
                "chemical": "chemicals",
                "gene": "genes",
                "variant": "genes",
                "dnamutation": "genes",
                "proteinmutation": "genes",
                "snp": "genes",
            }.get(other_type)
            if group is None:
                continue
            if group == "genes" and _variant_gene(other):
                gene = _variant_gene(other)
                name = f"{paper.names.get(gene, gene)} {variant_label(other)}"
                key = f"{gene}:{variant_key(other)}"
            else:
                name, key = other_name or other, other
            e = groups[group].setdefault(key, {"name": name, "types": Counter(), "pmids": set()})
            e["types"][r.type] += 1
            e["pmids"].add(doc.pmid)

    variant_rows = []
    for key, v in variants.items():
        related = sorted(
            (
                {
                    "name": name,
                    "kind": x["kind"],
                    "papers": len(x["pmids"]),
                    "relations": dict(x["types"].most_common()),
                    "pmids": _top_pmids(x["pmids"], cites),
                }
                for name, x in v["related"].items()
            ),
            key=lambda r: (-r["papers"], r["name"]),
        )[:5]
        variant_rows.append(
            {
                "id": key,
                "rsid": v.get("rsid") or (key if key.startswith("rs") else ""),
                "hgvs": v["hgvs"].most_common(1)[0][0] if v["hgvs"] else "",
                "label": v["labels"].most_common(1)[0][0] if v["labels"] else key,
                "papers": len(v["pmids"]),
                "pmids": _top_pmids(v["pmids"], cites),
                "related": related,
            }
        )
    variant_rows.sort(key=lambda r: (-r["papers"], r["id"]))

    organisms: Counter[str] = Counter()
    cell_counts: Counter[str] = Counter()
    cell_names: dict[str, str] = {}
    for pmid in naming:
        organisms.update(papers[pmid].species)
        for cid, text in papers[pmid].cell_lines.items():
            cell_counts[cid] += 1
            cell_names.setdefault(cid, text)

    sample = [p for p in keyword_sample if p in papers]
    sample_naming = sum(1 for p in sample if p in naming)
    precision = (
        round(sample_naming / len(sample), 3) if len(sample) >= MIN_PRECISION_SAMPLE else None
    )

    return {
        "target": {
            "symbol": target.symbol,
            "ncbi_id": target.ncbi_id,
            "orthologs": len(target.ortholog_ids),
        },
        "papers_requested": requested or len(docs),
        "papers_annotated": len(docs),
        "papers_naming_target": len(naming),
        "gene_studied": {
            "human_gene": sum(1 for p in naming if papers[p].human),
            "ortholog": sum(1 for p in naming if papers[p].ortholog),
            "variant": sum(1 for p in naming if papers[p].variant),
            "drug": sum(1 for p in naming if papers[p].drug),
        },
        "keyword": {
            "sample": len(keyword_sample),
            "annotated": len(sample),
            "naming_target": sample_naming,
            "precision": precision,
        },
        "organisms": [
            {"taxon": t, "name": taxon_name(t), "papers": n}
            for t, n in organisms.most_common()
            if t in TAXA
        ][:10],
        "other_organism_papers": sum(n for t, n in organisms.items() if t not in TAXA),
        "cell_lines": [
            {"id": c, "name": cell_names[c], "papers": n} for c, n in cell_counts.most_common(15)
        ],
        "diseases": _entities(groups["diseases"], cites),
        "chemicals": _entities(groups["chemicals"], cites),
        "genes": _entities(groups["genes"], cites),
        "variants": variant_rows[:TOP_ENTITIES],
        "variant_count": len(variant_rows),
        "papers": {
            p: {
                "names_target": p in naming,
                "grounded": p in grounded,
                "routes": [
                    route
                    for route, on in (
                        ("gene2pubmed", p in linked),
                        ("human gene", papers[p].human),
                        ("ortholog", papers[p].ortholog),
                        ("variant", papers[p].variant),
                    )
                    if on
                ],
                "organisms": sorted(taxon_name(t) for t in papers[p].species),
                "cell_lines": len(papers[p].cell_lines),
            }
            for p in dict.fromkeys(listed)
            if p in papers
        },
        # every annotated paper the grounding rule admits, for the blocks built from papers
        "grounded_pmids": sorted(grounded, key=int),
    }


def _sense(annotation: Any, target_ids: set[str]) -> str:
    if annotation is None:
        return "untagged"
    kind = annotation.type.lower().replace(" ", "")
    if kind == "gene":
        return "this gene" if set(_ids(annotation.identifier)) & target_ids else "another gene"
    if kind in ("variant", "dnamutation", "proteinmutation", "snp"):
        return "this gene" if set(_ids(annotation.gene_id)) & target_ids else "another gene"
    if kind == "chemical":
        return "chemical"
    if kind == "disease":
        return "disease"
    return "other entity"


def token_senses(docs: Sequence[Any], term: str, target: Any) -> dict[str, Any]:
    """What PubTator types ``term`` as in ``docs`` (a sample of its keyword hits): per
    paper, the sense of its mentions of the token — this gene (its NCBI id or an
    ortholog's), another gene, a chemical, a disease, another entity, or untagged (a word
    PubTator did not annotate) — the first in ``SENSES`` order when a paper has several.
    Papers whose title and abstract do not hold the token (a hit on another field) are not
    counted."""
    target_ids = {target.ncbi_id, *target.ortholog_ids} - {""}
    word = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
    counts: Counter[str] = Counter()
    for doc in docs:
        found: set[str] = set()
        for p in doc.passages:
            for m in word.finditer(p.text or ""):
                start, end = p.offset + m.start(), p.offset + m.end()
                ann = next(
                    (a for a in p.annotations if a.offset < end and start < a.offset + a.length),
                    None,
                )
                found.add(_sense(ann, target_ids))
        if found:
            counts[min(found, key=SENSES.index)] += 1
    n = sum(counts.values())
    return {
        "term": term,
        "papers": n,
        "shares": {s: round(counts[s] / n, 3) for s in SENSES if counts[s]} if n else {},
        "this_gene": round(counts["this gene"] / n, 3) if n else None,
    }


def choose_terms(
    symbol: str, senses: Sequence[Mapping[str, Any]], shared: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """The search terms: the approved symbol always (keyword precision then decides whether
    keyword or curated literature is used), and each previous symbol or alias that HGNC
    does not also give another gene and whose sampled hits mostly name this gene."""
    keep, dropped = [symbol], []
    for row in senses:
        term = row["term"]
        if term == symbol:
            continue
        if shared.get(term):
            dropped.append({"term": term, "reason": f"also a symbol of {', '.join(shared[term])}"})
        elif (row.get("papers") or 0) < MIN_PRECISION_SAMPLE:
            dropped.append(
                {"term": term, "reason": f"{row.get('papers') or 0} sampled hits hold it"}
            )
        elif (row.get("this_gene") or 0) < MIN_TERM_SHARE:
            top = max(row["shares"], key=row["shares"].get)
            dropped.append({"term": term, "reason": f"mostly {top} in its hits"})
        else:
            keep.append(term)
    return {"terms": keep, "dropped": dropped}


def grounded_set(literature_map: Mapping[str, Any]) -> set[str]:
    """The PMIDs every block built from papers may use (gene2pubmed or a PubTator gene,
    ortholog or variant tag); empty for maps built before the rule was recorded."""
    return set(literature_map.get("grounded_pmids") or [])


def volume(keyword_total: int, curated_total: int, precision: float | None) -> dict[str, Any]:
    """The literature volume to report: the keyword count unless PubTator shows most keyword
    hits are not about the gene, then the curated count."""
    basis = (
        "curated"
        if precision is not None and precision < MIN_KEYWORD_PRECISION and curated_total
        else "keyword"
    )
    return {
        "basis": basis,
        "total": curated_total if basis == "curated" else keyword_total,
        "keyword_total": keyword_total,
        "curated_total": curated_total,
        "keyword_precision": precision,
    }


def cited_pmids(literature_map: Mapping[str, Any]) -> list[str]:
    """Every PMID the map names, for registration as citable sources."""
    out: list[str] = []
    for group in ("diseases", "chemicals", "genes", "variants"):
        for e in literature_map.get(group) or []:
            out.extend(e.get("pmids") or [])
            for r in e.get("related") or []:
                out.extend(r.get("pmids") or [])
    return list(dict.fromkeys(out))
