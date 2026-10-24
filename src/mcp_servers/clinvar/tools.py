# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinVar pathogenic / likely pathogenic calls of a gene, from ClinVar itself.

gnomAD's ClinVar mirror carries each call's significance, stars and HGVS but not the
condition the call is for, its review status or its submitters. A classification is
condition-specific: a truncating variant can be pathogenic for one disorder of a gene and
protective for another, so without the condition a call cannot be weighed against the
run's disease or the gene's mechanism. This connector reads ClinVar through NCBI
E-utilities (esearch + esummary, public domain, no key needed; ``NCBI_API_KEY`` raises the
rate limit) and keeps, per allele, the accession, the conditions (with their MONDO ids),
the review status and the number of submissions.

A call is the gene's when the record title names a transcript of the gene
("NM_174936.4(PCSK9):c.1120G>T (p.Asp374Tyr)"); copy-number records spanning many genes
and haplotypes are left out. The title's HGVS is on the canonical transcript, unlike the
record's ``protein_change``, which lists other isoforms' positions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from mcp_servers.pubmed.tools import _rate_limited_get

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# Calls fetched per gene; the count past this is reported, not summarised.
MAX_RECORDS = 10000  # one esearch page; the largest genes (BRCA1/2) hold ~6000
_BATCH = 200

# Review status → ClinVar's gold stars.
_REVIEW_STARS = {
    "practice guideline": 4,
    "reviewed by expert panel": 3,
    "criteria provided, multiple submitters, no conflicts": 2,
    "criteria provided, single submitter": 1,
    "criteria provided, conflicting classifications": 1,
}
_PLP = {"Pathogenic": "pathogenic", "Likely pathogenic": "likely_pathogenic",
        "Pathogenic/Likely pathogenic": "likely_pathogenic"}  # fmt: skip
# ClinVar's consequence names that are not the Sequence Ontology term.
_CLINVAR_SO = {"nonsense": "stop_gained", "initiator_codon_variant": "start_lost",
               "inframe_indel": "inframe_deletion"}  # fmt: skip
# Most severe first: a record listing "splice donor variant" and "intron variant" is the former.
_SEVERITY = (
    "stop_gained", "frameshift_variant", "splice_acceptor_variant", "splice_donor_variant",
    "start_lost", "stop_lost", "inframe_insertion", "inframe_deletion",
    "protein_altering_variant", "missense_variant", "splice_region_variant",
)  # fmt: skip
_TITLE_RE = re.compile(
    r"^(?P<tx>[NX]M_[\d.]+)\((?P<gene>[^)]+)\):(?P<c>c\.\S+)(?: \((?P<p>p\.[^)]+)\))?"
)
_SPDI_RE = re.compile(
    r"^NC_0*(?P<chr>\d+|X|Y)\.\d+:(?P<pos>\d+):(?P<ref>[ACGTN]*):(?P<alt>[ACGTN]*)$"
)
_NO_CONDITION = {"not provided", "not specified", "see cases"}


class ClinVarCondition(BaseModel):
    name: str
    mondo_id: str = ""  # "MONDO:0011369"


class ClinVarCall(BaseModel):
    """One allele's germline classification; field names follow gnomAD's mirror rows."""

    variant_id: str = ""  # GRCh38 "chrom-pos-ref-alt" of a substitution; "" for an indel
    clinical_significance: str
    gold_stars: int = 0
    hgvsc: str = ""
    hgvsp: str | None = None
    transcript: str = ""
    major_consequence: str = ""
    accession: str = ""
    review_status: str = ""
    conditions: list[ClinVarCondition] = []
    submissions: int = 0


class ClinVarCallBundle(BaseModel):
    gene_symbol: str
    query: str = ""
    records_found: int = 0
    capped: bool = False
    pathogenic: list[ClinVarCall] = []
    likely_pathogenic: list[ClinVarCall] = []
    source_link: str = ""
    text: str = ""


def _spdi_allele(spdi: str) -> str:
    """GRCh38 'chrom-pos-ref-alt' of a substitution ("NC_000001.11:55057453:G:T" →
    "1-55057454-G-T"); "" for an insertion or deletion, whose VCF form needs the anchor base."""
    m = _SPDI_RE.match(spdi or "")
    if not m or not m["ref"] or not m["alt"] or len(m["ref"]) != len(m["alt"]):
        return ""
    chrom = m["chr"].lstrip("0") if m["chr"].isdigit() else m["chr"]
    chrom = {"23": "X", "24": "Y"}.get(chrom, chrom)
    return f"{chrom}-{int(m['pos']) + 1}-{m['ref']}-{m['alt']}"


