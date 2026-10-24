# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic evidence gathering for one gene (optionally scoped to one disease).

Calls the repo's source connectors in-process (``mcp_servers.<source>.tools`` — the same code
the MCP gateway exposes) plus a few public REST APIs the gateway does not cover yet (HGNC
record, UniProt GO/PDB, HPA, Reactome, STRING, AlphaFold DB, PubMed E-utilities,
ClinicalTrials.gov gene search). Every call is written to ``raw/<key>.json``; every successful
call becomes a citable ``Source`` in ``sources.json`` whose metadata comes from the response,
never from the model.

Usage (from the repo root):
    uv run python .claude/skills/gene-profile/scripts/gather.py TRPC6
    uv run python .claude/skills/gene-profile/scripts/gather.py TRPC6 --disease "focal segmental glomerulosclerosis"
    uv run python .claude/skills/gene-profile/scripts/gather.py TRPC6 --out <run_dir> --only gnomad_constraint,hpa

Exit codes: 0 ok · 2 ambiguous symbol (candidates printed) · 3 symbol not found.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, urlencode

import httpx
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_schema import (  # noqa: E402
    SECTIONS,
    Manifest,
    ManifestEntry,
    RawRecord,
    Source,
    load_raw,
    load_sources,
    safe_filename,
    save_sources,
)

load_dotenv(find_dotenv(usecwd=True))  # the caller's .env, wherever the skill is installed

from mcp_servers.chembl import tools as chembl  # noqa: E402
from mcp_servers.clingen import tools as clingen  # noqa: E402
from mcp_servers.clinicaltrials import tools as ctgov  # noqa: E402
from mcp_servers.depmap import tools as depmap  # noqa: E402
from mcp_servers.dgidb import tools as dgidb  # noqa: E402
from mcp_servers.expression_atlas import tools as gxa  # noqa: E402
from mcp_servers.gbd import tools as gbd  # noqa: E402
from mcp_servers.gencc import tools as gencc  # noqa: E402
from mcp_servers.gnomad import tools as gnomad  # noqa: E402
from mcp_servers.gtex import tools as gtex  # noqa: E402
from mcp_servers.gwas_catalog import tools as gwas  # noqa: E402
from mcp_servers.impc import tools as impc  # noqa: E402
from mcp_servers.omim import tools as omim  # noqa: E402
from mcp_servers.ontology import tools as ontology  # noqa: E402
from mcp_servers.openfda import tools as openfda  # noqa: E402
from mcp_servers.opentargets import tools as ot  # noqa: E402
from mcp_servers.orphanet import tools as orphanet  # noqa: E402
from mcp_servers.project_score import tools as project_score  # noqa: E402
from mcp_servers.pubmed import tools as pubmed  # noqa: E402
from mcp_servers.spoke import tools as spoke  # noqa: E402
from mcp_servers.ttd import tools as ttd  # noqa: E402
from mcp_servers.uniprot import tools as uniprot  # noqa: E402
from mcp_servers.uspto import tools as uspto  # noqa: E402

_TIMEOUT_S = 150
_CONCURRENCY = 8
_HGNC = "https://rest.genenames.org"
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_CTGOV = "https://clinicaltrials.gov/api/v2/studies"
_OPENFDA = "https://api.fda.gov"
_ICITE = "https://icite.od.nih.gov/api/pubs"
_ICITE_FIELDS = "pmid,citation_count,relative_citation_ratio,is_research_article,is_clinical"
_UA = {"User-Agent": "agentic-target-validation/gene-profile"}


class IdentityError(Exception):
    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass
class Ctx:
    query: str
    symbol: str = ""
    hgnc: dict[str, Any] = field(default_factory=dict)
    ensembl: str = ""
    entrez: str = ""
    uniprot: str = ""
    disease: str = ""
    disease_id: str = ""
    depth: str = "standard"
    today: str = field(default_factory=lambda: date.today().isoformat())
    identity_note: str = ""
    db_versions: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)  # key -> jsonable payload (ok only)
    # NCBI gene2pubmed PMIDs and their set on NCBI's history server, fetched once a run
    curated: asyncio.Task[list[str]] | None = None
    history: asyncio.Task[dict[str, str]] | None = None


@dataclass
class Spec:
    key: str
    section: str
    db_name: str
    title: str  # str.format(**ctx fields)
    tool: str
    fetch: Callable[[Ctx], Awaitable[Any]]
    url: Callable[[Ctx, Any], str]
    wave: int = 1
    skip: Callable[[Ctx], str | None] = lambda _c: None
    quick: bool = False  # included in --depth quick
    timeout_s: int = _TIMEOUT_S


# --------------------------------------------------------------------- helpers


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list | tuple):
        return [to_jsonable(o) for o in obj]
    if isinstance(obj, set):
        return sorted(to_jsonable(o) for o in obj)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return obj


# Echoed inputs and bookkeeping fields: their presence says nothing about whether data came back.
_ID_KEYS = {
    "gene_symbol",
    "ensembl_id",
    "gene_id",
    "source_link",
    "text",
    "gene",
    "symbol",
    "disease",
    "disease_id",
    "condition",
    "indication",
    "query",
    "query_used",
    "query_term",
    "sort",
    "mapping",
    "uniprot",
    "release",
    "hgnc_id",
    "gene_concept_id",
}


def is_substantive(data: Any, *, top: bool = True) -> bool:
    """Heuristic 'did this source return anything?' — zeros, blanks and ids do not count."""
    if data is None:
        return False
    if isinstance(data, bool):
        return data
    if isinstance(data, int | float):
        return data != 0
    if isinstance(data, str):
        return bool(data.strip())
    if isinstance(data, list):
        return any(is_substantive(v, top=False) for v in data)
    if isinstance(data, dict):
        return any(
            is_substantive(v, top=False) for k, v in data.items() if not (top and k in _ID_KEYS)
        )
    return True


