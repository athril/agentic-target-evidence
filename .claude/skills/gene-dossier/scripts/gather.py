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
    uv run python .claude/skills/gene-dossier/scripts/gather.py TRPC6
    uv run python .claude/skills/gene-dossier/scripts/gather.py TRPC6 --disease "focal segmental glomerulosclerosis"
    uv run python .claude/skills/gene-dossier/scripts/gather.py TRPC6 --out <run_dir> --only gnomad_constraint,hpa

Exit codes: 0 ok · 2 ambiguous symbol (candidates printed) · 3 symbol not found.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dossier_schema import (  # noqa: E402
    SECTIONS,
    Manifest,
    ManifestEntry,
    RawRecord,
    Source,
    load_sources,
    safe_filename,
    save_sources,
)

load_dotenv()

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
_UA = {"User-Agent": "agentic-target-validation/gene-dossier"}


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


async def fetch_uniprot_extra(ctx: Ctx) -> dict[str, Any]:
    fields = (
        "go_p,go_f,go_c,xref_pdb,cc_disease,cc_tissue_specificity,ft_domain,length,protein_families"
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
    domains = [
        {
            "description": f.get("description", ""),
            "start": f["location"]["start"]["value"],
            "end": f["location"]["end"]["value"],
        }
        for f in d.get("features", [])
        if f.get("type") == "Domain"
    ]
    return {
        "accession": ctx.uniprot,
        "length": (d.get("sequence") or {}).get("length"),
        "families": families,
        "domains": domains,
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
    return {k: m.get(k) for k in keep}


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


async def _pubmed(term: str, retmax: int, sort: str) -> dict[str, Any]:
    # Reuse the connector's throttled client: one shared 3 req/s window (10 with NCBI_API_KEY)
    # plus 429 backoff, so parallel literature fetches don't trip NCBI's rate limit.
    es = await pubmed._rate_limited_get(
        f"{_EUTILS}/esearch.fcgi", {"db": "pubmed", "term": term, "retmax": retmax, "sort": sort}
    )
    ids = es["esearchresult"]["idlist"]
    out: dict[str, Any] = {
        "query": term,
        "sort": sort,
        "total": int(es["esearchresult"]["count"]),
        "records": [],
    }
    if not ids:
        return out
    summ = await pubmed._rate_limited_get(
        f"{_EUTILS}/esummary.fcgi", {"db": "pubmed", "id": ",".join(ids)}
    )
    for pmid in ids:
        s = summ["result"].get(pmid) or {}
        doi = next((a["value"] for a in s.get("articleids", []) if a["idtype"] == "doi"), "")
        year = (s.get("pubdate") or "")[:4]
        out["records"].append(
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


def _pm_n(ctx: Ctx, n: int) -> int:
    return n * 2 if ctx.depth == "deep" else n


async def fetch_pubmed_counts(ctx: Ctx) -> dict[str, Any]:
    y = date.today().year
    base = tiab(ctx)
    tot, recent = await asyncio.gather(
        _pubmed(base, 0, "relevance"),
        _pubmed(f'{base} AND ("{y - 4}"[dp] : "3000"[dp])', 0, "relevance"),
    )
    return {"query": base, "total": tot["total"], f"since_{y - 4}": recent["total"]}


async def fetch_pubmed_top(ctx: Ctx) -> dict[str, Any]:
    return await _pubmed(tiab(ctx), _pm_n(ctx, 20), "relevance")


async def fetch_pubmed_reviews(ctx: Ctx) -> dict[str, Any]:
    y = date.today().year
    return await _pubmed(
        f'{tiab(ctx)} AND review[pt] AND ("{y - 5}"[dp] : "3000"[dp])',
        _pm_n(ctx, 15),
        "pub_date",
    )


async def fetch_pubmed_recent(ctx: Ctx) -> dict[str, Any]:
    y = date.today().year
    return await _pubmed(
        f'{tiab(ctx)} AND ("{y - 2}"[dp] : "3000"[dp])', _pm_n(ctx, 20), "pub_date"
    )


async def fetch_pubmed_disease(ctx: Ctx) -> dict[str, Any]:
    return await _pubmed(f'{tiab(ctx)} AND "{ctx.disease}"[tiab]', _pm_n(ctx, 25), "relevance")


async def fetch_ct_gene(ctx: Ctx) -> dict[str, Any]:
    fields = (
        "NCTId,BriefTitle,OverallStatus,Phase,LeadSponsorName,Condition,InterventionName,StartDate"
    )
    d = await _get_json(
        _CTGOV,
        **{
            "query.term": " OR ".join(search_symbols(ctx)),
            "countTotal": "true",
            "pageSize": 50,
            "fields": fields,
            "sort": "StartDate:desc",
        },
    )
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


def _const(url: str) -> Callable[[Ctx, Any], str]:
    return lambda c, _d: url.format(
        s=c.symbol,
        e=c.ensembl,
        u=c.uniprot,
        h=c.hgnc.get("hgnc_id", ""),
        d=quote(c.disease),
        di=c.disease_id,
    )


def _link_or(url: str) -> Callable[[Ctx, Any], str]:
    fallback = _const(url)
    return lambda c, d: (
        d.get("source_link") if isinstance(d, dict) and d.get("source_link") else fallback(c, d)
    )


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
        _const("https://reactome.org/content/query?q={u}&species=Homo+sapiens"),
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
        _link_or("https://score.depmap.sanger.ac.uk/"),
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
        _const("https://www.omim.org/search?search={s}"),
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
        _const("https://www.orpha.net/en/disease/search?search={s}"),
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
        _const("https://hpo.jax.org/browse/search?q={s}&navFilter=gene"),
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
        lambda c, _d: f"https://www.ebi.ac.uk/chembl/explore/target/{_chembl_id(c)}",
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
        _const("https://idrblab.net/ttd/"),
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
        _const("https://clinicaltrials.gov/search?term={s}"),
    ),
    Spec(
        "ct_gene_disease",
        "indication",
        "ClinicalTrials.gov",
        "ClinicalTrials.gov studies — {symbol} AND {disease}",
        "clinicaltrials.search_trials",
        lambda c: ctgov.search_trials(c.symbol, c.disease),
        _const("https://clinicaltrials.gov/search?term={s}&cond={d}"),
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
        lambda c: impc.get_impc_phenotypes(c.symbol),
        _link_or("https://www.mousephenotype.org/data/search?term={s}&type=gene"),
    ),
    Spec(
        "faers",
        "safety",
        "openFDA FAERS",
        "FDA Adverse Event Reporting System — approved {symbol} drugs",
        "openfda.search_adverse_events",
        fetch_faers,
        _const("https://open.fda.gov/apis/drug/event/"),
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
        # Abstracts are OCR'd from per-patent PDFs (minutes for broad queries); the dossier
        # cites titles, assignees and dates only.
        fetch_uspto,
        _const("https://data.uspto.gov/patent-file-wrapper/search"),
        skip=lambda _c: None if os.environ.get("USPTO_API_KEY") else "USPTO_API_KEY not set",
    ),
    Spec(
        "orphanet_prevalence",
        "commercial",
        "Orphanet",
        "Orphanet prevalence — {symbol}-associated disorders",
        "orphanet.get_orphanet_prevalence",
        fetch_orphanet_prevalence,
        _const("https://www.orpha.net/en/disease/search?search={s}"),
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
        _const("https://vizhub.healthdata.org/gbd-results/"),
        skip=_need_disease,
    ),
    Spec(
        "openfda_indication",
        "commercial",
        "openFDA",
        "FDA-labelled drugs for {disease}",
        "openfda.count_indication_drugs",
        lambda c: openfda.count_indication_drugs(c.disease),
        _const("https://open.fda.gov/apis/drug/label/"),
        skip=_need_disease,
    ),
    Spec(
        "ct_condition",
        "commercial",
        "ClinicalTrials.gov",
        "ClinicalTrials.gov trial landscape — {disease}",
        "clinicaltrials.count_condition_trials",
        lambda c: ctgov.count_condition_trials(c.disease),
        _const("https://clinicaltrials.gov/search?cond={d}"),
        skip=_need_disease,
    ),
    # regulatory ---------------------------------------------------------------
    Spec(
        "openfda_labels",
        "regulatory",
        "openFDA",
        "FDA drug labels mentioning {symbol}",
        "openfda.search_drug_labels",
        lambda c: openfda.search_drug_labels(c.symbol, c.disease or c.symbol),
        _const("https://open.fda.gov/apis/drug/label/"),
    ),
    # literature ---------------------------------------------------------------
    Spec(
        "pubmed_counts",
        "literature",
        "PubMed",
        "PubMed publication counts — {symbol}",
        "NCBI E-utilities esearch",
        fetch_pubmed_counts,
        _const("https://pubmed.ncbi.nlm.nih.gov/?term=%22{s}%22%5Btiab%5D"),
        quick=True,
    ),
    Spec(
        "pubmed_top",
        "literature",
        "PubMed",
        "PubMed most relevant — {symbol}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_top,
        _const("https://pubmed.ncbi.nlm.nih.gov/?term=%22{s}%22%5Btiab%5D&sort=relevance"),
    ),
    Spec(
        "pubmed_reviews",
        "literature",
        "PubMed",
        "PubMed recent reviews — {symbol}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_reviews,
        _const(
            "https://pubmed.ncbi.nlm.nih.gov/?term=%22{s}%22%5Btiab%5D+AND+review%5Bpt%5D&sort=date"
        ),
    ),
    Spec(
        "pubmed_recent",
        "literature",
        "PubMed",
        "PubMed last 24 months — {symbol}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_recent,
        _const("https://pubmed.ncbi.nlm.nih.gov/?term=%22{s}%22%5Btiab%5D&sort=date"),
    ),
    Spec(
        "pubmed_disease",
        "indication",
        "PubMed",
        "PubMed — {symbol} AND {disease}",
        "NCBI E-utilities esearch+esummary",
        fetch_pubmed_disease,
        _const("https://pubmed.ncbi.nlm.nih.gov/?term=%22{s}%22%5Btiab%5D+AND+%22{d}%22%5Btiab%5D"),
        skip=_need_disease,
    ),
]
SPEC_BY_KEY = {s.key: s for s in SPECS}


# ------------------------------------------------------------------------ runner


def _title(spec: Spec, ctx: Ctx) -> str:
    return spec.title.format(
        symbol=ctx.symbol,
        ensembl=ctx.ensembl,
        uniprot=ctx.uniprot,
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


def derived_sources(rec: RawRecord, today: str) -> list[Source]:
    """Literature / trial / patent records inside a retrieval become citable sources too."""
    out: list[Source] = []
    d = rec.data
    if rec.status != "ok" or d is None:
        return out
    if rec.key.startswith("pubmed_") and isinstance(d, dict):
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
    for sec, title in SECTIONS:
        if sec not in by_section:
            continue
        lines += [f"## {title}", ""]
        for e in by_section[sec]:
            lines.append(
                f"### `{e.key}` — {e.db_name} [{e.status}]" + (f" — {e.note}" if e.note else "")
            )
            raw = run_dir / "raw" / f"{safe_filename(e.key)}.json"
            if e.status == "ok" and raw.exists():
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

    run_dir = Path(args.out or f"results/dossiers/{ctx.symbol}_{ctx.today}")
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
        # Dependent (wave-2) specs need their upstream payloads; reload them from raw/.
        for key in ("uniprot_profile", "ot_known_drugs", "orphanet_associations"):
            raw = run_dir / "raw" / f"{key}.json"
            if raw.exists():
                rec = RawRecord.model_validate_json(raw.read_text())
                if rec.status == "ok":
                    ctx.data[key] = rec.data
        specs = [s for s in specs if s.key in only]

    sem = asyncio.Semaphore(_CONCURRENCY)
    records: list[tuple[Spec, RawRecord]] = []
    for wave in (1, 2):
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
        )
        sources.pop(spec.key, None)
        if rec.status in ("ok", "empty"):
            sources[spec.key] = database_source(spec, rec, ctx)
        for s in derived_sources(rec, ctx.today):
            sources.setdefault(s.key, s)
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
    p.add_argument("--out", help="run directory (default results/dossiers/<GENE>_<date>)")
    p.add_argument(
        "--only", help="comma-separated source keys to (re)fetch into an existing run dir"
    )
    sys.exit(asyncio.run(gather(p.parse_args())))


if __name__ == "__main__":
    main()