def _severity(term: str) -> int:
    if term == "synonymous_variant":
        return len(_SEVERITY) + 1
    return _SEVERITY.index(term) if term in _SEVERITY else len(_SEVERITY)


def _conditions(traits: Iterable[Mapping[str, Any]]) -> list[ClinVarCondition]:
    out: dict[str, ClinVarCondition] = {}
    for t in traits:
        name = str(t.get("trait_name") or "").strip()
        if not name or name.lower() in _NO_CONDITION:
            continue
        mondo = next(
            (str(x.get("db_id") or "") for x in t.get("trait_xrefs") or []
             if x.get("db_source") == "MONDO"),
            "",
        )  # fmt: skip
        out.setdefault(name, ClinVarCondition(name=name, mondo_id=mondo))
    return list(out.values())


def parse_summaries(
    summaries: Iterable[Mapping[str, Any]], gene_symbol: str
) -> tuple[list[ClinVarCall], list[ClinVarCall]]:
    """(pathogenic, likely pathogenic) calls of the gene's own alleles from ClinVar
    esummary records. "Pathogenic/Likely pathogenic" goes with likely pathogenic, as in
    gnomAD's mirror; conflicting classifications are not P/LP and are left out."""
    calls: dict[str, list[ClinVarCall]] = {"pathogenic": [], "likely_pathogenic": []}
    for s in summaries:
        g = s.get("germline_classification") or {}
        sig = str(g.get("description") or "").split(";")[0].strip()
        title = _TITLE_RE.match(str(s.get("title") or ""))
        variation = s.get("variation_set") or []
        if sig not in _PLP or not title or title["gene"] != gene_symbol or len(variation) != 1:
            continue
        terms = [
            _CLINVAR_SO.get(t, t)
            for c in s.get("molecular_consequence_list") or []
            if (t := str(c).strip().replace(" ", "_"))
        ]
        review = str(g.get("review_status") or "")
        calls[_PLP[sig]].append(
            ClinVarCall(
                variant_id=_spdi_allele(str(variation[0].get("canonical_spdi") or "")),
                clinical_significance=sig,
                gold_stars=_REVIEW_STARS.get(review, 0),
                hgvsc=title["c"],
                hgvsp=title["p"] or None,
                transcript=title["tx"],
                major_consequence=min(terms, key=_severity, default=""),
                accession=str(s.get("accession") or ""),
                review_status=review,
                conditions=_conditions(g.get("trait_set") or []),
                submissions=len((s.get("supporting_submissions") or {}).get("scv") or []),
            )
        )
    return calls["pathogenic"], calls["likely_pathogenic"]


async def get_pathogenic_variants(gene_symbol: str) -> ClinVarCallBundle:
    """ClinVar's pathogenic / likely pathogenic calls for the gene's own alleles, each with
    the condition(s) it is for, its review status and its number of submissions."""
    term = (
        f'"{gene_symbol}"[gene] AND (clinsig_pathogenic[prop] OR clinsig_likely_pathogenic[prop])'
    )
    es = await _rate_limited_get(
        f"{_EUTILS}/esearch.fcgi", {"db": "clinvar", "term": term, "retmax": MAX_RECORDS}
    )
    found = int(es["esearchresult"]["count"])
    ids: list[str] = list(es["esearchresult"]["idlist"])
    summaries: list[dict[str, Any]] = []
    for i in range(0, len(ids), _BATCH):
        batch = ids[i : i + _BATCH]
        res = await _rate_limited_get(
            f"{_EUTILS}/esummary.fcgi", {"db": "clinvar", "id": ",".join(batch)}
        )
        result = res.get("result") or {}
        summaries += [result[u] for u in batch if u in result]
    pathogenic, likely = parse_summaries(summaries, gene_symbol)
    named = sum(1 for c in (*pathogenic, *likely) if c.conditions)
    return ClinVarCallBundle(
        gene_symbol=gene_symbol,
        query=term,
        records_found=found,
        capped=found > len(ids),
        pathogenic=pathogenic,
        likely_pathogenic=likely,
        source_link=f"https://www.ncbi.nlm.nih.gov/clinvar/?term={gene_symbol}%5Bgene%5D",
        text=(
            f"ClinVar: {len(pathogenic)} pathogenic and {len(likely)} likely pathogenic calls "
            f"on {gene_symbol}'s own alleles ({named} name the condition they are for); "
            f"{found} records matched the query{' (capped)' if found > len(ids) else ''}."
        ),
    )