async def _get_json(url: str, **params: Any) -> Any:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, headers=_UA) as c:
        r = await c.get(url, params=params or None, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()


def _need(attr: str, why: str) -> Callable[[Ctx], str | None]:
    return lambda c: None if getattr(c, attr) else why


def _need_disease(c: Ctx) -> str | None:
    return None if c.disease else "gene-only run (no --disease)"


# ------------------------------------------------------------- identity / meta


async def _hgnc_fetch(field_name: str, value: str) -> list[dict[str, Any]]:
    data = await _get_json(f"{_HGNC}/{field_name}/{quote(value)}")
    return list((data.get("response") or {}).get("docs") or [])


async def resolve_identity(query: str) -> tuple[dict[str, Any], str]:
    """Return (HGNC record, note). Refuses to guess when an alias maps to several genes."""
    for candidate in dict.fromkeys([query, query.upper()]):
        docs = await _hgnc_fetch("fetch/symbol", candidate)
        if docs:
            return docs[0], ""
    hits: dict[str, str] = {}
    for fld in ("prev_symbol", "alias_symbol"):
        for d in await _hgnc_fetch(f"search/{fld}", query.upper()):
            hits.setdefault(d["symbol"], fld)
    if not hits:
        raise IdentityError(f"No HGNC record for '{query}'")
    if len(hits) > 1:
        raise IdentityError(f"'{query}' is ambiguous in HGNC", sorted(hits))
    symbol, fld = next(iter(hits.items()))
    docs = await _hgnc_fetch("fetch/symbol", symbol)
    label = {"prev_symbol": "previous symbol", "alias_symbol": "alias"}[fld]
    note = f"Input '{query}' is an HGNC {label} of {symbol}; using {symbol}."
    return docs[0], note


async def probe_versions(ctx: Ctx) -> None:
    async def ot_version() -> None:
        d = await ot._graphql("{ meta { dataVersion { year month } } }", {})
        v = d["meta"]["dataVersion"]
        ctx.db_versions["Open Targets Platform"] = f"{v['year']}.{v['month']}"

    async def string_version() -> None:
        d = await _get_json("https://string-db.org/api/json/version")
        ctx.db_versions["STRING"] = str(d[0]["string_version"])

    async def reactome_version() -> None:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get("https://reactome.org/ContentService/data/database/version")
            ctx.db_versions["Reactome"] = r.text.strip()

    ctx.db_versions["gnomAD"] = "v4 (gnomad_r4)"
    await asyncio.gather(ot_version(), string_version(), reactome_version(), return_exceptions=True)


# ----------------------------------------------------------- direct REST fetchers


async def fetch_hgnc(ctx: Ctx) -> dict[str, Any]:
    keep = [
        "symbol",
        "name",
        "hgnc_id",
        "location",
        "locus_group",
        "locus_type",
        "gene_group",
        "alias_symbol",
        "prev_symbol",
        "entrez_id",
        "ensembl_gene_id",
        "uniprot_ids",
        "mgd_id",
        "omim_id",
        "date_modified",
    ]
    return {k: ctx.hgnc.get(k) for k in keep if ctx.hgnc.get(k) is not None}


# UniProt positional features kept for the protein map and the allelic series' regions.
_UNIPROT_FEATURES = {
    "ft_domain": "Domain",
    "ft_repeat": "Repeat",
    "ft_transmem": "Transmembrane",
    "ft_intramem": "Intramembrane",
    "ft_topo_dom": "Topological domain",
    "ft_region": "Region",
    "ft_coiled": "Coiled coil",
    "ft_motif": "Motif",
    "ft_zn_fing": "Zinc finger",
    "ft_signal": "Signal",
    "ft_propep": "Propeptide",
    "ft_act_site": "Active site",
    "ft_binding": "Binding site",
}


def _uniprot_change(f: dict[str, Any]) -> dict[str, Any] | None:
    """A natural variant or mutagenesis feature as a single change with its PMIDs."""
    alt = f.get("alternativeSequence") or {}
    ref, alts = alt.get("originalSequence") or "", alt.get("alternativeSequences") or []
    start = f["location"]["start"].get("value")
    if not ref or not alts or start is None:
        return None
    return {
        "position": start,
        "ref": ref,
        "alt": alts[0],
        "description": f.get("description", ""),
        "pmids": [e["id"] for e in f.get("evidences", []) if e.get("source") == "PubMed"],
    }


async def fetch_uniprot_extra(ctx: Ctx) -> dict[str, Any]:
    from profile_tables import parse_uniprot_variant

    fields = (
        "go_p,go_f,go_c,xref_pdb,cc_disease,cc_tissue_specificity,length,protein_families,"
        + ",".join(_UNIPROT_FEATURES)
        + ",ft_variant,ft_mutagen"
    )
    async with httpx.AsyncClient(timeout=45.0, headers=_UA) as c:
        r = await c.get(
            f"https://rest.uniprot.org/uniprotkb/{ctx.uniprot}",
            params={"fields": fields, "format": "json"},
        )
        r.raise_for_status()
        d = r.json()
        if r.headers.get("X-UniProt-Release"):
            ctx.db_versions["UniProt"] = r.headers["X-UniProt-Release"]
    go: dict[str, list[str]] = {"process": [], "function": [], "component": []}
    pdb: list[dict[str, str]] = []
    for x in d.get("uniProtKBCrossReferences", []):
        props = {p["key"]: p["value"] for p in x.get("properties", [])}
        if x["database"] == "GO" and ":" in props.get("GoTerm", ""):
            aspect, term = props["GoTerm"].split(":", 1)
            go[{"P": "process", "F": "function", "C": "component"}[aspect]].append(term)
        elif x["database"] == "PDB":
            pdb.append(
                {
                    "id": x["id"],
                    "method": props.get("Method", ""),
                    "resolution": props.get("Resolution", ""),
                    "chains": props.get("Chains", ""),
                }
            )
    diseases, tissue, families = [], "", []
    for cm in d.get("comments", []):
        if cm["commentType"] == "DISEASE" and cm.get("disease"):
            dz = cm["disease"]
            diseases.append(
                {
                    "name": dz.get("diseaseId", ""),
                    "acronym": dz.get("acronym", ""),
                    "mim": (dz.get("diseaseCrossReference") or {}).get("id", ""),
                    "description": dz.get("description", "")[:400],
                }
            )
        elif cm["commentType"] == "TISSUE SPECIFICITY":
            tissue = " ".join(t["value"] for t in cm.get("texts", []))
        elif cm["commentType"] == "SIMILARITY":
            families.extend(t["value"] for t in cm.get("texts", []))
    kinds = set(_UNIPROT_FEATURES.values())
    features = [
        {
            "type": f["type"],
            "description": f.get("description", ""),
            "start": f["location"]["start"]["value"],
            "end": f["location"]["end"]["value"],
        }
        for f in d.get("features", [])
        if f.get("type") in kinds
        and f["location"]["start"].get("value") is not None
        and f["location"]["end"].get("value") is not None
    ]
    variants, mutagenesis = [], []
    for f in d.get("features", []):
        change = _uniprot_change(f) if f.get("type") in ("Natural variant", "Mutagenesis") else None
        if change is None:
            continue
        if f["type"] == "Natural variant":
            variants.append({**change, **parse_uniprot_variant(change["description"])})
        else:
            mutagenesis.append(change)
    return {
        "accession": ctx.uniprot,
        "length": (d.get("sequence") or {}).get("length"),
        "families": families,
        "domains": [f for f in features if f["type"] == "Domain"],
        "features": features,
        "variants": variants,
        "disease_variants": sum(1 for v in variants if v["diseases"]),
        "mutagenesis": mutagenesis,
        "go": go,
        "pdb_count": len(pdb),
        "pdb": pdb,
        "diseases": diseases,
        "tissue_specificity": tissue,
    }


_HPA_KEYS = [
    "Protein class",
    "Biological process",
    "Molecular function",
    "Disease involvement",
    "Evidence",
    "RNA tissue specificity",
    "RNA tissue distribution",
    "RNA tissue specific nTPM",
    "RNA single cell type specificity",
    "RNA single cell type specific nCPM",
    "RNA tissue cell type enrichment",
    "Protein tissue specificity",
    "Reliability (IH)",
    "Subcellular location",
    "Secretome location",
    "Blood concentration - Conc. blood MS [pg/L]",
]


async def fetch_hpa(ctx: Ctx) -> dict[str, Any]:
    d = await _get_json(f"https://www.proteinatlas.org/{ctx.ensembl}.json")
    return {k: d.get(k) for k in _HPA_KEYS if d.get(k) not in (None, "", [])}


async def fetch_reactome(ctx: Ctx) -> dict[str, Any]:
    try:
        rows = await _get_json(
            f"https://reactome.org/ContentService/data/mapping/UniProt/{ctx.uniprot}/pathways",
            species="9606",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"count": 0, "pathways": []}
        raise
    pw = [
        {"id": r["stId"], "name": r["displayName"], "in_disease": r.get("isInDisease", False)}
        for r in rows
    ]
    return {"count": len(pw), "pathways": pw}


async def fetch_string(ctx: Ctx) -> dict[str, Any]:
    rows = await _get_json(
        "https://string-db.org/api/json/interaction_partners",
        identifiers=ctx.symbol,
        species=9606,
        limit=15,
        required_score=700,
    )
    partners = [
        {
            "partner": r["preferredName_B"],
            "combined_score": r["score"],
            "experimental": r["escore"],
            "database": r["dscore"],
            "textmining": r["tscore"],
        }
        for r in rows
    ]
    return {"min_combined_score": 0.7, "count": len(partners), "partners": partners}


async def fetch_alphafold(ctx: Ctx) -> dict[str, Any]:
    rows = await _get_json(f"https://alphafold.ebi.ac.uk/api/prediction/{ctx.uniprot}")
    m = rows[0]
    keep = [
        "modelEntityId",
        "globalMetricValue",
        "fractionPlddtVeryHigh",
        "fractionPlddtConfident",
        "fractionPlddtLow",
        "fractionPlddtVeryLow",
        "latestVersion",
        "modelCreatedDate",
    ]
    out = {k: m.get(k) for k in keep}
    # Per-residue confidence for the protein map; the summary stands without it.
    if m.get("plddtDocUrl"):
        try:
            doc = await _get_json(m["plddtDocUrl"])
            out["plddt"] = [round(float(x), 1) for x in doc.get("confidenceScore") or []]
        except (httpx.HTTPError, ValueError):
            pass
    return out


async def fetch_ot_gene_associations(ctx: Ctx) -> dict[str, Any]:
    q = """query($id:String!){ target(ensemblId:$id){ associatedDiseases(page:{index:0,size:25}){
      count rows{ score disease{ id name } datatypeScores{ id score } } } } }"""
    d = await ot._graphql(q, {"id": ctx.ensembl})
    ad = d["target"]["associatedDiseases"]
    rows = [
        {
            "disease_id": r["disease"]["id"],
            "disease_name": r["disease"]["name"],
            "score": round(r["score"], 3),
            "datatype_scores": {s["id"]: round(s["score"], 3) for s in r["datatypeScores"]},
        }
        for r in ad["rows"]
    ]
    return {"total_associated_diseases": ad["count"], "top": rows}


async def _esearch(
    term: str, retmax: int, sort: str = "relevance", history: dict[str, str] | None = None
) -> tuple[int, list[str]]:
    # Reuse the connector's throttled client: one shared 3 req/s window (10 with NCBI_API_KEY)
    # plus 429 backoff, so parallel literature fetches don't trip NCBI's rate limit.
    # ``history`` (WebEnv, usehistory) lets ``term`` start from a stored set ("#1 AND …").
    es = await pubmed._rate_limited_get(
        f"{_EUTILS}/esearch.fcgi",
        {"db": "pubmed", "term": term, "retmax": retmax, "sort": sort, **(history or {})},
    )
    return int(es["esearchresult"]["count"]), es["esearchresult"]["idlist"]


async def _summaries(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    summ = await pubmed._rate_limited_get(
        f"{_EUTILS}/esummary.fcgi", {"db": "pubmed", "id": ",".join(ids)}
    )
    out = []
    for pmid in ids:
        s = summ["result"].get(pmid) or {}
        doi = next((a["value"] for a in s.get("articleids", []) if a["idtype"] == "doi"), "")
        year = (s.get("pubdate") or "")[:4]
        out.append(
            {
                "pmid": pmid,
                "title": s.get("title", ""),
                "journal": s.get("source", ""),
                "year": int(year) if year.isdigit() else None,
                "doi": doi,
                "authors": [a["name"] for a in s.get("authors", [])][:6],
                "pubtypes": s.get("pubtype", []),
            }
        )
    return out


async def _pubmed(
    term: str, retmax: int, sort: str, history: dict[str, str] | None = None, shown: str = ""
) -> dict[str, Any]:
    total, ids = await _esearch(term, retmax, sort, history)
    return {"query": shown or term, "sort": sort, "total": total, "records": await _summaries(ids)}


async def _icite(pmids: list[str]) -> dict[str, dict[str, Any]]:
    """NIH iCite citation metrics (public domain) keyed by PMID; unknown PMIDs are absent."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pmids), 200):  # URL-encoded ids; 400 hits HTTP 413
        d = await _get_json(_ICITE, pmids=",".join(pmids[i : i + 200]), fl=_ICITE_FIELDS)
        for r in d.get("data", []):
            rcr = r.get("relative_citation_ratio")
            out[str(r["pmid"])] = {
                "citation_count": r.get("citation_count") or 0,
                "rcr": round(rcr, 2) if isinstance(rcr, int | float) else None,
                "is_research_article": r.get("is_research_article"),
                "is_clinical": r.get("is_clinical"),
            }
    return out


async def _summaries_batched(ids: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), 200):
        out.extend(await _summaries(ids[i : i + 200]))
    return out


async def _gene2pubmed(entrez: str) -> list[str]:
    if not entrez:
        return []
    d = await pubmed._rate_limited_get(
        f"{_EUTILS}/elink.fcgi",
        {"dbfrom": "gene", "db": "pubmed", "id": entrez, "linkname": "gene_pubmed"},
    )
    sets = ((d.get("linksets") or [{}])[0]).get("linksetdbs") or []
    return [str(x) for x in sets[0].get("links", [])] if sets else []


async def curated_pmids(ctx: Ctx) -> list[str]:
    """The PMIDs NCBI links to the gene (gene2pubmed: curated, GeneRIFs included), fetched
    once per run; a failed fetch is retried by the next caller."""
    if ctx.curated is None or (ctx.curated.done() and ctx.curated.exception() is not None):
        ctx.curated = asyncio.ensure_future(_gene2pubmed(ctx.entrez))
    return await asyncio.shield(ctx.curated)


async def _curated_history(entrez: str) -> dict[str, str]:
    d = await pubmed._rate_limited_get(
        f"{_EUTILS}/elink.fcgi",
        {
            "dbfrom": "gene",
            "db": "pubmed",
            "id": entrez,
            "linkname": "gene_pubmed",
            "cmd": "neighbor_history",
        },
    )
    ls = (d.get("linksets") or [{}])[0]
    key = (ls.get("linksetdbhistories") or [{}])[0].get("querykey")
    if not ls.get("webenv") or not key:
        raise RuntimeError("NCBI elink returned no history for the gene's PubMed links")
    return {"WebEnv": ls["webenv"], "usehistory": "y", "query_key": str(key)}


def curated_mode(ctx: Ctx) -> bool:
    """The gene's literature is searched in its curated papers: most keyword hits are not
    about the gene (``literature_scope``)."""
    return (ctx.data.get("literature_scope") or {}).get("basis") == "curated"


async def search_base(ctx: Ctx) -> tuple[str, dict[str, str] | None, str]:
    """(PubMed clause, history parameters, clause as shown) the literature lists start from:
    the keyword clause, or in curated mode the gene's curated papers on NCBI's history
    server (filters are added to it like to a query)."""
    if not curated_mode(ctx):
        return tiab(ctx), None, tiab(ctx)
    if ctx.history is None or (ctx.history.done() and ctx.history.exception() is not None):
        ctx.history = asyncio.ensure_future(_curated_history(ctx.entrez))
    h = dict(await asyncio.shield(ctx.history))
    key = h.pop("query_key")
    return f"#{key}", h, f"NCBI gene2pubmed {ctx.symbol} (Gene {ctx.entrez})"


def _pubmed_url(term: Callable[[Ctx], str], sort: str = "") -> Callable[[Ctx, Any], str]:
    """Link the exact query the fetch runs (date filters, previous symbols), even when it
    failed; in curated mode, the gene's linked PubMed records."""
    return lambda c, _d: (
        f"https://pubmed.ncbi.nlm.nih.gov/?linkname=gene_pubmed&from_uid={c.entrez}"
        if curated_mode(c)
        else f"https://pubmed.ncbi.nlm.nih.gov/?term={quote_plus(term(c))}"
        + (f"&sort={sort}" if sort else "")
    )


def search_symbols(ctx: Ctx) -> list[str]:
    """Current symbol plus HGNC previous symbols that look like gene symbols.

    Literature and registries keep using retired symbols for years (SEPTIN9 is still "SEPT9"
    in most papers). Only previous symbols of ≥ 4 characters containing a digit are used;
    short or digit-free ones ("MSF") and aliases are too ambiguous for keyword search.
    """
    prev = [p for p in ctx.hgnc.get("prev_symbol") or [] if len(p) >= 4 and re.search(r"\d", p)]
    return list(dict.fromkeys([ctx.symbol, *prev]))


def tiab(ctx: Ctx) -> str:
    """PubMed title/abstract clause for the gene, e.g. ("SEPTIN9"[tiab] OR "SEPT9"[tiab])."""
    terms = [f'"{s}"[tiab]' for s in search_symbols(ctx)]
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def _reviews(base: str) -> str:
    return f'{base} AND review[pt] AND ("{date.today().year - 5}"[dp] : "3000"[dp])'


def _recent(base: str) -> str:
    return f'{base} AND ("{date.today().year - 2}"[dp] : "3000"[dp])'


def _disease(base: str, disease: str) -> str:
    return f'{base} AND "{disease}"[tiab]' if disease else base


def q_reviews(ctx: Ctx) -> str:
    return _reviews(tiab(ctx))


def q_recent(ctx: Ctx) -> str:
    return _recent(tiab(ctx))


def q_disease(ctx: Ctx) -> str:
    """Gene AND disease; gene only when no disease was given (never AND an empty phrase)."""
    return _disease(tiab(ctx), ctx.disease)


def _pm_n(ctx: Ctx, n: int) -> int:
    return n * 2 if ctx.depth == "deep" else n


async def fetch_pubmed_counts(ctx: Ctx) -> dict[str, Any]:
    y = date.today().year
    base, history, shown = await search_base(ctx)
    years = list(range(y - 9, y + 1))
    tot, recent, *per_year = await asyncio.gather(
        _esearch(base, 0, history=history),
        _esearch(f'{base} AND ("{y - 4}"[dp] : "3000"[dp])', 0, history=history),
        *(_esearch(f'{base} AND "{yr}"[dp]', 0, history=history) for yr in years),
    )
    return {
        "query": shown,
        "total": tot[0],
        f"since_{y - 4}": recent[0],
        "by_year": {str(yr): n for yr, (n, _) in zip(years, per_year, strict=True)},
    }


# Curated PMIDs ranked for the most-cited list (iCite takes 200 a request).
_CURATED_POOL = 10_000


async def fetch_pubmed_cited(ctx: Ctx) -> dict[str, Any]:
    """Most-cited papers: the gene's curated papers (NCBI gene2pubmed) plus the top-1000
    PubMed hits by relevance and the top RCTs and meta-analyses, re-ranked by iCite
    citations.

    Relevance order alone surfaces recent, keyword-dense papers, so a target's landmarks
    fell outside the pool (PCSK9: FOURIER, Cohen 2006); all four PCSK9 landmarks are in
    gene2pubmed. Keyword hits that are not about the gene ("MET" as a word) stay in the
    pool and are marked by ``pubtator_map``; the list keeps more papers than the digest
    shows so the grounded ones still fill it.
    """
    term, history, shown = await search_base(ctx)
    total, ids = await _esearch(term, 1000, history=history)
    _n, trial_ids = await _esearch(
        f'{term} AND ("randomized controlled trial"[pt] OR "meta-analysis"[pt])',
        300,
        history=history,
    )
    _n, genetics_ids = await _esearch(
        f'{term} AND ("loss-of-function"[tiab] OR "gain-of-function"[tiab] OR mutations[ti] '
        'OR variants[ti] OR "mendelian randomization"[tiab])',
        200,
        history=history,
    )
    curated = (await curated_pmids(ctx))[:_CURATED_POOL]
    ids = list(dict.fromkeys([*curated, *ids, *trial_ids, *genetics_ids]))
    metrics = await _icite(ids)
    ranked = sorted(metrics, key=lambda p: -metrics[p]["citation_count"])[: _pm_n(ctx, 40)]
    records = await _summaries(ranked)
    in_curated = set(curated)
    for r in records:
        r.update(metrics[r["pmid"]])
        r["curated"] = r["pmid"] in in_curated
    return {
        "query": shown,
        "sort": "citation_count",
        "total": total,
        "ranked_pool": len(ids),
        "curated_in_pool": len(curated),
        "records": records,
    }


async def fetch_pubmed_top(ctx: Ctx) -> dict[str, Any]:
    base, history, shown = await search_base(ctx)
    if history is not None:  # a stored set has no terms to rank by: rank by the keyword in it
        base, shown = f"{base} AND {tiab(ctx)}", f"{shown} AND {tiab(ctx)}"
    return await _pubmed(base, _pm_n(ctx, 20), "relevance", history, shown)


async def fetch_pubmed_reviews(ctx: Ctx) -> dict[str, Any]:
    base, history, shown = await search_base(ctx)
    return await _pubmed(_reviews(base), _pm_n(ctx, 15), "pub_date", history, _reviews(shown))


async def fetch_pubmed_recent(ctx: Ctx) -> dict[str, Any]:
    base, history, shown = await search_base(ctx)
    return await _pubmed(_recent(base), _pm_n(ctx, 20), "pub_date", history, _recent(shown))


async def fetch_pubmed_disease(ctx: Ctx) -> dict[str, Any]:
    base, history, shown = await search_base(ctx)
    return await _pubmed(
        _disease(base, ctx.disease),
        _pm_n(ctx, 25),
        "relevance",
        history,
        _disease(shown, ctx.disease),
    )


_PUBMED_LISTS = ("pubmed_cited", "pubmed_top", "pubmed_reviews", "pubmed_recent", "pubmed_disease")
# Specs whose payload carries PubMed ``records`` (esummary) that become citable sources.
_RECORD_KEYS = (*_PUBMED_LISTS, "ncbi_gene", "pubtator_map", "allelic_series")


def _record_pmids(ctx: Ctx, keys: tuple[str, ...] = _RECORD_KEYS) -> list[str]:
    return list(
        dict.fromkeys(r["pmid"] for k in keys for r in (ctx.data.get(k) or {}).get("records", []))
    )


async def fetch_ncbi_gene(ctx: Ctx) -> dict[str, Any]:
    """NCBI Gene: the RefSeq summary, the GeneRIFs (NLM-curated one-line statements of
    function, each tied to a PMID) and the count of curated gene–PubMed links.

    GeneRIFs are the direct comments of the record; the ones nested under interactions
    belong to the interacting pairs, not to the gene. The most-cited and the newest are
    kept with their PubMed metadata so they can be cited.
    """
    xml = await pubmed._rate_limited_get_text(
        f"{_EUTILS}/efetch.fcgi", {"db": "gene", "id": ctx.entrez, "retmode": "xml"}
    )
    root = ET.fromstring(xml)
    summary = (root.findtext(".//Entrezgene_summary") or "").strip()
    comments = root.find(".//Entrezgene_comments")
    rifs: dict[str, str] = {}
    for c in comments.findall("Gene-commentary") if comments is not None else []:
        kind = c.find("Gene-commentary_type")
        text = (c.findtext("Gene-commentary_text") or "").strip()
        pmid = c.findtext(".//PubMedId")
        if kind is not None and kind.get("value") == "generif" and text and pmid:
            rifs.setdefault(pmid, text)
    metrics = await _icite(list(rifs))
    cites = {p: (metrics.get(p) or {}).get("citation_count") or 0 for p in rifs}
    top = sorted(rifs, key=lambda p: -cites[p])[: _pm_n(ctx, 25)]
    newest = sorted(rifs, key=int, reverse=True)[: _pm_n(ctx, 10)]
    shown = list(dict.fromkeys([*top, *newest]))
    curated = await curated_pmids(ctx)
    return {
        "summary": summary,
        "generif_count": len(rifs),
        "generifs": [
            {"pmid": p, "text": rifs[p], "citation_count": cites[p], "newest": p in newest}
            for p in shown
        ],
        "gene2pubmed_count": len(curated),
        "records": await _summaries_batched(shown),
    }


# PubTator documents read for the literature map: the whole curated set up to this many,
# else half by citations and half newest.
_MAP_CURATED_CAP = 2000
# Keyword precision sample: this many hits from each of these publication years ago.
_SAMPLE_PER_YEAR = 10
_SAMPLE_YEARS = (1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


async def keyword_sample(term: str) -> list[str]:
    """Keyword hits drawn without regard to topic, to measure how many are about the gene.

    Relevance order puts the gene's papers first ("MET": 88 of the top 100 name the gene,
    against 2,891 curated papers in 345,759 hits), and PubMed pages only the first 10,000,
    so the sample takes the latest hits of ten publication years instead."""
    y = date.today().year
    found = await asyncio.gather(
        *(
            _esearch(f'{term} AND "{y - k}"[dp]', _SAMPLE_PER_YEAR, sort="pub_date")
            for k in _SAMPLE_YEARS
        )
    )
    return list(dict.fromkeys(p for _n, ids in found for p in ids))


async def target_identity(ctx: Ctx) -> Any:
    """The gene as identifiers for PubTator grounding (NCBI Gene id, orthologs, HGNC
    names)."""
    from mcp_servers.ontology.tools import ncbi_orthologs
    from services.evidence.grounding import TargetIdentity

    return TargetIdentity(
        symbol=ctx.symbol,
        ncbi_id=ctx.entrez,
        name=str(ctx.hgnc.get("name") or ""),
        aliases=list(ctx.hgnc.get("alias_symbol") or []),
        previous_symbols=list(ctx.hgnc.get("prev_symbol") or []),
        ortholog_ids=await ncbi_orthologs(ctx.entrez) if ctx.entrez else [],
    )


async def rehoming_records(docs: list[Any], target: Any) -> dict[str, Any]:
    """HGNC records of the genes the approved-symbol rule asks about."""
    from mcp_servers.ontology.tools import hgnc_by_entrez
    from services.evidence.grounding import needs_rehoming

    ids = sorted(set().union(*(needs_rehoming(d, target) for d in docs))) if docs else []
    return {i: await hgnc_by_entrez(i) for i in ids}


async def fetch_literature_scope(ctx: Ctx) -> dict[str, Any]:
    """Whether the keyword search finds the gene's literature: the share of keyword hits,
    drawn across ten publication years, that PubTator3 ties to the gene. Below
    ``MIN_KEYWORD_PRECISION`` the literature lists search the gene's curated papers
    instead (curated mode), and the volume reported is the curated count."""
    from profile_literature import build_map, volume

    from mcp_servers.pubtator.tools import get_annotations

    keyword_total, _ids = await _esearch(tiab(ctx), 0)
    sample = await keyword_sample(tiab(ctx))
    docs = await get_annotations(sorted(sample, key=int))
    target = await target_identity(ctx)
    m = build_map(docs, target, hgnc=await rehoming_records(docs, target), keyword_sample=sample)
    curated = await curated_pmids(ctx)
    return {
        **volume(keyword_total, len(curated), m["keyword"]["precision"]),
        "keyword": m["keyword"],
        "query": tiab(ctx),
    }


async def fetch_pubtator_map(ctx: Ctx) -> dict[str, Any]:
    """The literature map (profile_literature.py): PubTator3 annotations of the curated
    papers and every listed paper, aggregated by identifier."""
    from profile_literature import build_map, cited_pmids

    from mcp_servers.pubtator.tools import get_annotations

    curated = await curated_pmids(ctx)
    sent = curated
    if len(curated) > _MAP_CURATED_CAP:
        m = await _icite(curated)
        by_cites = sorted(curated, key=lambda p: -((m.get(p) or {}).get("citation_count") or 0))
        newest = sorted(curated, key=int, reverse=True)
        sent = list(dict.fromkeys([*by_cites[: _MAP_CURATED_CAP // 2], *newest]))[:_MAP_CURATED_CAP]
    rif_pmids = [r["pmid"] for r in (ctx.data.get("ncbi_gene") or {}).get("generifs", [])]
    listed = list(dict.fromkeys([*_record_pmids(ctx, _PUBMED_LISTS), *rif_pmids]))
    # Ascending: the newest PMIDs, which PubTator may not have annotated yet, share batches.
    pmids = sorted({*sent, *listed}, key=int)
    docs = await get_annotations(pmids)
    target = await target_identity(ctx)
    hgnc = await rehoming_records(docs, target)
    metrics = await _icite([d.pmid for d in docs])
    drugs = [
        d.get("drug_name", "") for d in (ctx.data.get("ot_known_drugs") or {}).get("drugs", [])
    ]
    out = build_map(
        docs,
        target,
        hgnc=hgnc,
        drug_names=drugs,
        citations={p: v.get("citation_count") or 0 for p, v in metrics.items()},
        listed=listed,
        requested=len(pmids),
    )
    scope = ctx.data.get("literature_scope") or {}
    out["curated"] = {"total": len(curated), "sent": len(sent)}
    out["keyword"] = scope.get("keyword") or out["keyword"]
    out["volume"] = {k: v for k, v in scope.items() if k not in ("keyword", "query")}
    out["records"] = await _summaries_batched(cited_pmids(out))
    return out


def _need_entrez(c: Ctx) -> str | None:
    return None if c.entrez else "no NCBI Gene id in HGNC"


async def fetch_clinvar(ctx: Ctx) -> dict[str, Any]:
    """ClinVar's pathogenic / likely pathogenic calls for the gene, from the system of
    record (``mcp_servers.clinvar``): per allele the accession, the condition(s) the
    classification is for, the review status and the number of submissions, which gnomAD's
    ClinVar mirror drops. Imported here, not at the top: a connector release without it
    fails this source alone, and the series falls back to the mirror."""
    from mcp_servers.clinvar import tools as clinvar

    return to_jsonable(await clinvar.get_pathogenic_variants(ctx.symbol))


async def fetch_allelic_series(ctx: Ctx) -> dict[str, Any]:
    """The allelic series and the variant spectrum (profile_tables.py), with the Open
    Targets effect records of the variants that have rs ids. ClinVar comes from its own
    record when that was fetched (conditions, submissions), else from gnomAD's mirror."""
    from profile_tables import MAX_ROWS, allelic_series, series_rsids, variant_spectrum

    uni = ctx.data.get("uniprot_extra") or {}
    lit = ctx.data.get("pubtator_map") or {}
    gw = ctx.data.get("gwas") or {}
    clinvar_key = "clinvar" if ctx.data.get("clinvar") else "gnomad_clinvar"
    clinvar = ctx.data.get(clinvar_key) or {}
    lof = ctx.data.get("gnomad_lof") or {}
    rsids = series_rsids(uni, gw, lit)
    found = await asyncio.gather(
        *(ot.get_variant_target_effects(r, ctx.ensembl) for r in rsids), return_exceptions=True
    )
    effects = {
        r: to_jsonable(f)
        for r, f in zip(rsids, found, strict=True)
        if f and not isinstance(f, BaseException)
    }
    series = allelic_series(
        uniprot=uni,
        clinvar=clinvar,
        lof=lof,
        gwas=gw,
        literature=lit,
        effects=effects,
        clinvar_key=clinvar_key,
    )
    pmids = [
        k.removeprefix("pmid:")
        for r in series["rows"][:MAX_ROWS]
        for k in r["refs"]
        if k.startswith("pmid:")
    ]
    return {
        "series": series,
        "spectrum": variant_spectrum(clinvar, lof, uni.get("features") or [], clinvar_key),
        "effects_looked_up": rsids,
        "effects": effects,
        "records": await _summaries_batched(list(dict.fromkeys(pmids))),
    }


async def fetch_pubmed_abstracts(ctx: Ctx) -> dict[str, Any]:
    """Abstracts and citation metrics for every paper the profile can cite: the PubMed
    lists, the GeneRIFs, the literature map and the allelic series.

    Cached so the writer reads abstracts from raw/ before citing a paper (one jq call instead
    of one network call per PMID), and so every listed paper carries a citation count.
    """
    pmids = _record_pmids(ctx)
    abstracts: dict[str, str] = {}
    for i in range(0, len(pmids), 100):
        abstracts.update(await pubmed._fetch_abstracts_batch(pmids[i : i + 100]))
    metrics = await _icite(pmids)
    papers = {
        p: {
            **metrics.get(p, {"citation_count": None, "rcr": None}),
            "abstract": abstracts.get(p, ""),
        }
        for p in pmids
    }
    return {
        "count": len(papers),
        "with_abstract": sum(1 for v in papers.values() if v["abstract"]),
        "papers": papers,
    }


def _need_pubmed_records(c: Ctx) -> str | None:
    has = any((c.data.get(k) or {}).get("records") for k in _RECORD_KEYS)
    return None if has else "no PubMed records to annotate"


def ct_gene_params(ctx: Ctx) -> dict[str, Any]:
    return {
        "query.term": " OR ".join(search_symbols(ctx)),
        "countTotal": "true",
        "pageSize": 50,
        "fields": "NCTId,BriefTitle,OverallStatus,Phase,LeadSponsorName,Condition,"
        "InterventionName,StartDate",
        "sort": "StartDate:desc",
    }


async def fetch_ct_gene(ctx: Ctx) -> dict[str, Any]:
    d = await _get_json(_CTGOV, **ct_gene_params(ctx))
    trials = []
    for s in d.get("studies", []):
        p = s.get("protocolSection", {})
        trials.append(
            {
                "nct_id": p.get("identificationModule", {}).get("nctId", ""),
                "title": p.get("identificationModule", {}).get("briefTitle", ""),
                "status": p.get("statusModule", {}).get("overallStatus", ""),
                "start": (p.get("statusModule", {}).get("startDateStruct") or {}).get("date", ""),
                "phases": p.get("designModule", {}).get("phases", []),
                "sponsor": (p.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get(
                    "name", ""
                ),
                "conditions": p.get("conditionsModule", {}).get("conditions", [])[:5],
                "interventions": [
                    i.get("name", "")
                    for i in p.get("armsInterventionsModule", {}).get("interventions", [])
                ][:5],
            }
        )
    return {
        "query_term": " OR ".join(search_symbols(ctx)),
        "total": d.get("totalCount", len(trials)),
        "trials": trials,
    }


async def fetch_ct_drugs(ctx: Ctx) -> dict[str, Any]:
    """Largest interventional trials of the target's drugs (any indication; the disease
    first when one is given).

    Trial records name the drug, rarely the gene, so ``ct_gene`` misses the landmark
    outcome trials (PCSK9: FOURIER, ODYSSEY OUTCOMES, VESALIUS-CV) and drug-code-only
    programmes (TRPC6: BI 764198's basket and COVID-19 trials). Drug names come from
    Open Targets known drugs plus the investigational products of gene-matched trials —
    the same rule the pipeline's trial retrieval uses.
    """
    from mcp_servers.clinicaltrials.tools import (
        TrialRecord,
        drug_named_in,
        search_trials_for_drugs,
    )
    from services.retrieval.clinical_trial import drug_query_names, known_drug_names

    probe = await _get_json(
        _CTGOV,
        **{
            "query.term": " OR ".join(search_symbols(ctx)),
            "pageSize": 100,
            "fields": "NCTId,BriefTitle,InterventionName",
        },
    )
    records = [
        TrialRecord(
            nct_id=p.get("identificationModule", {}).get("nctId", ""),
            title=p.get("identificationModule", {}).get("briefTitle", ""),
            interventions=[
                i.get("name", "")
                for i in p.get("armsInterventionsModule", {}).get("interventions", [])
            ],
        )
        for p in (s.get("protocolSection", {}) for s in probe.get("studies", []))
    ]
    drugs = drug_query_names(
        await known_drug_names(ctx.ensembl, ctx.disease or ""), records, ctx.symbol
    )
    found = await search_trials_for_drugs(
        drugs, ctx.disease or "", per_drug_disease=10, per_drug_any=8
    )
    # The registry's intervention search is fuzzy: keep a trial only when it names one of
    # the drugs, or (registry synonym) was found in the profiled disease.
    found = [r for r in found if r.query_in_disease or any(drug_named_in(r, d) for d in drugs)]
    found.sort(key=lambda r: -(r.enrollment or 0))
    return {
        "drugs": drugs,
        "total": len(found),
        "trials": [
            {
                "nct_id": r.nct_id,
                "title": r.title,
                "status": r.status,
                "phases": [r.phase] if r.phase else [],
                "enrollment": r.enrollment,
                "sponsor": r.sponsor,
                "conditions": r.conditions[:5],
                "interventions": r.interventions[:5],
                "has_results": bool(r.results),
            }
            for r in found[:40]
        ],
    }


_SALT_SUFFIXES = {
    "SODIUM",
    "POTASSIUM",
    "CALCIUM",
    "MAGNESIUM",
    "HYDROCHLORIDE",
    "HCL",
    "MESYLATE",
    "TOSYLATE",
    "BESYLATE",
    "MALEATE",
    "FUMARATE",
    "SUCCINATE",
    "TARTRATE",
    "CITRATE",
    "SULFATE",
    "ACETATE",
    "PHOSPHATE",
    "BROMIDE",
}
_FAERS_CANDIDATES = 6
_FAERS_KEEP = 3


def faers_names(drugs: list[dict[str, Any]]) -> list[str]:
    """Approved drug names with salt forms merged ('INCLISIRAN SODIUM' → 'INCLISIRAN')."""
    names: list[str] = []
    for d in drugs:
        if not d.get("is_approved"):
            continue
        words = d["drug_name"].upper().split()
        while len(words) > 1 and words[-1] in _SALT_SUFFIXES:
            words.pop()
        names.append(" ".join(words))
    return list(dict.fromkeys(names))[:_FAERS_CANDIDATES]


async def fetch_faers(ctx: Ctx) -> dict[str, Any]:
    """FAERS for the approved drugs with the most reports.

    Open Targets marks drugs approved in any jurisdiction; a drug never marketed in the US has
    no FAERS reports, so query a few candidates and keep the best-documented ones.
    """
    names = faers_names((ctx.data.get("ot_known_drugs") or {}).get("drugs", []))
    results = await asyncio.gather(
        *(openfda.search_adverse_events(n) for n in names), return_exceptions=True
    )
    found = {n: r for n, r in zip(names, results, strict=True) if not isinstance(r, BaseException)}
    reported = sorted(
        (n for n in found if found[n].total_reports), key=lambda n: -found[n].total_reports
    )
    return {
        "drugs_queried": names,
        "no_faers_reports": [n for n in found if not found[n].total_reports],
        "errors": {
            n: str(r) for n, r in zip(names, results, strict=True) if isinstance(r, BaseException)
        },
        "by_drug": {n: to_jsonable(found[n]) for n in reported[:_FAERS_KEEP]},
    }


async def fetch_uspto(ctx: Ctx) -> dict[str, Any]:
    """Granted US patents by title, split into target IP and indication-only IP.

    The ODP title query matches the gene OR the disease (in gene-only runs the symbol twice, i.e.
    a gene-only search). Counts are precomputed so the report can anchor them.
    """
    records = await uspto.search_patents(
        ctx.symbol, ctx.disease or ctx.symbol, with_abstracts=False
    )
    target = sum(1 for r in records if r.title_mentions_gene)
    return {
        "patents": to_jsonable(records),
        "total": len(records),
        "target_count": target,
        "indication_only_count": len(records) - target,
        "capped": len(records) >= uspto._MAX_RESULTS,
    }


def uspto_url(ctx: Ctx, _d: Any) -> str:
    """The ODP query search_patents runs, as a GET (needs the X-API-KEY header to open)."""
    title = f'applicationMetaData.inventionTitle:("{ctx.symbol}" "{ctx.disease or ctx.symbol}")'
    status = 'applicationMetaData.applicationStatusDescriptionText:"Patented Case"'
    return _api(uspto._ODP_SEARCH_URL, q=f"{title} AND {status}")


def orphanet_url(ctx: Ctx, d: Any) -> str:
    """Orphanet page of the first associated disorder (the bulk XML has no per-gene page)."""
    d = d if isinstance(d, dict) else {}
    assoc = d.get("associations") or (ctx.data.get("orphanet_associations") or {}).get(
        "associations", []
    )
    code = _first(d.get("orphacodes")) or (assoc[0]["orphacode"] if assoc else "")
    return f"https://www.orpha.net/en/disease/detail/{code}" if code else ""


async def fetch_orphanet_prevalence(ctx: Ctx) -> Any:
    assoc = (ctx.data.get("orphanet_associations") or {}).get("associations", [])
    codes = list(dict.fromkeys(a["orphacode"] for a in assoc))[:10]
    return await orphanet.get_orphanet_prevalence(codes)


def _chembl_id(ctx: Ctx) -> str:
    return str((ctx.data.get("uniprot_profile") or {}).get("chembl_target_id") or "")


# ------------------------------------------------------------------------ specs

_OT = "https://platform.opentargets.org/target/{e}"


def _ot(suffix: str = "") -> Callable[[Ctx, Any], str]:
    return lambda c, _d: _OT.format(e=c.ensembl) + suffix


def _first(v: Any) -> str:
    return str((v[0] if v else "") if isinstance(v, list) else (v or ""))


def _const(url: str) -> Callable[[Ctx, Any], str]:
    """A templated record URL; "" when a placeholder it needs is unknown (never a half-filled link)."""

    def build(c: Ctx, _d: Any) -> str:
        vals = {
            "s": c.symbol,
            "n": c.entrez,
            "e": c.ensembl,
            "u": c.uniprot,
            "h": c.hgnc.get("hgnc_id", ""),
            "o": _first(c.hgnc.get("omim_id")),
            "m": _first(c.hgnc.get("mgd_id")),
            "d": quote(c.disease),
            "di": c.disease_id,
        }
        used = re.findall(r"\{(\w+)\}", url)
        return url.format(**vals) if all(vals[k] for k in used) else ""

    return build


def _api(base: str, **params: Any) -> str:
    """The exact API request as a URL (percent-encoded, so it survives as a markdown link)."""
    return f"{base}?{urlencode(params, quote_via=quote)}"


def _link_or(url: str | Callable[[Ctx, Any], str]) -> Callable[[Ctx, Any], str]:
    """The connector's own source_link (re-encoded) when it returned one, else ``url``."""
    fallback = _const(url) if isinstance(url, str) else url
    return lambda c, d: (
        _encode(d["source_link"])
        if isinstance(d, dict) and d.get("source_link")
        else fallback(c, d)
    )


def _encode(url: str) -> str:
    """Percent-encode a connector link's query ('search=x:"a b"' breaks a markdown link)."""
    base, sep, query = url.partition("?")
    return f"{base}?{urlencode(parse_qsl(query), quote_via=quote)}" if sep else url


SPECS: list[Spec] = [
    # identity -----------------------------------------------------------------
    Spec(
        "hgnc",
        "identity",
        "HGNC",
        "HGNC gene symbol report — {symbol}",
        "HGNC REST fetch/symbol",
        fetch_hgnc,
        _const("https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/{h}"),
        quick=True,
    ),
    Spec(
        "uniprot_profile",
        "identity",
        "UniProt",
        "UniProtKB entry {uniprot} — {symbol}",
        "uniprot.get_protein_profile",
        lambda c: uniprot.get_protein_profile(c.symbol),
        _const("https://www.uniprot.org/uniprotkb/{u}/entry"),
        quick=True,
    ),
    # biology ------------------------------------------------------------------
    Spec(
        "ncbi_gene",
        "biology",
        "NCBI Gene",
        "NCBI Gene {entrez} — RefSeq summary and GeneRIFs",
        "NCBI E-utilities efetch (gene) + elink (gene_pubmed); NIH iCite",
        fetch_ncbi_gene,
        _const("https://www.ncbi.nlm.nih.gov/gene/{n}"),
        skip=_need_entrez,
        quick=True,
    ),
    Spec(
        "uniprot_extra",
        "biology",
        "UniProt",
        "UniProtKB {uniprot} — GO, structures, domains, disease",
        "UniProt REST",
        fetch_uniprot_extra,
        _const("https://www.uniprot.org/uniprotkb/{u}/entry"),
        skip=_need("uniprot", "no UniProt accession"),
    ),
    Spec(
        "hpa",
        "biology",
        "Human Protein Atlas",
        "Human Protein Atlas — {symbol}",
        "HPA JSON",
        fetch_hpa,
        _const("https://www.proteinatlas.org/{e}"),
    ),
    Spec(
        "gtex",
        "biology",
        "GTEx",
        "GTEx gene expression — {symbol}",
        "gtex.get_expression",
        lambda c: gtex.get_expression(c.symbol, c.ensembl),
        _const("https://gtexportal.org/home/gene/{s}"),
        quick=True,
    ),
    Spec(
        "spoke_anatomy",
        "biology",
        "SPOKE",
        "SPOKE anatomy expression — {symbol}",
        "spoke.get_anatomy_expression",
        lambda c: spoke.get_anatomy_expression(c.symbol),
        _link_or("https://spoke.rbvi.ucsf.edu/neighborhood.html"),
    ),
    Spec(
        "expression_atlas",
        "biology",
        "Expression Atlas",
        "Expression Atlas differential expression — {symbol}",
        "expression_atlas.get_differential_expression",
        lambda c: gxa.get_differential_expression(c.symbol, c.disease, ensembl_id=c.ensembl),
        _const("https://www.ebi.ac.uk/gxa/genes/{e}"),
    ),
    Spec(
        "reactome",
        "biology",
        "Reactome",
        "Reactome pathways for {uniprot}",
        "Reactome ContentService",
        fetch_reactome,
        _const(
            "https://reactome.org/ContentService/data/mapping/UniProt/{u}/pathways?species=9606"
        ),
        skip=_need("uniprot", "no UniProt accession"),
    ),
    Spec(
        "string",
        "biology",
        "STRING",
        "STRING interaction partners — {symbol}",
        "STRING API",
        fetch_string,
        _const("https://string-db.org/cgi/network?identifiers={s}&species=9606"),
    ),
    Spec(
        "alphafold",
        "biology",
        "AlphaFold DB",
        "AlphaFold DB model AF-{uniprot}-F1",
        "AlphaFold DB API",
        fetch_alphafold,
        _const("https://alphafold.ebi.ac.uk/entry/{u}"),
        skip=_need("uniprot", "no UniProt accession"),
    ),
    Spec(
        "depmap",
        "biology",
        "DepMap",
        "DepMap gene dependency — {symbol}",
        "depmap.get_dependency",
        lambda c: depmap.get_dependency(c.symbol),
        _const("https://depmap.org/portal/gene/{s}"),
    ),
    Spec(
        "project_score",
        "biology",
        "Project Score",
        "Project Score CRISPR fitness — {symbol}",
        "project_score.get_project_score",
        lambda c: project_score.get_project_score(c.symbol),
        _link_or(lambda _c, _d: ""),  # the Project Score web UI is retired; no fallback page
    ),
    # genetics -----------------------------------------------------------------
    Spec(
        "gnomad_constraint",
        "genetics",
        "gnomAD",
        "gnomAD gene constraint — {symbol} ({ensembl})",
        "gnomad.get_constraint",
        lambda c: gnomad.get_constraint(c.symbol, c.ensembl),
        _const("https://gnomad.broadinstitute.org/gene/{e}?dataset=gnomad_r4"),
        quick=True,
    ),
    Spec(
        "gnomad_clinvar",
        "genetics",
        "ClinVar (via gnomAD)",
        "ClinVar variants in {symbol} (gnomAD v4 browser)",
        "gnomad.get_clinvar_variants",
        lambda c: gnomad.get_clinvar_variants(c.ensembl, c.symbol),
        _const("https://gnomad.broadinstitute.org/gene/{e}?dataset=gnomad_r4"),
    ),
    Spec(
        "clinvar",
        "genetics",
        "ClinVar",
        "ClinVar pathogenic / likely pathogenic records — {symbol}",
        "clinvar.get_pathogenic_variants (NCBI E-utilities esearch + esummary)",
        fetch_clinvar,
        _const("https://www.ncbi.nlm.nih.gov/clinvar/?term={s}%5Bgene%5D"),
    ),
    Spec(
        "gnomad_lof",
        "genetics",
        "gnomAD",
        "gnomAD pLoF variants — {symbol}",
        "gnomad.get_lof_variants",
        lambda c: gnomad.get_lof_variants(c.ensembl, c.symbol),
        _const("https://gnomad.broadinstitute.org/gene/{e}?dataset=gnomad_r4"),
    ),
    Spec(
        "clingen",
        "genetics",
        "ClinGen",
        "ClinGen gene–disease validity — {symbol}",
        "clingen.get_clingen_validity",
        lambda c: clingen.get_clingen_validity(c.symbol),
        _const("https://search.clinicalgenome.org/kb/genes/{h}"),
    ),
    Spec(
        "gencc",
        "genetics",
        "GenCC",
        "GenCC gene–disease submissions — {symbol}",
        "gencc.get_gencc_validity",
        lambda c: gencc.get_gencc_validity(c.symbol),
        _const("https://search.thegencc.org/genes/{h}"),
    ),
    Spec(
        "omim",
        "genetics",
        "OMIM",
        "OMIM gene–phenotype relationships — {symbol}",
        "omim.get_omim_validity",
        lambda c: omim.get_omim_validity(c.symbol),
        _const("https://www.omim.org/entry/{o}"),
        skip=lambda _c: (
            None if omim.omim_configured() else "gated off (OMIM_ENABLED / OMIM_API_KEY)"
        ),
    ),
    Spec(
        "orphanet_associations",
        "genetics",
        "Orphanet",
        "Orphanet gene–disorder associations — {symbol}",
        "orphanet.get_orphanet_associations",
        lambda c: orphanet.get_orphanet_associations(c.symbol),
        orphanet_url,
    ),
    Spec(
        "gwas",
        "genetics",
        "GWAS Catalog",
        "GWAS Catalog associations — {symbol}",
        "gwas_catalog.get_gwas_associations",
        lambda c: gwas.get_gwas_associations(c.symbol, max_hits=25),
        _const("https://www.ebi.ac.uk/gwas/genes/{s}"),
    ),
    Spec(
        "ot_coloc",
        "genetics",
        "Open Targets Platform",
        "Open Targets QTL–GWAS colocalisations — {symbol}",
        "opentargets.get_colocalizations",
        lambda c: ot.get_colocalizations(c.ensembl),
        _ot(),
    ),
    Spec(
        "hpo",
        "genetics",
        "HPO",
        "HPO gene–phenotype annotations — {symbol}",
        "ontology.get_gene_phenotypes",
        lambda c: ontology.get_gene_phenotypes(c.symbol),
        _const("https://monarchinitiative.org/{h}"),  # the connector reads HPO via Monarch
    ),
    Spec(
        "allelic_series",
        "genetics",
        "Allelic series (UniProt, ClinVar, gnomAD, GWAS Catalog, PubTator3, Open Targets)",
        "Allelic series and variant spectrum — {symbol}",
        "profile_tables.allelic_series; opentargets.get_variant_target_effects",
        fetch_allelic_series,
        _ot(),
        wave=4,
        timeout_s=300,
    ),
    # associations -------------------------------------------------------------
    Spec(
        "ot_associations",
        "associations",
        "Open Targets Platform",
        "Open Targets target–disease associations — {symbol}",
        "Open Targets GraphQL associatedDiseases",
        fetch_ot_gene_associations,
        _ot("/associations"),
        quick=True,
    ),
    Spec(
        "spoke_diseases",
        "associations",
        "SPOKE",
        "SPOKE gene–disease edges — {symbol}",
        "spoke.get_gene_disease_associations",
        lambda c: spoke.get_gene_disease_associations(c.symbol),
        _link_or("https://spoke.rbvi.ucsf.edu/neighborhood.html"),
    ),
    Spec(
        "ot_association_disease",
        "indication",
        "Open Targets Platform",
        "Open Targets association — {symbol} × {disease}",
        "opentargets.get_associations",
        lambda c: ot.get_associations(c.ensembl, c.disease_id),
        _const("https://platform.opentargets.org/evidence/{e}/{di}"),
        skip=lambda c: (
            _need_disease(c) or (None if c.disease_id else "disease not resolved in Open Targets")
        ),
    ),
    Spec(
        "ot_l2g",
        "indication",
        "Open Targets Platform",
        "Open Targets locus-to-gene scores — {symbol} × {disease}",
        "opentargets.get_l2g_scores",
        lambda c: ot.get_l2g_scores(c.ensembl, c.disease_id),
        _ot(),
        skip=lambda c: _need_disease(c) or (None if c.disease_id else "disease not resolved"),
    ),
    # druggability -------------------------------------------------------------
    Spec(
        "ot_tractability",
        "druggability",
        "Open Targets Platform",
        "Open Targets tractability — {symbol}",
        "opentargets.get_tractability",
        lambda c: ot.get_tractability(c.ensembl),
        _ot(),
        quick=True,
    ),
    Spec(
        "chembl",
        "druggability",
        "ChEMBL",
        "ChEMBL target {chembl} — bioactive compounds",
        "chembl.get_chemistry",
        lambda c: chembl.get_chemistry(_chembl_id(c), c.symbol),
        lambda c, _d: (
            f"https://www.ebi.ac.uk/chembl/explore/target/{_chembl_id(c)}" if _chembl_id(c) else ""
        ),
        wave=2,
        skip=lambda c: None if _chembl_id(c) else "no ChEMBL target cross-reference in UniProt",
    ),
    Spec(
        "dgidb_interactions",
        "druggability",
        "DGIdb",
        "DGIdb drug–gene interactions — {symbol}",
        "dgidb.get_gene_drug_interactions",
        lambda c: dgidb.get_gene_drug_interactions(c.symbol),
        _const("https://dgidb.org/genes/{s}"),
    ),
    Spec(
        "dgidb_categories",
        "druggability",
        "DGIdb",
        "DGIdb druggable-genome categories — {symbol}",
        "dgidb.get_gene_categories",
        lambda c: dgidb.get_gene_categories(c.symbol),
        _const("https://dgidb.org/genes/{s}"),
    ),
    Spec(
        "ttd",
        "druggability",
        "TTD",
        "Therapeutic Target Database — {symbol}",
        "ttd.get_ttd_target_status",
        lambda c: ttd.get_ttd_target_status(c.symbol),
        _link_or(lambda _c, _d: ""),  # the target page needs the TTD id; none when gated off
        skip=lambda _c: None if ttd.ttd_configured() else "gated off (TTD_ENABLED)",
    ),
    # clinical -----------------------------------------------------------------
    Spec(
        "ot_known_drugs",
        "clinical",
        "Open Targets Platform",
        "Open Targets known drugs — {symbol}",
        "opentargets.get_known_drugs",
        lambda c: ot.get_known_drugs(c.ensembl),
        _ot(),
        quick=True,
    ),
    Spec(
        "ct_gene",
        "clinical",
        "ClinicalTrials.gov",
        "ClinicalTrials.gov studies mentioning {symbol}",
        "ClinicalTrials.gov API v2",
        fetch_ct_gene,
        lambda c, _d: _api(_CTGOV, **ct_gene_params(c)),
    ),
    Spec(
        "ct_drugs",
        "clinical",
        "ClinicalTrials.gov",
        "ClinicalTrials.gov trials of {symbol}'s drugs",
        "ClinicalTrials.gov API v2 (query.intr, by enrolment)",
        fetch_ct_drugs,
        lambda c, _d: _api(_CTGOV, **{"query.intr": " OR ".join((_d or {}).get("drugs", [])[:3])}),
    ),
    Spec(
        "ct_gene_disease",
        "indication",
        "ClinicalTrials.gov",
        "ClinicalTrials.gov studies — {symbol} AND {disease}",
        "clinicaltrials.search_trials",
        lambda c: ctgov.search_trials(c.symbol, c.disease),
        lambda c, _d: _api(
            _CTGOV, **{"query.term": " AND ".join(x for x in (c.symbol, c.disease) if x)}
        ),
        skip=_need_disease,
    ),
    # safety -------------------------------------------------------------------
    Spec(
        "ot_safety",
        "safety",
        "Open Targets Platform",
        "Open Targets target safety liabilities — {symbol}",
        "opentargets.get_safety",
        lambda c: ot.get_safety(c.ensembl),
        _ot(),
        quick=True,
    ),
    Spec(
        "ot_mouse",
        "safety",
        "Open Targets Platform",
        "Open Targets mouse phenotypes (MGI/IMPC) — {symbol}",
        "opentargets.get_mouse_phenotypes",
        lambda c: ot.get_mouse_phenotypes(c.ensembl),
        _ot(),
    ),
    Spec(
        "impc",
        "safety",
        "IMPC",
        "IMPC knockout phenotypes — {symbol}",
        "impc.get_impc_phenotypes",
        lambda c: impc.get_impc_phenotypes(c.symbol, _first(c.hgnc.get("mgd_id"))),
        _link_or("https://www.mousephenotype.org/data/genes/{m}"),
    ),
    Spec(
        "faers",
        "safety",
        "openFDA FAERS",
        "FDA Adverse Event Reporting System — approved {symbol} drugs",
        "openfda.search_adverse_events",
        fetch_faers,
        lambda _c, d: next(
            (_encode(v["source_link"]) for v in ((d or {}).get("by_drug") or {}).values()), ""
        ),
        wave=2,
        skip=lambda c: (
            None
            if any(
                d.get("is_approved") for d in (c.data.get("ot_known_drugs") or {}).get("drugs", [])
            )
            else "no approved drug in Open Targets known drugs"
        ),
    ),
    # commercial ---------------------------------------------------------------
    Spec(
        "uspto",
        "commercial",
        "USPTO",
        "USPTO granted patents — {symbol}",
        "uspto.search_patents",
        # The ODP title query is ("<gene>" "<disease>"); passing the symbol twice gives a gene-only search.
        # Abstracts are OCR'd from per-patent PDFs (minutes for broad queries); the profile
        # cites titles, assignees and dates only.
        fetch_uspto,
        uspto_url,
        skip=lambda _c: None if os.environ.get("USPTO_API_KEY") else "USPTO_API_KEY not set",
    ),
    Spec(
        "orphanet_prevalence",
        "commercial",
        "Orphanet",
        "Orphanet prevalence — {symbol}-associated disorders",
        "orphanet.get_orphanet_prevalence",
        fetch_orphanet_prevalence,
        orphanet_url,
        wave=2,
        skip=lambda c: (
            None
            if (c.data.get("orphanet_associations") or {}).get("associations")
            else "no Orphanet disorders associated"
        ),
    ),
    Spec(
        "gbd",
        "commercial",
        "GBD (IHME)",
        "Global Burden of Disease — {disease}",
        "gbd.get_disease_burden",
        lambda c: gbd.get_disease_burden(c.disease, disease_id=c.disease_id),
        # A local GHDx extract: no public per-cause record to link (the Results Tool is generic).
        lambda _c, _d: "",
        # Disabled, the connector returns an empty bundle; that is "not checked", not "none found".
        skip=lambda c: (
            _need_disease(c)
            or (
                None
                if gbd._enabled() and gbd._data_path()
                else "gated off (GBD_ENABLED / GBD_DATA_PATH)"
            )
        ),
    ),
    Spec(
        "openfda_indication",
        "commercial",
        "openFDA",
        "FDA-labelled drugs for {disease}",
        "openfda.count_indication_drugs",
        lambda c: openfda.count_indication_drugs(c.disease),
        _link_or(lambda _c, _d: ""),
        skip=_need_disease,
    ),
    Spec(
        "ct_condition",
        "commercial",
        "ClinicalTrials.gov",
        "ClinicalTrials.gov trial landscape — {disease}",
        "clinicaltrials.count_condition_trials",
        lambda c: ctgov.count_condition_trials(c.disease),
        _link_or(lambda _c, _d: ""),
        skip=_need_disease,
    ),
    # regulatory ---------------------------------------------------------------
    Spec(
        "openfda_labels",
        "commercial",
        "openFDA",
        "FDA drug labels mentioning {symbol}",
        "openfda.search_drug_labels",
        lambda c: openfda.search_drug_labels(c.symbol, c.disease or c.symbol),
        lambda c, _d: _api(
            f"{_OPENFDA}/drug/label.json",
            search=f'mechanism_of_action:"{c.symbol}" '
            f'indications_and_usage:"{c.disease or c.symbol}"',  # space = OR in openFDA
            limit=openfda._LABEL_LIMIT,
        ),
    ),
    # literature ---------------------------------------------------------------
    Spec(
        "literature_scope",
        "literature",
        "PubMed + PubTator3",
        "Literature scope — keyword precision for {symbol}",
        "NCBI E-utilities esearch/elink; PubTator3 annotations",
        fetch_literature_scope,
        lambda c, _d: f"https://pubmed.ncbi.nlm.nih.gov/?term={quote_plus(tiab(c))}",
        skip=_need_entrez,
        quick=True,
    ),
    Spec(
        "pubmed_counts",
        "literature",
        "PubMed",
        "PubMed publication counts — {symbol}",
        "NCBI E-utilities esearch",
        fetch_pubmed_counts,
        _pubmed_url(tiab),
        quick=True,
        wave=2,
    ),
    Spec(
        "pubmed_top",
        "literature",
        "PubMed",
        "PubMed most relevant — {symbol}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_top,
        _pubmed_url(tiab),
        wave=2,
    ),
    Spec(
        "pubmed_cited",
        "literature",
        "PubMed + NIH iCite",
        "PubMed most-cited — {symbol} (iCite citation counts)",
        "NCBI E-utilities esearch+esummary; NIH iCite",
        fetch_pubmed_cited,
        _pubmed_url(tiab),
        wave=2,
    ),
    Spec(
        "pubmed_reviews",
        "literature",
        "PubMed",
        "PubMed recent reviews — {symbol}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_reviews,
        _pubmed_url(q_reviews, "date"),
        wave=2,
    ),
    Spec(
        "pubmed_recent",
        "literature",
        "PubMed",
        "PubMed last 24 months — {symbol}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_recent,
        _pubmed_url(q_recent, "date"),
        wave=2,
    ),
    Spec(
        "pubmed_disease",
        "indication",
        "PubMed",
        "PubMed — {symbol} AND {disease}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_disease,
        _pubmed_url(q_disease),
        skip=_need_disease,
        wave=2,
    ),
    Spec(
        "pubtator_map",
        "literature",
        "PubTator3",
        "PubTator3 literature map — {symbol} (NCBI Gene {entrez})",
        "mcp_servers.pubtator.get_annotations; profile_literature.build_map",
        fetch_pubtator_map,
        _const("https://www.ncbi.nlm.nih.gov/research/pubtator3/docsum?text=@GENE_{s}"),
        wave=3,
        skip=_need_entrez,
        timeout_s=600,
    ),
    Spec(
        "pubmed_abstracts",
        "literature",
        "PubMed + NIH iCite",
        "PubMed abstracts and iCite citation metrics — {symbol}",
        "NCBI E-utilities efetch; NIH iCite",
        fetch_pubmed_abstracts,
        lambda _c, d: (
            _api(_ICITE, pmids=",".join(d["papers"]), fl=_ICITE_FIELDS)
            if isinstance(d, dict) and d.get("papers")
            else ""
        ),
        wave=5,
        skip=_need_pubmed_records,
    ),
]
SPEC_BY_KEY = {s.key: s for s in SPECS}


# ------------------------------------------------------------------------ runner


def _title(spec: Spec, ctx: Ctx) -> str:
    return spec.title.format(
        symbol=ctx.symbol,
        ensembl=ctx.ensembl,
        uniprot=ctx.uniprot,
        entrez=ctx.entrez,
        disease=ctx.disease,
        chembl=_chembl_id(ctx),
    )


async def run_spec(spec: Spec, ctx: Ctx, run_dir: Path, sem: asyncio.Semaphore) -> RawRecord:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    base = {
        "key": spec.key,
        "section": spec.section,
        "db_name": spec.db_name,
        "tool": spec.tool,
        "retrieved_at": now,
        "args": {
            "gene": ctx.symbol,
            "ensembl": ctx.ensembl,
            "disease": ctx.disease,
            "disease_id": ctx.disease_id,
        },
    }
    reason = spec.skip(ctx)
    if reason:
        rec = RawRecord(status="skipped", note=reason, **base)
    else:
        async with sem:
            # One automatic retry for transient failures (timeouts, 429/5xx), then record the error.
            for attempt in (1, 2):
                try:
                    data = to_jsonable(await asyncio.wait_for(spec.fetch(ctx), spec.timeout_s))
                    status = "ok" if is_substantive(data) else "empty"
                    note = "succeeded on retry" if attempt == 2 else ""
                    rec = RawRecord(
                        status=status, data=data, url=spec.url(ctx, data), note=note, **base
                    )
                    if status == "ok":
                        ctx.data[spec.key] = data
                    break
                except TimeoutError:
                    # Our own budget expired; an immediate retry would only double the wait.
                    note = f"timed out after {spec.timeout_s}s"
                    rec = RawRecord(status="error", note=note, **base)
                    break
                except Exception as exc:  # noqa: BLE001 — every failure is recorded, never fatal
                    msg = f"{type(exc).__name__}: {exc}"[:400] or type(exc).__name__
                    rec = RawRecord(status="error", note=f"after 2 attempts: {msg}", **base)
                    if attempt == 1:
                        await asyncio.sleep(3)
    if not rec.url:
        # Skipped and failed sources still get a link, so readers can check them by hand.
        try:
            rec.url = spec.url(ctx, None)
        except Exception:  # noqa: BLE001 — some links need the payload (e.g. a ChEMBL id)
            rec.url = ""
    (run_dir / "raw" / f"{safe_filename(spec.key)}.json").write_text(rec.model_dump_json(indent=2))
    print(f"  {rec.status:<7} {spec.key:<24} {rec.note[:90]}", file=sys.stderr)
    return rec


def database_source(spec: Spec, rec: RawRecord, ctx: Ctx) -> Source:
    return Source(
        key=spec.key,
        kind="database",
        tier=1,
        title=_title(spec, ctx),
        url=rec.url,
        accessed=ctx.today,
        db_name=spec.db_name,
        db_version=ctx.db_versions.get(spec.db_name.split(" (")[0], ""),
        accession=ctx.ensembl,
        retrieved_via=spec.tool,
        verified=True,
        raw_file=f"raw/{safe_filename(spec.key)}.json",
    )


def mark_grounding(sources: dict[str, Source], literature_map: dict[str, Any]) -> None:
    """Record on each article whether PubTator3 found the target in it (None: not
    annotated), for the checker's off-target warning."""
    for pmid, info in (literature_map.get("papers") or {}).items():
        s = sources.get(f"pmid:{pmid}")
        if s is not None:
            s.grounded = bool(info.get("names_target"))


def derived_sources(rec: RawRecord, today: str) -> list[Source]:
    """Literature / trial / patent records inside a retrieval become citable sources too."""
    out: list[Source] = []
    d = rec.data
    if rec.status != "ok" or d is None:
        return out
    if rec.key in _RECORD_KEYS and isinstance(d, dict):
        for r in d.get("records", []):
            out.append(
                Source(
                    key=f"pmid:{r['pmid']}",
                    kind="article",
                    tier=1,
                    title=r["title"],
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/",
                    accessed=today,
                    authors=r["authors"],
                    venue=r["journal"],
                    year=r["year"],
                    pmid=r["pmid"],
                    doi=r["doi"],
                    retrieved_via="NCBI E-utilities esummary",
                    verified=True,
                )
            )
    trials = (
        (d.get("trials", []) if isinstance(d, dict) else d) if rec.key.startswith("ct_") else []
    )
    for t in trials if isinstance(trials, list) else []:
        nct = t.get("nct_id", "")
        if nct:
            out.append(
                Source(
                    key=f"nct:{nct}",
                    kind="trial",
                    tier=2,
                    title=t.get("title", ""),
                    url=f"https://clinicaltrials.gov/study/{nct}",
                    accessed=today,
                    nct_id=nct,
                    venue=t.get("sponsor", ""),
                    retrieved_via="ClinicalTrials.gov API v2",
                    verified=True,
                )
            )
    if rec.key == "uspto" and isinstance(d, dict):
        for p in d.get("patents", []):
            out.append(
                Source(
                    key=f"patent:{p['patent_id']}",
                    kind="patent",
                    tier=2,
                    title=p["title"],
                    url=p.get("uspto_link") or p.get("source_link") or "",
                    accessed=today,
                    patent_number=p["patent_id"],
                    venue=p.get("assignee", ""),
                    year=int(p["filing_date"][:4])
                    if p.get("filing_date", "")[:4].isdigit()
                    else None,
                    retrieved_via="USPTO ODP",
                    verified=True,
                )
            )
    return out


# ------------------------------------------------------------------------ digest


def _compact(v: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "…"
    if isinstance(v, dict):
        return {k: _compact(x, depth + 1) for k, x in v.items() if x not in (None, "", [], {})}
    if isinstance(v, list):
        head = [_compact(x, depth + 1) for x in v[:8]]
        return head + [f"… (+{len(v) - 8} more)"] if len(v) > 8 else head
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + "…"
    return v


def _cap(line: str, n: int) -> str:
    return line if len(line) <= n else line[:n] + " …"


# Most-cited papers shown in the digest (the list keeps more, for off-target ones).
_CITED_SHOWN = 15


def _literature_digest(
    data: dict[str, Any], metrics: dict[str, Any], grounding: dict[str, Any] | None = None
) -> str:
    """One line per paper, with its citation count and what PubTator3 found in it (the
    organisms it names); papers PubTator annotated without any mention of the target are
    left out and counted."""
    grounding = grounding or {}
    head = {k: v for k, v in data.items() if k != "records"}
    lines = [json.dumps(head, ensure_ascii=False)]
    hidden = 0
    shown = 0
    cap = _CITED_SHOWN if data.get("sort") == "citation_count" else None
    for r in data.get("records", []):
        g = grounding.get(r["pmid"])
        if g is not None and not g.get("names_target"):
            hidden += 1
            continue
        if cap is not None and shown >= cap:
            continue
        shown += 1
        m = metrics.get(r["pmid"]) or r
        cites = m.get("citation_count")
        cite = f"{cites} cites" + (f", RCR {m['rcr']}" if m.get("rcr") is not None else "")
        types = [t for t in r.get("pubtypes", []) if t != "Journal Article"]
        systems = ", ".join(g.get("organisms") or []) if g else ""
        lines.append(
            f"pmid:{r['pmid']} · {r.get('year') or '?'} · "
            f"{cite if cites is not None else 'cites n/a'} · {r.get('journal', '')}"
            + (f" · {', '.join(types)}" if types else "")
            + (f" · [{systems}]" if systems else "")
            + (" · (not annotated by PubTator)" if grounding and not g else "")
            + f" · {r.get('title', '')}"
        )
    if hidden:
        lines.append(
            f"({hidden} hit(s) PubTator3 shows are not about {data.get('query', 'the gene')} left out)"
        )
    return "\n".join(lines)


def _entity_line(e: dict[str, Any]) -> str:
    rel = ", ".join(f"{k} {v}" for k, v in (e.get("relations") or {}).items())
    pm = " ".join(f"pmid:{p}" for p in e.get("pmids") or [])
    return (
        f"- {e.get('name') or e.get('label') or e.get('id')} · {e['papers']} papers · {rel} · {pm}"
    )


def _map_digest(d: dict[str, Any]) -> str:
    """The literature map as lines the writer can select from."""
    vol = d.get("volume") or {}
    kw = d.get("keyword") or {}
    lines = [
        f"volume: {vol.get('total')} papers (basis {vol.get('basis')}; keyword "
        f"{vol.get('keyword_total')}, curated {vol.get('curated_total')}, keyword precision "
        f"{kw.get('precision')} = {kw.get('naming_target')}/{kw.get('annotated')} annotated hits "
        "name the target) — anchor {{pubtator_map:volume.total}}",
        f"annotated {d.get('papers_annotated')} of {d.get('papers_requested')} papers sent; "
        f"{d.get('papers_naming_target')} name the target; gene studied: "
        + json.dumps(d.get("gene_studied") or {}),
        "organisms: " + ", ".join(f"{o['name']} {o['papers']}" for o in d.get("organisms") or []),
        "cell lines: " + ", ".join(f"{c['name']} {c['papers']}" for c in d.get("cell_lines") or []),
    ]
    for group in ("diseases", "chemicals", "genes"):
        rows = d.get(group) or []
        lines.append(f"{group} related to the target (top {len(rows)}; relation types are tags):")
        lines += [_entity_line(e) for e in rows[:15]]
    rows = d.get("variants") or []
    lines.append(
        f"variants of the target named in papers (top {len(rows)} of {d.get('variant_count')}):"
    )
    for v in rows[:15]:
        rel = "; ".join(
            f"{r['name']} ({', '.join(r.get('relations') or {})}, {r['papers']})"
            for r in v.get("related") or []
        )
        pm = " ".join(f"pmid:{p}" for p in v.get("pmids") or [])
        lines.append(
            f"- {v.get('label')} {v.get('rsid') or ''} {v.get('hgvs') or ''} · {v['papers']} papers"
            + (f" · related: {rel}" if rel else "")
            + f" · {pm}"
        )
    return "\n".join(lines)


def _ncbi_gene_digest(d: dict[str, Any]) -> str:
    lines = [
        f"summary: {d.get('summary') or '(none)'}",
        f"gene2pubmed_count: {d.get('gene2pubmed_count')} · generif_count: {d.get('generif_count')}",
        "GeneRIFs (most cited, then newest):",
    ]
    years = {r["pmid"]: r.get("year") for r in d.get("records") or []}
    for r in d.get("generifs") or []:
        lines.append(
            f"- pmid:{r['pmid']} · {years.get(r['pmid']) or '?'} · {r.get('citation_count')} cites"
            f" · {r['text']}"
        )
    return "\n".join(lines)


def _clinvar_digest(d: dict[str, Any]) -> str:
    rows = [*(d.get("pathogenic") or []), *(d.get("likely_pathogenic") or [])]
    rows.sort(key=lambda v: (-(v.get("gold_stars") or 0), v.get("hgvsc") or ""))
    lines = [
        f"query: {d.get('query')} · {d.get('records_found')} records"
        + (" (capped)" if d.get("capped") else "")
        + f" · {len(rows)} P/LP calls on the gene's own alleles",
        "A classification is for the condition it names; weigh a call against the mechanism "
        "only with its condition, review status and submissions.",
    ]
    for v in rows[:60]:
        lines.append(
            f"- {v.get('accession')} · {v.get('transcript')}:{v.get('hgvsc')} "
            f"{v.get('hgvsp') or ''} · {v.get('major_consequence') or '—'} · "
            f"{v.get('clinical_significance')} · {v.get('gold_stars')}★ {v.get('review_status')} · "
            f"{v.get('submissions')} submissions · for: "
            + ("; ".join(c.get("name", "") for c in v.get("conditions") or []) or "—")
        )
    if len(rows) > 60:
        lines.append(f"… and {len(rows) - 60} more in raw/clinvar.json")
    return "\n".join(lines)


def _series_digest(d: dict[str, Any]) -> str:
    sp = d.get("spectrum") or {}
    rows = (d.get("series") or {}).get("rows") or []
    lines = [
        "Insert the tables with {{table:allelic_series}} and {{table:variant_spectrum}} "
        "(one line each, in Human genetics); the series ends with a tally of what the "
        "disease variants do to the target.",
        "spectrum: " + json.dumps(sp, ensure_ascii=False),
        f"allelic series: {len(rows)} variants (table shows the first 15):",
    ]
    for r in rows[:25]:
        alleles = ", ".join(
            f"{a.get('id') or '?'} {a.get('hgvsc') or ''}".strip() for a in r.get("alleles") or []
        )
        lines.append(
            f"- {r.get('change') or ''} {r.get('rsid') or ''} [{alleles or 'allele not resolved'}]"
            f" · {r.get('region') or '—'} · "
            f"{r.get('consequence') or '—'} · effect: {r.get('effect') or '—'} · "
            f"UniProt: {r.get('uniprot_note') or '—'} · {'; '.join(r.get('phenotypes') or []) or '—'}"
            f" · {'; '.join(r.get('origins') or [])} · refs {' '.join(r.get('refs') or [])}"
        )
    return "\n".join(lines)


_CUSTOM_DIGEST = {
    "pubtator_map": _map_digest,
    "ncbi_gene": _ncbi_gene_digest,
    "clinvar": _clinvar_digest,
    "allelic_series": _series_digest,
}


def write_digest(run_dir: Path, manifest: Manifest) -> None:
    lines = [
        f"# Evidence digest — {manifest.gene}",
        "",
        "Truncated view of raw/*.json (lists cut at 8, strings at 300 chars). Open the raw file "
        "for exact values and anchor paths.",
        "",
    ]
    by_section: dict[str, list[ManifestEntry]] = {}
    for e in manifest.entries:
        by_section.setdefault(e.section, []).append(e)
    abstracts = load_raw(run_dir, "pubmed_abstracts")
    metrics = (abstracts.data or {}).get("papers", {}) if abstracts else {}
    lit_map = load_raw(run_dir, "pubtator_map")
    grounding = (lit_map.data or {}).get("papers", {}) if lit_map and lit_map.status == "ok" else {}
    for sec, title in SECTIONS:
        if sec not in by_section:
            continue
        lines += [f"## {title}", ""]
        for e in by_section[sec]:
            lines.append(
                f"### `{e.key}` — {e.db_name} [{e.status}]" + (f" — {e.note}" if e.note else "")
            )
            raw = run_dir / "raw" / f"{safe_filename(e.key)}.json"
            if e.status == "ok" and raw.exists() and e.key == "pubmed_abstracts":
                d = json.loads(raw.read_text())["data"]
                lines += [
                    f"{d['with_abstract']} of {d['count']} papers above have a cached abstract. "
                    "Read before citing: `jq '.data.papers[\"<PMID>\"]' "
                    "raw/pubmed_abstracts.json`.",
                ]
            elif e.status == "ok" and raw.exists() and e.key in _PUBMED_LISTS:
                lines += [
                    "```",
                    _literature_digest(json.loads(raw.read_text())["data"], metrics, grounding),
                    "```",
                ]
            elif e.status == "ok" and raw.exists() and e.key in _CUSTOM_DIGEST:
                lines += [
                    "```",
                    _CUSTOM_DIGEST[e.key](json.loads(raw.read_text())["data"]),
                    "```",
                ]
            elif e.status == "ok" and raw.exists():
                data = _compact(json.loads(raw.read_text())["data"])
                # One line per top-level field (path = field name) keeps the digest dense.
                items = data.items() if isinstance(data, dict) else enumerate(data)
                body = "\n".join(
                    _cap(f"{k}: {json.dumps(v, ensure_ascii=False)}", 1200)
                    for k, v in items
                    if k != "text"
                )
                if len(body) > 3500:
                    body = body[:3500] + "\n… (truncated — see raw file)"
                lines += ["```", body, "```"]
            lines.append("")
    (run_dir / "digest.md").write_text("\n".join(lines))


# -------------------------------------------------------------------------- main


async def gather(args: argparse.Namespace) -> int:
    ctx = Ctx(query=args.gene, disease=args.disease or "", depth=args.depth)
    try:
        ctx.hgnc, ctx.identity_note = await resolve_identity(args.gene)
    except IdentityError as exc:
        print(f"IDENTITY: {exc}", file=sys.stderr)
        if exc.candidates:
            print("CANDIDATES: " + ", ".join(exc.candidates), file=sys.stderr)
            return 2
        return 3
    ctx.symbol = ctx.hgnc["symbol"]
    ctx.ensembl = ctx.hgnc.get("ensembl_gene_id", "")
    ctx.entrez = ctx.hgnc.get("entrez_id", "")
    ctx.uniprot = (ctx.hgnc.get("uniprot_ids") or [""])[0]
    if ctx.identity_note:
        print(f"IDENTITY NOTE: {ctx.identity_note}", file=sys.stderr)
    if ctx.disease:
        try:
            ctx.disease_id = await ot.resolve_disease(ctx.disease)
        except Exception as exc:  # noqa: BLE001
            print(f"  disease not resolved in Open Targets: {exc}", file=sys.stderr)

    run_dir = Path(args.out or f"results/profiles/{ctx.symbol}_{ctx.today}")
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    print(
        f"Run dir: {run_dir}\nGene: {ctx.symbol} {ctx.hgnc.get('hgnc_id')} {ctx.ensembl} "
        f"UniProt {ctx.uniprot or '—'}"
        + (f"\nDisease: {ctx.disease} ({ctx.disease_id or 'unresolved'})" if ctx.disease else ""),
        file=sys.stderr,
    )
    await probe_versions(ctx)

    specs = [s for s in SPECS if args.depth != "quick" or s.quick]
    only = set(args.only.split(",")) if args.only else None
    if only:
        unknown = only - SPEC_BY_KEY.keys()
        if unknown:
            print(f"Unknown keys: {sorted(unknown)}", file=sys.stderr)
            return 1
        # Later-wave specs read earlier payloads; reload every record not being refetched.
        for raw in sorted((run_dir / "raw").glob("*.json")):
            rec = RawRecord.model_validate_json(raw.read_text())
            if rec.status == "ok" and rec.key not in only:
                ctx.data[rec.key] = rec.data
        specs = [s for s in specs if s.key in only]

    sem = asyncio.Semaphore(_CONCURRENCY)
    records: list[tuple[Spec, RawRecord]] = []
    for wave in sorted({s.wave for s in specs}):
        batch = [s for s in specs if s.wave == wave]
        recs = await asyncio.gather(*(run_spec(s, ctx, run_dir, sem) for s in batch))
        records.extend(zip(batch, recs, strict=True))

    # Merge with any previous run of the same directory (``--only`` refreshes a subset).
    sources = load_sources(run_dir)
    entries: dict[str, ManifestEntry] = {}
    if (run_dir / "manifest.json").exists():
        old = Manifest.model_validate_json((run_dir / "manifest.json").read_text())
        entries = {e.key: e for e in old.entries}
    for spec, rec in records:
        entries[spec.key] = ManifestEntry(
            key=spec.key,
            section=spec.section,
            db_name=spec.db_name,
            status=rec.status,
            note=rec.note,
            url=rec.url,
        )
        sources.pop(spec.key, None)
        if rec.status in ("ok", "empty"):
            sources[spec.key] = database_source(spec, rec, ctx)
        for s in derived_sources(rec, ctx.today):
            sources.setdefault(s.key, s)
    mark_grounding(sources, ctx.data.get("pubtator_map") or {})
    save_sources(run_dir, sources)

    order = {k: i for i, k in enumerate(SPEC_BY_KEY)}
    manifest = Manifest(
        query=args.gene,
        gene=ctx.symbol,
        hgnc_id=ctx.hgnc.get("hgnc_id", ""),
        ensembl_id=ctx.ensembl,
        entrez_id=ctx.entrez,
        uniprot=ctx.uniprot,
        disease=ctx.disease,
        disease_id=ctx.disease_id,
        depth=args.depth,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        identity_note=ctx.identity_note,
        db_versions=ctx.db_versions,
        entries=sorted(entries.values(), key=lambda e: order.get(e.key, 999)),
    )
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    write_digest(run_dir, manifest)

    counts: dict[str, int] = {}
    for e in manifest.entries:
        counts[e.status] = counts.get(e.status, 0) + 1
    print(
        f"\nDone: {counts} · {len(sources)} citable sources · digest: {run_dir / 'digest.md'}",
        file=sys.stderr,
    )
    print(run_dir)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("gene", help="HGNC symbol (aliases / previous symbols are resolved)")
    p.add_argument("--disease", help="optional indication; adds disease-scoped sources")
    p.add_argument("--depth", choices=["quick", "standard", "deep"], default="standard")
    p.add_argument("--out", help="run directory (default results/profiles/<GENE>_<date>)")
    p.add_argument(
        "--only", help="comma-separated source keys to (re)fetch into an existing run dir"
    )
    sys.exit(asyncio.run(gather(p.parse_args())))


if __name__ == "__main__":
    main()
