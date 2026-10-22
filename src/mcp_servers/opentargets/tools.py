# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Open Targets Platform tools via the GraphQL API.

Endpoint: https://api.platform.opentargets.org/api/v4/graphql

Covers:
- Gene/disease resolution (resolve_gene, resolve_disease)
- Platform association & tractability scores (get_associations, get_tractability)
- Known drugs targeting a gene (get_known_drugs)
- Safety liabilities / adverse events (get_safety)
- Mouse KO phenotypes (get_mouse_phenotypes)
- OT Genetics: Locus-to-Gene scores (get_l2g_scores)
- OT Genetics: eQTL/pQTL ↔ GWAS colocalizations (get_colocalizations)
- Disease ontology: EFO descendant resolution (get_disease_descendants)
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import Any, cast

import httpx
from pydantic import BaseModel

from core.degradations import record_degradation
from core.evidence_text import trait_matches
from core.exceptions import MCPToolError
from core.http import post_with_retry

logger = logging.getLogger(__name__)

_OT_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"


_OT_BASE = "https://platform.opentargets.org"


class AssociationBundle(BaseModel):
    gene_id: str
    disease_id: str
    overall_score: float = 0.0
    genetic_score: float = 0.0
    literature_score: float = 0.0
    rna_expression_score: float = 0.0
    animal_model_score: float = 0.0
    known_drugs_score: float = 0.0
    somatic_mutations_score: float = 0.0
    source_link: str = ""
    text: str = ""


class TractabilityBundle(BaseModel):
    gene_id: str
    small_molecule: bool = False
    antibody: bool = False
    other_modalities: list[str] = []
    source_link: str = ""
    text: str = ""


async def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await post_with_retry(
            client,
            _OT_GRAPHQL,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise MCPToolError(f"Open Targets API returned HTTP {response.status_code}")
    data = response.json()
    if "errors" in data:
        raise MCPToolError(f"Open Targets GraphQL error: {data['errors']}")
    return cast("dict[str, Any]", data.get("data", {}))


# ---------------------------------------------------------------------------
# Disease ontology — EFO descendant resolution
# ---------------------------------------------------------------------------


class DiseaseOntology(BaseModel):
    """Self EFO ID plus all descendant EFO IDs, plus therapeutic-area IDs."""

    efo_ids: set[str]
    therapeutic_areas: set[str] = set()
    therapeutic_area_names: set[str] = set()
    # The disease's own name and exact synonyms, and its direct parents' names: how a
    # label or abstract may name it ("inflammatory bowel disease" for Crohn disease).
    names: set[str] = set()
    parent_names: set[str] = set()
    # Narrow synonyms name subtypes (in scope); related and broad synonyms name a wider or
    # neighbouring concept (related, never in scope).
    narrow_names: set[str] = set()
    broad_names: set[str] = set()
    # Names and exact synonyms of the descendants (subtypes), and every ancestor's name.
    descendant_names: set[str] = set()
    ancestor_names: set[str] = set()
    # Names (and exact synonyms) of the parents of the disease's cross-referenced terms in
    # other open ontologies (DOID, EFO, NCIT, MeSH), with the term id each came from:
    # the disease group papers name when MONDO files the disease elsewhere.
    xref_parent_names: set[str] = set()
    xref_parent_sources: dict[str, str] = {}
    # MeSH descriptor ids ("D000544") cross-referenced by the disease and the subtypes
    # whose names were fetched: how PubTator-grounded disease mentions are placed in scope.
    mesh_ids: set[str] = set()
    # True when the descendant names were capped or could not be fetched.
    names_partial: bool = False


_DISEASE_DESCENDANTS_QUERY = """
query Descendants($efoId: String!) {
  disease(efoId: $efoId) {
    id
    name
    synonyms { relation terms }
    parents { id name }
    descendants
    ancestors
    therapeuticAreas { id name }
    dbXRefs
  }
}
"""

_DISEASE_NAMES_QUERY = """
query Names($ids: [String!]!) {
  diseases(efoIds: $ids) { id name synonyms { relation terms } dbXRefs }
}
"""
# Descendant names are fetched for at most this many subtypes (broad roots have thousands).
_MAX_DESCENDANT_NAMES = 300
_NAMES_BATCH = 100


def _mesh_xrefs(d: dict[str, Any]) -> set[str]:
    return {
        str(x).split(":", 1)[1]
        for x in d.get("dbXRefs") or []
        if str(x).upper().startswith("MESH:")
    }


def _synonyms(d: dict[str, Any], *relations: str) -> set[str]:
    return {
        str(t)
        for syn in d.get("synonyms") or []
        if syn.get("relation") in relations
        for t in syn.get("terms") or []
        if t
    }


async def _disease_names(ids: list[str]) -> tuple[dict[str, dict[str, Any]], bool]:
    """{id: disease row with name and synonyms} for ``ids``; False when a batch failed."""
    out: dict[str, dict[str, Any]] = {}
    complete = True
    for i in range(0, len(ids), _NAMES_BATCH):
        try:
            data = await _graphql(_DISEASE_NAMES_QUERY, {"ids": ids[i : i + _NAMES_BATCH]})
        except Exception:
            complete = False
            continue
        for row in data.get("diseases") or []:
            if row.get("id"):
                out[str(row["id"])] = row
    return out, complete


# Module-level cache keyed by EFO ID; descendants are static per OT release. A lookup that
# failed in part is not cached: it is kept for _RETRY_AFTER_S only, so the run's later
# callers retry it (a transient failure at the start of a run had narrowed every scope
# of that run) without hammering a service that is down.
_disease_ontology_cache: dict[str, DiseaseOntology] = {}
_disease_ontology_failed: dict[str, tuple[float, DiseaseOntology]] = {}
_RETRY_AFTER_S = 60.0


async def get_disease_descendants(disease_id: str) -> DiseaseOntology:
    """Return self EFO ID ∪ all descendant EFO IDs plus therapeutic-area IDs.

    Used to scope GWAS/coloc evidence to the target indication (and its
    children in the EFO hierarchy) without false-matching unrelated traits.
    Falls back gracefully to {disease_id} only on network error or unknown ID.
    """
    if disease_id in _disease_ontology_cache:
        return _disease_ontology_cache[disease_id]
    failed_at, partial = _disease_ontology_failed.get(disease_id, (0.0, None))
    if partial is not None and time.monotonic() - failed_at < _RETRY_AFTER_S:
        return partial
    degraded = False
    try:
        data = await _graphql(_DISEASE_DESCENDANTS_QUERY, {"efoId": disease_id})
        d = data.get("disease") or {}
        ids: set[str] = {disease_id}
        ids.update(d.get("descendants") or [])
        areas: set[str] = {a["id"] for a in (d.get("therapeuticAreas") or [])}
        area_names: set[str] = {
            a["name"] for a in (d.get("therapeuticAreas") or []) if a.get("name")
        }
        names = {str(d["name"])} if d.get("name") else set()
        names |= _synonyms(d, "hasExactSynonym")
        descendants = [str(x) for x in d.get("descendants") or []]
        ancestors = [str(x) for x in d.get("ancestors") or []]
        wanted = descendants[:_MAX_DESCENDANT_NAMES] + ancestors
        rows, complete = await _disease_names(wanted) if wanted else ({}, True)
        descendant_names = {
            str(n)
            for x in descendants[:_MAX_DESCENDANT_NAMES]
            if (row := rows.get(x))
            for n in ({row.get("name")} | _synonyms(row, "hasExactSynonym"))
            if n
        }
        result = DiseaseOntology(
            efo_ids=ids,
            therapeutic_areas=areas,
            therapeutic_area_names=area_names,
            names=names,
            # Parents' names and their exact synonyms ("non-alcoholic fatty liver disease",
            # "NAFLD"): how a paper names the broader condition.
            parent_names={str(p["name"]) for p in d.get("parents") or [] if p.get("name")}
            | {
                n
                for p in d.get("parents") or []
                if (row := rows.get(str(p.get("id") or "")))
                for n in _synonyms(row, "hasExactSynonym")
            },
            narrow_names=_synonyms(d, "hasNarrowSynonym"),
            broad_names=_synonyms(d, "hasRelatedSynonym", "hasBroadSynonym"),
            descendant_names=descendant_names,
            ancestor_names={
                str(row["name"]) for x in ancestors if (row := rows.get(x)) and row.get("name")
            },
            mesh_ids=_mesh_xrefs(d)
            | {
                m
                for x in descendants[:_MAX_DESCENDANT_NAMES]
                if (row := rows.get(x))
                for m in _mesh_xrefs(row)
            },
            names_partial=len(descendants) > _MAX_DESCENDANT_NAMES or not complete,
        )
        if not complete:
            degraded = True
            record_degradation(
                "disease scope: Open Targets disease names",
                f"a names batch for {disease_id} failed",
                "some subtypes' and ancestors' names are missing from the disease scope",
            )
    except Exception as exc:
        degraded = True
        record_degradation(
            "disease scope: Open Targets ontology",
            f"{type(exc).__name__}: {exc}",
            "the disease scope has only the disease's own id: no subtypes, ancestors or synonyms",
        )
        result = DiseaseOntology(efo_ids={disease_id}, names_partial=True)
    else:
        result, xref_ok = await _with_xref_parents(disease_id, result)
        degraded = degraded or not xref_ok
    if degraded:
        _disease_ontology_failed[disease_id] = (time.monotonic(), result)
    else:
        _disease_ontology_cache[disease_id] = result
        _disease_ontology_failed.pop(disease_id, None)
    return result


def _fold_name(name: str) -> str:
    return " ".join(name.lower().split())


async def _with_xref_parents(
    disease_id: str, onto: DiseaseOntology
) -> tuple[DiseaseOntology, bool]:
    """``onto`` with the names of its cross-referenced terms' parents (ontology
    ``xref_parents``), less therapeutic-area names and the disease's own and subtypes'
    names, and whether the lookup succeeded. A failed lookup leaves the scope as it was,
    marks it partial and records a degradation."""
    from mcp_servers.ontology.tools import xref_parents

    try:
        parents = await xref_parents(disease_id)
    except MCPToolError as exc:
        record_degradation(
            "disease scope: cross-ontology parents",
            str(exc),
            "papers naming the disease by a parent term of another ontology (DOID, MeSH, "
            "NCIT) are not matched to it",
        )
        return onto.model_copy(update={"names_partial": True}), False
    excluded = {
        _fold_name(n) for n in onto.therapeutic_area_names | onto.names | onto.descendant_names
    }
    sources: dict[str, str] = {}
    for parent in parents:
        for name in [parent.label, *parent.exact_synonyms]:
            if name and _fold_name(name) not in excluded:
                sources.setdefault(name, parent.term_id)
    return onto.model_copy(
        update={"xref_parent_names": set(sources), "xref_parent_sources": sources}
    ), True


class DirectionEvidence(BaseModel):
    """One Open Targets genetic evidence row with its direction annotations: whether the
    variant lowers or raises the target's function (``LoF`` / ``GoF``) and whether it
    raises or lowers the trait's risk (``risk`` / ``protect``)."""

    datasource: str
    disease_id: str = ""
    disease_name: str = ""
    direction_on_target: str | None = None
    direction_on_trait: str | None = None
    consequence: str = ""


_DIRECTION_EVIDENCE_QUERY = """
query DirectionEvidence($ensemblId: String!, $efoIds: [String!]!, $datasources: [String!]) {
  target(ensemblId: $ensemblId) {
    evidences(efoIds: $efoIds, datasourceIds: $datasources, size: 3000) {
      rows {
        datasourceId
        disease { id name }
        directionOnTarget
        directionOnTrait
        variantFunctionalConsequence { label }
      }
    }
  }
}
"""
DIRECTION_DATASOURCES = (
    "eva",
    "orphanet",
    "gene2phenotype",
    "clingen",
    "genomics_england",
    "uniprot_variants",
    "uniprot_literature",
    "gene_burden",
    "gwas_credible_sets",
)
_direction_cache: dict[tuple[str, tuple[str, ...]], list[DirectionEvidence]] = {}


async def get_direction_evidence(
    ensembl_id: str, efo_ids: Iterable[str]
) -> list[DirectionEvidence]:
    """Genetic evidence rows of the target for these diseases (the run's scope: the
    disease and its descendants) that Open Targets annotates with a direction; cached per
    process; [] on any error."""
    ids = tuple(sorted({i for i in efo_ids if i}))
    if not ensembl_id or not ids:
        return []
    key = (ensembl_id, ids)
    if key in _direction_cache:
        return _direction_cache[key]
    rows: list[DirectionEvidence] = []
    try:
        data = await _graphql(
            _DIRECTION_EVIDENCE_QUERY,
            {
                "ensemblId": ensembl_id,
                "efoIds": list(ids),
                "datasources": list(DIRECTION_DATASOURCES),
            },
        )
        for r in ((data.get("target") or {}).get("evidences") or {}).get("rows") or []:
            if not (r.get("directionOnTarget") or r.get("directionOnTrait")):
                continue
            disease = r.get("disease") or {}
            rows.append(
                DirectionEvidence(
                    datasource=str(r.get("datasourceId") or ""),
                    disease_id=str(disease.get("id") or ""),
                    disease_name=str(disease.get("name") or ""),
                    direction_on_target=r.get("directionOnTarget"),
                    direction_on_trait=r.get("directionOnTrait"),
                    consequence=str(
                        (r.get("variantFunctionalConsequence") or {}).get("label") or ""
                    ),
                )
            )
    except Exception:
        return []
    _direction_cache[key] = rows
    return rows


class QtlColoc(BaseModel):
    """A strong colocalisation of a disease GWAS credible set of the target with a QTL of
    the target itself. ``beta_ratio_sign`` is Gentropy's ``betaRatioSignAverage``, the mean
    over shared variants of sign(beta_GWAS / beta_QTL): positive when the allele raising
    the target's product raises the trait."""

    # eqtl | pqtl: QTLs of the amount of the gene's product. sQTLs are not asked for: their
    # effect is on one splice junction's use, so their sign is no change of the target.
    study_type: str
    h4: float | None = None
    clpp: float | None = None
    beta_ratio_sign: float
    gwas_study_locus_id: str
    disease_id: str = ""
    # The QTL's lead variant is a missense change of the target itself. An affinity-assay
    # pQTL of such a variant can be an epitope artefact (the antibody binds the variant
    # protein less), so its sign is not read as a change of amount.
    lead_missense_in_target: bool = False


# Conventional cut-offs for a shared causal variant: coloc's posterior H4 (Giambartolomei
# et al. 2014) and eCAVIAR's colocalisation posterior probability (Hormozdiari et al. 2016).
COLOC_MIN_H4 = 0.8
COLOC_MIN_CLPP = 0.01
_COLOC_PAGE = 200
_COLOC_BATCH = 10

_GWAS_LOCI_QUERY = """
query GwasLoci($ensemblId: String!, $efoIds: [String!]!) {
  target(ensemblId: $ensemblId) {
    evidences(efoIds: $efoIds, datasourceIds: ["gwas_credible_sets"], size: 500) {
      rows { disease { id } credibleSet { studyLocusId } }
    }
  }
}
"""
_COLOC_FIELDS = (
    f"colocalisation(studyTypes: [eqtl, pqtl], page: {{index: 0, size: {_COLOC_PAGE}}}) "
    "{ count rows { h4 clpp betaRatioSignAverage "
    "otherStudyLocus { study { studyType target { id } } "
    "variant { transcriptConsequences { target { id } variantConsequences { label } } } } } }"
)


def _coloc_query(n: int) -> str:
    """One request for ``n`` credible sets, aliased cs0…cs<n-1>."""
    params = ", ".join(f"$id{i}: String!" for i in range(n))
    sets = " ".join(
        f"cs{i}: credibleSet(studyLocusId: $id{i}) {{ {_COLOC_FIELDS} }}" for i in range(n)
    )
    return f"query QtlColoc({params}) {{ {sets} }}"


def _strong(row: dict[str, Any]) -> bool:
    h4, clpp = row.get("h4"), row.get("clpp")
    return (h4 is not None and h4 >= COLOC_MIN_H4) or (clpp is not None and clpp >= COLOC_MIN_CLPP)


_qtl_cache: dict[tuple[str, tuple[str, ...]], list[QtlColoc]] = {}


async def get_qtl_colocalisations(ensembl_id: str, efo_ids: Iterable[str]) -> list[QtlColoc]:
    """Strong colocalisations of the target's GWAS credible sets for these diseases (the
    run's scope) with QTLs of the target itself, whose effect signs give the direction of
    effect for common variants. Cached per process; [] and a degradation on error."""
    ids = tuple(sorted({i for i in efo_ids if i}))
    if not ensembl_id or not ids:
        return []
    key = (ensembl_id, ids)
    if key in _qtl_cache:
        return _qtl_cache[key]
    out: list[QtlColoc] = []
    try:
        data = await _graphql(_GWAS_LOCI_QUERY, {"ensemblId": ensembl_id, "efoIds": list(ids)})
        disease_of: dict[str, str] = {}
        for r in ((data.get("target") or {}).get("evidences") or {}).get("rows") or []:
            locus = str((r.get("credibleSet") or {}).get("studyLocusId") or "")
            if locus:
                disease_of.setdefault(locus, str((r.get("disease") or {}).get("id") or ""))
        loci = list(disease_of)
        for start in range(0, len(loci), _COLOC_BATCH):
            batch = loci[start : start + _COLOC_BATCH]
            res = await _graphql(
                _coloc_query(len(batch)), {f"id{i}": locus for i, locus in enumerate(batch)}
            )
            for i, locus in enumerate(batch):
                coloc = (res.get(f"cs{i}") or {}).get("colocalisation") or {}
                rows = coloc.get("rows") or []
                if int(coloc.get("count") or 0) > len(rows):
                    logger.info(
                        "QTL colocalisation of %s: page full (%d of %d rows read)",
                        locus,
                        len(rows),
                        coloc.get("count"),
                    )
                for row in rows:
                    study = (row.get("otherStudyLocus") or {}).get("study") or {}
                    sign = row.get("betaRatioSignAverage")
                    if (
                        (study.get("target") or {}).get("id") != ensembl_id
                        or not _strong(row)
                        or not sign
                    ):
                        continue
                    lead = (row.get("otherStudyLocus") or {}).get("variant") or {}
                    out.append(
                        QtlColoc(
                            study_type=str(study.get("studyType") or ""),
                            h4=row.get("h4"),
                            clpp=row.get("clpp"),
                            beta_ratio_sign=float(sign),
                            gwas_study_locus_id=locus,
                            disease_id=disease_of[locus],
                            lead_missense_in_target=missense_in(lead, ensembl_id),
                        )
                    )
    except Exception as exc:
        record_degradation(
            "genetics: QTL colocalisation",
            f"{type(exc).__name__}: {exc}",
            "no direction of effect read from GWAS–QTL colocalisation",
        )
        return []
    _qtl_cache[key] = out
    return out


def missense_in(variant: dict[str, Any], ensembl_id: str) -> bool:
    """The variant (an Open Targets ``variant`` with ``transcriptConsequences``) is a
    missense change of the gene ``ensembl_id``."""
    return any(
        "missense_variant"
        in {str(c.get("label") or "") for c in tc.get("variantConsequences") or []}
        for tc in variant.get("transcriptConsequences") or []
        if (tc.get("target") or {}).get("id") == ensembl_id
    )


class VariantQtl(BaseModel):
    """One expression or protein QTL credible set of the target that contains the
    variant, with the variant's own effect (beta of its alternate allele)."""

    study_type: str  # eqtl | pqtl
    beta: float
    biosample: str = ""
    lead_variant: str = ""
    lead_missense_in_target: bool = False


class VariantTargetEffect(BaseModel):
    """What data say about one variant's effect on the target. Alleles are on the forward
    strand; ``amino_acid_change`` is the change in the target's protein ("R46L")."""

    variant_id: str  # "1_55039974_G_T"
    rsids: list[str] = []
    ref: str = ""
    alt: str = ""
    amino_acid_change: str = ""
    consequences: list[str] = []  # in the target's transcripts
    curated: list[str] = []  # directionOnTarget of curated rows for the target: LoF | GoF
    qtls: list[VariantQtl] = []


_VARIANT_SEARCH_QUERY = """
query VariantSearch($q: String!) {
  search(queryString: $q, entityNames: ["variant"], page: {index: 0, size: 10}) { hits { id } }
}
"""
_VARIANT_TARGET_QUERY = """
query VariantTarget($id: String!, $datasources: [String!]) {
  variant(variantId: $id) {
    id rsIds referenceAllele alternateAllele
    transcriptConsequences { aminoAcidChange target { id } variantConsequences { label } }
    evidences(datasourceIds: $datasources, size: 500) {
      rows { directionOnTarget target { id } }
    }
    credibleSets(studyTypes: [eqtl, pqtl], page: {index: 0, size: 200}) {
      rows {
        study { studyType target { id } biosample { biosampleName } }
        variant { id transcriptConsequences { target { id } variantConsequences { label } } }
        locus(variantIds: [$id]) { rows { beta } }
      }
    }
  }
}
"""
_variant_cache: dict[tuple[str, str], list[VariantTargetEffect]] = {}


def _variant_target_effect(v: dict[str, Any], ensembl_id: str) -> VariantTargetEffect:
    own = [
        tc
        for tc in v.get("transcriptConsequences") or []
        if (tc.get("target") or {}).get("id") == ensembl_id
    ]
    qtls: list[VariantQtl] = []
    for row in (v.get("credibleSets") or {}).get("rows") or []:
        study = row.get("study") or {}
        betas = [r.get("beta") for r in (row.get("locus") or {}).get("rows") or []]
        if (study.get("target") or {}).get("id") != ensembl_id or not betas or betas[0] is None:
            continue
        lead = row.get("variant") or {}
        qtls.append(
            VariantQtl(
                study_type=str(study.get("studyType") or "").lower(),
                beta=float(betas[0]),
                biosample=str((study.get("biosample") or {}).get("biosampleName") or ""),
                lead_variant=str(lead.get("id") or ""),
                lead_missense_in_target=missense_in(lead, ensembl_id),
            )
        )
    return VariantTargetEffect(
        variant_id=str(v.get("id") or ""),
        rsids=[str(r).lower() for r in v.get("rsIds") or []],
        ref=str(v.get("referenceAllele") or ""),
        alt=str(v.get("alternateAllele") or ""),
        amino_acid_change=next(
            (str(tc["aminoAcidChange"]) for tc in own if tc.get("aminoAcidChange")), ""
        ),
        consequences=sorted(
            {str(c.get("label") or "") for tc in own for c in tc.get("variantConsequences") or []}
            - {""}
        ),
        curated=[
            str(r["directionOnTarget"])
            for r in (v.get("evidences") or {}).get("rows") or []
            if (r.get("target") or {}).get("id") == ensembl_id
            and r.get("directionOnTarget") in ("LoF", "GoF")
        ],
        qtls=qtls,
    )


async def get_variant_target_effects(rsid: str, ensembl_id: str) -> list[VariantTargetEffect]:
    """Each Open Targets variant with this rs id (one per alternate allele), with what its
    data say about its effect on the target ``ensembl_id``: protein change, curated
    direction, and its own beta in the target's eQTL and pQTL credible sets. Cached per
    process; [] and a degradation on error."""
    key = (rsid.lower(), ensembl_id)
    if not rsid or not ensembl_id:
        return []
    if key in _variant_cache:
        return _variant_cache[key]
    out: list[VariantTargetEffect] = []
    try:
        hits = await _graphql(_VARIANT_SEARCH_QUERY, {"q": rsid})
        for hit in ((hits.get("search") or {}).get("hits")) or []:
            data = await _graphql(
                _VARIANT_TARGET_QUERY,
                {"id": str(hit.get("id") or ""), "datasources": list(DIRECTION_DATASOURCES)},
            )
            v = data.get("variant") or {}
            if rsid.lower() in {str(r).lower() for r in v.get("rsIds") or []}:
                out.append(_variant_target_effect(v, ensembl_id))
    except Exception as exc:
        record_degradation(
            "genetics: variant effect lookup",
            f"{type(exc).__name__}: {exc}",
            f"no curated direction or QTL read for {rsid}",
        )
        return []
    _variant_cache[key] = out
    return out


SOMATIC_DATASOURCES = ("cancer_gene_census", "intogen", "cancer_biomarkers")
_SOMATIC_EVIDENCE_QUERY = """
query SomaticEvidence($ensemblId: String!, $efoIds: [String!]!, $datasources: [String!]) {
  target(ensemblId: $ensemblId) {
    evidences(efoIds: $efoIds, datasourceIds: $datasources, size: 500) {
      rows {
        datasourceId
        disease { id name }
      }
    }
  }
}
"""
_somatic_cache: dict[tuple[str, tuple[str, ...]], list[DirectionEvidence]] = {}


async def get_somatic_evidence(ensembl_id: str, efo_ids: Iterable[str]) -> list[DirectionEvidence]:
    """Curated somatic evidence rows of the target for these diseases (Cancer Gene Census,
    IntOGen, cancer biomarkers); cached per process; [] on any error. Direction fields are
    left empty: most driver annotations do not say whether lowering activity helps."""
    ids = tuple(sorted({i for i in efo_ids if i}))
    if not ensembl_id or not ids:
        return []
    key = (ensembl_id, ids)
    if key in _somatic_cache:
        return _somatic_cache[key]
    try:
        data = await _graphql(
            _SOMATIC_EVIDENCE_QUERY,
            {
                "ensemblId": ensembl_id,
                "efoIds": list(ids),
                "datasources": list(SOMATIC_DATASOURCES),
            },
        )
    except Exception:
        return []
    rows = [
        DirectionEvidence(
            datasource=str(r.get("datasourceId") or ""),
            disease_id=str((r.get("disease") or {}).get("id") or ""),
            disease_name=str((r.get("disease") or {}).get("name") or ""),
        )
        for r in ((data.get("target") or {}).get("evidences") or {}).get("rows") or []
    ]
    _somatic_cache[key] = rows
    return rows


_SEARCH_QUERY = """
query Search($q: String!, $entities: [String!]) {
  search(queryString: $q, entityNames: $entities) {
    hits {
      id
      entity
      name
    }
  }
}
"""


async def resolve_gene(symbol: str) -> str:
    """Resolve a gene symbol (e.g. BRCA1) to its Ensembl ID via Open Targets search.

    Returns the Ensembl ID of the top-ranked target hit, or raises MCPToolError
    if nothing is found.
    """
    data = await _graphql(_SEARCH_QUERY, {"q": symbol, "entities": ["target"]})
    hits = (data.get("search") or {}).get("hits") or []
    target_hits = [h for h in hits if h.get("entity") == "target"]
    if not target_hits:
        raise MCPToolError(f"No Open Targets gene match for symbol '{symbol}'")
    return cast("str", target_hits[0]["id"])


async def resolve_disease(name: str) -> str:
    """Resolve a disease name (e.g. 'breast cancer') to its EFO/MONDO ID.

    Returns the ontology ID of the top-ranked disease hit, or raises MCPToolError
    if nothing is found.
    """
    data = await _graphql(_SEARCH_QUERY, {"q": name, "entities": ["disease"]})
    hits = (data.get("search") or {}).get("hits") or []
    disease_hits = [h for h in hits if h.get("entity") == "disease"]
    if not disease_hits:
        raise MCPToolError(f"No Open Targets disease match for name '{name}'")
    return cast("str", disease_hits[0]["id"])


_ASSOCIATIONS_QUERY = """
query AssociationScores($geneId: String!, $diseaseIds: [String!]) {
  target(ensemblId: $geneId) {
    approvedSymbol
    associatedDiseases(Bs: $diseaseIds, page: {index: 0, size: 1}) {
      rows {
        score
        datatypeScores {
          id
          score
        }
      }
    }
  }
}
"""

_TRACTABILITY_QUERY = """
query Tractability($geneId: String!) {
  target(ensemblId: $geneId) {
    tractability {
      label
      modality
      value
    }
  }
}
"""


async def get_associations(gene_id: str, disease_id: str) -> AssociationBundle:
    """Fetch association scores between a gene and disease from Open Targets."""
    data = await _graphql(_ASSOCIATIONS_QUERY, {"geneId": gene_id, "diseaseIds": [disease_id]})
    target = data.get("target") or {}
    rows = (target.get("associatedDiseases") or {}).get("rows") or []

    link = f"{_OT_BASE}/target/{gene_id}/associations?disease={disease_id}"

    if not rows:
        return AssociationBundle(
            gene_id=gene_id,
            disease_id=disease_id,
            source_link=link,
            text=f"No association data found for {gene_id} / {disease_id}.",
        )

    row = rows[0]
    overall = float(row.get("score", 0.0))
    score_map = {s["id"]: float(s["score"]) for s in row.get("datatypeScores", [])}
    genetic = score_map.get("genetic_association", 0.0)
    literature = score_map.get("literature", 0.0)
    rna = score_map.get("affected_pathway", 0.0)  # OT v4: was rna_expression
    animal = score_map.get("animal_model", 0.0)
    drugs = score_map.get("clinical", 0.0)  # OT v4: was known_drug
    somatic = score_map.get("somatic_mutation", 0.0)
    text = (
        f"Open Targets association {gene_id} / {disease_id}: overall={overall:.3f}, "
        f"genetic={genetic:.3f}, literature={literature:.3f}, affected_pathway={rna:.3f}, "
        f"animal_model={animal:.3f}, clinical={drugs:.3f}, somatic_mutations={somatic:.3f}."
    )
    return AssociationBundle(
        gene_id=gene_id,
        disease_id=disease_id,
        overall_score=overall,
        genetic_score=genetic,
        literature_score=literature,
        rna_expression_score=rna,
        animal_model_score=animal,
        known_drugs_score=drugs,
        somatic_mutations_score=somatic,
        source_link=link,
        text=text,
    )


async def get_tractability(gene_id: str) -> TractabilityBundle:
    """Fetch tractability evidence for a gene from Open Targets."""
    data = await _graphql(_TRACTABILITY_QUERY, {"geneId": gene_id})
    target = data.get("target") or {}
    tractability = target.get("tractability") or []

    small_molecule = False
    antibody = False
    other: list[str] = []

    for item in tractability:
        if not item.get("value"):
            continue
        modality = item.get("modality", "").lower()
        if modality == "sm":
            small_molecule = True
        elif modality == "ab":
            antibody = True
        else:
            label = item.get("label", modality)
            if label not in other:
                other.append(label)

    link = f"{_OT_BASE}/target/{gene_id}"
    modalities = []
    if small_molecule:
        modalities.append("small molecule")
    if antibody:
        modalities.append("antibody")
    modalities.extend(other)
    text = (
        f"Open Targets tractability for {gene_id}: "
        + (", ".join(modalities) if modalities else "no tractable modalities found")
        + "."
    )
    return TractabilityBundle(
        gene_id=gene_id,
        small_molecule=small_molecule,
        antibody=antibody,
        other_modalities=other,
        source_link=link,
        text=text,
    )


# ---------------------------------------------------------------------------
# Known drugs
# ---------------------------------------------------------------------------


class KnownDrug(BaseModel):
    drug_id: str
    drug_name: str
    drug_type: str
    # Highest stage this drug reached *for this indication* (clinical reports naming the
    # disease), not the drug's best stage anywhere.
    max_phase: float
    is_approved: bool  # approved for some indication (drug-level)
    mechanism_of_action: str
    action_type: str
    disease_name: str
    disease_id: str
    trial_status: str
    # A regulatory approval report names this indication.
    approved_in_indication: bool = False
    # Trials in this indication stopped for lack of efficacy or for safety, as
    # "<trial id> (<stage>, <status>): <why stopped> [<categories>]".
    stopped_for_cause: list[str] = []
    # Every clinical report in this indication, whatever its stop categories: the raw
    # material for classifying a programme's outcome (services/evidence/programme_outcomes.py).
    stop_reports: list[dict[str, Any]] = []


class KnownDrugsBundle(BaseModel):
    gene_id: str
    total_count: int = 0
    drugs: list[KnownDrug] = []
    text: str = ""


_KNOWN_DRUGS_QUERY = """
query KnownDrugs($geneId: String!) {
  target(ensemblId: $geneId) {
    approvedSymbol
    drugAndClinicalCandidates {
      count
      rows {
        maxClinicalStage
        drug {
          id
          name
          drugType
          maximumClinicalStage
          mechanismsOfAction {
            rows {
              mechanismOfAction
              actionType
            }
          }
        }
        diseases {
          disease { id name }
        }
        clinicalReports {
          id
          clinicalStage
          trialOverallStatus
          trialWhyStopped
          trialStopReasonCategories
          diseases { disease { id name } }
        }
      }
    }
  }
}
"""

_PHASE_MAP: dict[str, float] = {
    "PHASE_0": 0.0,
    "PHASE_1": 1.0,
    "PHASE_1_2": 1.5,
    "PHASE_2": 2.0,
    "PHASE_2_3": 2.5,
    "PHASE_3": 3.0,
    "PHASE_3_4": 3.5,
    "PHASE_4": 4.0,
    # Drug-level ``maximumClinicalStage`` reports approved drugs as "APPROVAL", not "PHASE_4".
    "APPROVAL": 4.0,
}


def _stage_to_float(stage: str | None) -> float:
    return _PHASE_MAP.get(stage or "", 0.0)


# Open Targets stop-reason categories that say something about the hypothesis (the drug
# did not work, or harmed), as opposed to enrolment, funding or business reasons.
STOP_FOR_CAUSE = frozenset({"Negative", "Safety_Sideeffects"})


class _IndicationStage(BaseModel):
    name: str
    stage: float = 0.0
    approved: bool = False
    status: str = ""
    stopped_for_cause: list[str] = []
    # trial_id, stage (float), status, categories (all of them), why (≤ 200 chars)
    reports: list[dict[str, Any]] = []


def _per_indication(row: dict[str, Any]) -> dict[str, _IndicationStage]:
    """Disease id → stage, approval and failed trials, from the row's clinical reports.

    A row is one drug; its ``diseases`` list is the union over every report, so giving
    each disease the row's ``maxClinicalStage`` made a drug's phase 4 psoriasis trials
    read as phase 4 in Crohn's disease, where its only trials were a phase 2 stopped
    for futility. Rows whose reports carry no disease fall back to the row-level stage.
    """
    out: dict[str, _IndicationStage] = {}
    reports = row.get("clinicalReports") or []
    for cr in reports:
        stage_raw = str(cr.get("clinicalStage") or "")
        stage = _stage_to_float(stage_raw)
        status = str(cr.get("trialOverallStatus") or "")
        categories = [str(c) for c in cr.get("trialStopReasonCategories") or [] if c]
        cause = sorted(set(categories) & STOP_FOR_CAUSE)
        why = " ".join(str(cr.get("trialWhyStopped") or "").split())[:200]
        report = {
            "trial_id": str(cr.get("id") or ""),
            "stage": stage,
            "status": status,
            "categories": categories,
            "why": why,
        }
        for item in cr.get("diseases") or []:
            disease = (item or {}).get("disease") or {}
            d_id = disease.get("id") or ""
            if not d_id:
                continue
            ind = out.setdefault(d_id, _IndicationStage(name=disease.get("name") or ""))
            if stage >= ind.stage:
                ind.stage = stage
                ind.status = status or ind.status
            ind.approved = ind.approved or stage_raw == "APPROVAL"
            ind.reports.append(report)
            if cause:
                phase = stage_raw.replace("_", " ").lower() or "stage unknown"
                ind.stopped_for_cause.append(
                    f"{cr.get('id') or 'trial'} ({phase}, {status.lower() or 'status unknown'}): "
                    f"{why or 'no reason given'} [{', '.join(cause)}]"
                )
    if out:
        return out
    row_stage = _stage_to_float(row.get("maxClinicalStage"))
    statuses = [cr.get("trialOverallStatus") for cr in reports if cr.get("trialOverallStatus")]
    for item in row.get("diseases") or []:
        disease = (item or {}).get("disease") or {}
        d_id = disease.get("id") or ""
        if d_id and d_id not in out:
            out[d_id] = _IndicationStage(
                name=disease.get("name") or "",
                stage=row_stage,
                status=str(statuses[0]) if statuses else "",
            )
    return out


# Salt and formulation suffixes: "AFATINIB DIMALEATE" is the same drug as "AFATINIB".
SALT_SUFFIX_RE = re.compile(
    r"\s+(?:di|mono|tri|bis|hemi)?(?:sodium|potassium|calcium|magnesium|hydrochloride|"
    r"hydrobromide|hcl|mesylate|maleate|tartrate|bitartrate|citrate|sulfate|phosphate|"
    r"acetate|besylate|tosylate|succinate|fumarate|decanoate|lactate|malate|tromethamine|"
    r"meglumine)(?:\s+(?:monohydrate|dihydrate|anhydrous))?$",
    re.IGNORECASE,
)
_SALT_SUFFIX_RE = SALT_SUFFIX_RE


def unique_drug_counts(drugs: list[KnownDrug] | list[dict[str, Any]]) -> dict[str, int]:
    """Distinct drugs (salt forms merged), approved ones, and unapproved phase-3 ones.

    Open Targets lists a drug once per indication, so row counts are drug-indication
    pairs, not drugs. Accepts KnownDrug models or their dict dumps.
    """
    best: dict[str, tuple[bool, float]] = {}
    for d in drugs:
        row = d if isinstance(d, dict) else d.model_dump()
        name = str(row.get("drug_name") or "")
        if not name:
            continue
        key = _SALT_SUFFIX_RE.sub("", " ".join(name.lower().split()))
        prev = best.get(key, (False, 0.0))
        best[key] = (
            prev[0] or bool(row.get("is_approved")),
            max(prev[1], float(row.get("max_phase") or 0)),
        )
    return {
        "total": len(best),
        "approved": sum(1 for a, _ in best.values() if a),
        "phase3": sum(1 for a, p in best.values() if not a and p >= 3),
    }


async def get_known_drugs(gene_id: str, max_results: int | None = None) -> KnownDrugsBundle:
    """Fetch known drugs that target a gene from Open Targets.

    Returns drugs with their clinical phase, approval status, mechanism of action,
    and the indication they are being developed/approved for.
    gene_id must be an Ensembl ID. All rows are kept by default: a 50-row cap silently
    dropped a target's main drugs (EGFR: osimertinib and gefitinib past row 50 of 82).
    """
    data = await _graphql(_KNOWN_DRUGS_QUERY, {"geneId": gene_id})
    target = data.get("target") or {}
    symbol = target.get("approvedSymbol", gene_id)
    kd = target.get("drugAndClinicalCandidates") or {}
    total = int(kd.get("count", 0))
    rows = kd.get("rows") or []

    drugs: list[KnownDrug] = []
    for row in rows if max_results is None else rows[:max_results]:
        drug = row.get("drug") or {}
        is_approved = _stage_to_float(drug.get("maximumClinicalStage")) >= 4.0
        moa_rows = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        mechanism = moa_rows[0].get("mechanismOfAction", "") if moa_rows else ""
        action_type = moa_rows[0].get("actionType", "") if moa_rows else ""

        for d_id, ind in _per_indication(row).items():
            drugs.append(
                KnownDrug(
                    drug_id=drug.get("id", ""),
                    drug_name=drug.get("name", ""),
                    drug_type=drug.get("drugType", ""),
                    max_phase=ind.stage,
                    is_approved=is_approved,
                    mechanism_of_action=mechanism,
                    action_type=action_type,
                    disease_name=ind.name,
                    disease_id=d_id,
                    trial_status=ind.status,
                    approved_in_indication=ind.approved,
                    stopped_for_cause=ind.stopped_for_cause,
                    stop_reports=ind.reports,
                )
            )

    if not drugs:
        text = f"No known drugs found targeting {symbol} ({gene_id}) in Open Targets."
    else:
        # Rows are drug × indication: count distinct drugs, not rows (PCSK9 has ~10
        # drugs but 72 approved rows, which became "10 drugs (72 approved)").
        counts = unique_drug_counts(drugs)
        top_names = ", ".join(dict.fromkeys(d.drug_name for d in drugs[:5]))
        text = (
            f"Open Targets known drugs for {symbol}: {counts['total']} distinct drug(s) "
            f"across {len(drugs)} drug-indication rows. "
            f"Approved: {counts['approved']}, Phase 3 (not approved): {counts['phase3']}. "
            f"Examples: {top_names or 'N/A'}."
        )
    return KnownDrugsBundle(gene_id=gene_id, total_count=total, drugs=drugs, text=text)


# ---------------------------------------------------------------------------
# Safety liabilities
# ---------------------------------------------------------------------------


class SafetyEffect(BaseModel):
    direction: str
    dosing: str


class SafetyLiability(BaseModel):
    event: str
    event_id: str
    effects: list[SafetyEffect] = []
    datasource: str
    literature: str
    url: str


class SafetyBundle(BaseModel):
    gene_id: str
    liabilities: list[SafetyLiability] = []
    text: str = ""


_SAFETY_QUERY = """
query Safety($geneId: String!) {
  target(ensemblId: $geneId) {
    approvedSymbol
    safetyLiabilities {
      event
      eventId
      effects {
        direction
        dosing
      }
      datasource
      literature
      url
    }
  }
}
"""


async def get_safety(gene_id: str) -> SafetyBundle:
    """Fetch safety liabilities for a gene from Open Targets.

    Returns adverse events and safety signals (hepatotoxicity, nephrotoxicity,
    cardiotoxicity, etc.) annotated from curated sources (FDA FAERS, toxicology
    databases, literature). gene_id must be an Ensembl ID.
    """
    data = await _graphql(_SAFETY_QUERY, {"geneId": gene_id})
    target = data.get("target") or {}
    symbol = target.get("approvedSymbol", gene_id)
    raw = target.get("safetyLiabilities") or []

    liabilities: list[SafetyLiability] = []
    for item in raw:
        effects = [
            SafetyEffect(
                direction=e.get("direction", ""),
                dosing=e.get("dosing", "") or "",
            )
            for e in (item.get("effects") or [])
        ]
        liabilities.append(
            SafetyLiability(
                event=item.get("event", ""),
                event_id=item.get("eventId", "") or "",
                effects=effects,
                datasource=item.get("datasource", ""),
                literature=item.get("literature", "") or "",
                url=item.get("url", "") or "",
            )
        )

    if not liabilities:
        text = f"No safety liabilities found for {symbol} ({gene_id}) in Open Targets."
    else:
        event_names = ", ".join(dict.fromkeys(li.event for li in liabilities[:6] if li.event))
        text = (
            f"Open Targets safety for {symbol}: {len(liabilities)} liability event(s). "
            f"Events include: {event_names or 'N/A'}."
        )
    return SafetyBundle(gene_id=gene_id, liabilities=liabilities, text=text)


# ---------------------------------------------------------------------------
# Mouse phenotypes
# ---------------------------------------------------------------------------


class MousePhenotypeClass(BaseModel):
    id: str
    label: str


class MousePhenotype(BaseModel):
    phenotype_id: str
    phenotype_label: str
    phenotype_classes: list[MousePhenotypeClass] = []
    target_in_model: str
    allelic_compositions: list[str] = []


class MousePhenotypeBundle(BaseModel):
    gene_id: str
    phenotypes: list[MousePhenotype] = []
    text: str = ""


_MOUSE_PHENOTYPES_QUERY = """
query MousePhenotypes($geneId: String!) {
  target(ensemblId: $geneId) {
    approvedSymbol
    mousePhenotypes {
      modelPhenotypeId
      modelPhenotypeLabel
      modelPhenotypeClasses {
        id
        label
      }
      biologicalModels {
        allelicComposition
        geneticBackground
      }
      targetInModel
    }
  }
}
"""


async def get_mouse_phenotypes(gene_id: str) -> MousePhenotypeBundle:
    """Fetch mouse knock-out phenotypes for a gene from Open Targets (MGI/IMPC).

    Returns phenotypic consequences of disrupting the mouse orthologue, grouped by
    phenotype term (e.g. 'abnormal heart morphology', 'lethality'). Useful for
    establishing biological plausibility and early safety signals.
    gene_id must be an Ensembl ID.
    """
    data = await _graphql(_MOUSE_PHENOTYPES_QUERY, {"geneId": gene_id})
    target = data.get("target") or {}
    symbol = target.get("approvedSymbol", gene_id)
    raw = target.get("mousePhenotypes") or []

    phenotypes: list[MousePhenotype] = []
    for item in raw:
        classes = [
            MousePhenotypeClass(id=c.get("id", ""), label=c.get("label", ""))
            for c in (item.get("modelPhenotypeClasses") or [])
        ]
        compositions = [
            m.get("allelicComposition", "")
            for m in (item.get("biologicalModels") or [])
            if m.get("allelicComposition")
        ]
        phenotypes.append(
            MousePhenotype(
                phenotype_id=item.get("modelPhenotypeId", ""),
                phenotype_label=item.get("modelPhenotypeLabel", ""),
                phenotype_classes=classes,
                target_in_model=item.get("targetInModel", "") or "",
                allelic_compositions=list(dict.fromkeys(compositions)),
            )
        )

    if not phenotypes:
        text = f"No mouse KO phenotypes found for {symbol} ({gene_id}) in Open Targets."
    else:
        top_labels = ", ".join(
            dict.fromkeys(p.phenotype_label for p in phenotypes[:5] if p.phenotype_label)
        )
        class_labels = ", ".join(
            dict.fromkeys(c.label for p in phenotypes for c in p.phenotype_classes if c.label)
        )
        text = (
            f"Open Targets mouse phenotypes for {symbol}: {len(phenotypes)} KO phenotype(s). "
            f"Top phenotypes: {top_labels or 'N/A'}. "
            f"Phenotype classes: {class_labels[:200] or 'N/A'}."
        )
    return MousePhenotypeBundle(gene_id=gene_id, phenotypes=phenotypes, text=text)


# ---------------------------------------------------------------------------
# OT Genetics: Locus-to-Gene (L2G) scores
# ---------------------------------------------------------------------------


class L2GHit(BaseModel):
    study_locus_id: str
    trait: str
    disease_name: str
    chromosome: str
    position: int
    region: str
    p_value_mantissa: float
    p_value_exponent: int
    beta: float
    l2g_score: float
    top_l2g_gene: str
    top_l2g_score: float
    pubmed_id: str
    source_link: str


class L2GBundle(BaseModel):
    gene_id: str
    disease_id: str
    hits: list[L2GHit] = []
    text: str = ""


_L2G_QUERY = """
query L2G($geneId: String!, $diseaseId: String!, $size: Int!) {
  target(ensemblId: $geneId) {
    approvedSymbol
    evidences(efoIds: [$diseaseId], datasourceIds: ["gwas_credible_sets"], size: $size) {
      count
      rows {
        resourceScore
        disease { name }
        credibleSet {
          studyLocusId
          chromosome
          position
          region
          pValueMantissa
          pValueExponent
          beta
          study {
            traitFromSource
            pubmedId
          }
          l2GPredictions {
            rows {
              score
              target { approvedSymbol }
            }
          }
        }
      }
    }
  }
}
"""


async def get_l2g_scores(gene_id: str, disease_id: str, max_results: int = 25) -> L2GBundle:
    """Fetch GWAS Locus-to-Gene (L2G) evidence for a gene-disease pair (OT Genetics).

    Queries Open Targets 'gwas_credible_sets' evidence for the given gene and
    disease, returning GWAS loci where this gene has been prioritized as the
    likely causal gene. gene_id must be an Ensembl ID; disease_id must be an
    EFO/MONDO ontology ID.
    """
    data = await _graphql(
        _L2G_QUERY, {"geneId": gene_id, "diseaseId": disease_id, "size": max_results}
    )
    target = data.get("target") or {}
    symbol = target.get("approvedSymbol", gene_id)
    rows = (target.get("evidences") or {}).get("rows") or []

    hits: list[L2GHit] = []
    for row in rows:
        cs = row.get("credibleSet") or {}
        if not cs:
            continue
        l2g_rows = (cs.get("l2GPredictions") or {}).get("rows") or []
        top = l2g_rows[0] if l2g_rows else {}
        study = cs.get("study") or {}
        hits.append(
            L2GHit(
                study_locus_id=cs.get("studyLocusId") or "",
                trait=study.get("traitFromSource") or "",
                disease_name=(row.get("disease") or {}).get("name") or "",
                chromosome=cs.get("chromosome") or "",
                position=int(cs.get("position") or 0),
                region=cs.get("region") or "",
                p_value_mantissa=float(cs.get("pValueMantissa") or 0.0),
                p_value_exponent=int(cs.get("pValueExponent") or 0),
                beta=float(cs.get("beta") or 0.0),
                l2g_score=float(row.get("resourceScore") or 0.0),
                top_l2g_gene=(top.get("target") or {}).get("approvedSymbol") or "",
                top_l2g_score=float(top.get("score") or 0.0),
                pubmed_id=study.get("pubmedId") or "",
                source_link=f"{_OT_BASE}/target/{gene_id}/associations",
            )
        )

    if not hits:
        text = (
            f"No GWAS L2G evidence found for {symbol} ({gene_id}) linked to disease {disease_id}."
        )
    else:
        top_traits = ", ".join(dict.fromkeys(h.trait for h in hits[:5] if h.trait))
        text = (
            f"Open Targets Genetics L2G: {symbol} is prioritized as a causal gene "
            f"at {len(hits)} GWAS locus/loci for disease {disease_id}. "
            f"Top traits: {top_traits or 'N/A'}."
        )
    return L2GBundle(gene_id=gene_id, disease_id=disease_id, hits=hits, text=text)


# ---------------------------------------------------------------------------
# OT Genetics: eQTL/pQTL ↔ GWAS colocalizations
# ---------------------------------------------------------------------------


class ColocHit(BaseModel):
    study_locus_id: str
    qtl_study_id: str
    qtl_study_type: str
    gwas_study_id: str
    gwas_trait: str
    gwas_efo_ids: list[str] = []
    h4: float
    clpp: float
    coloc_method: str
    source_link: str


class ColocBundle(BaseModel):
    gene_id: str
    hits: list[ColocHit] = []
    text: str = ""
    dropped_off_target: int = 0
    all_traits: list[str] = []
    kept_traits: list[str] = []


_COLOC_QUERY = """
query Coloc($geneId: String!, $pageSize: Int!) {
  target(ensemblId: $geneId) {
    approvedSymbol
    credibleSets(page: {index: 0, size: $pageSize}) {
      rows {
        studyLocusId
        studyId
        studyType
        colocalisation {
          rows {
            h4
            clpp
            colocalisationMethod
            rightStudyType
            otherStudyLocus {
              studyId
              study {
                traitFromSource
                diseases { id }
              }
            }
          }
        }
      }
    }
  }
}
"""

_QTL_TYPES = {"eqtl", "pqtl", "sqtl", "tuqtl"}


async def get_colocalizations(
    gene_id: str,
    h4_threshold: float = 0.5,
    max_results: int = 25,
    *,
    efo_ids: set[str] | None = None,
    trait_terms: list[str] | None = None,
) -> ColocBundle:
    """Fetch eQTL/pQTL ↔ GWAS colocalisations for a gene (OT Genetics).

    Returns credible sets where a molecular QTL for this gene colocalizes with
    a GWAS signal (posterior probability H4 >= h4_threshold), indicating that
    variation affecting the gene's expression or protein level is shared with
    disease-associated variants. gene_id must be an Ensembl ID.

    When efo_ids or trait_terms are provided, only hits whose GWAS-side disease
    EFO IDs (or trait text) match are kept. Others are counted in dropped_off_target.
    Backward compatible: pass neither to get all colocalizations as before.
    """
    data = await _graphql(_COLOC_QUERY, {"geneId": gene_id, "pageSize": max(max_results * 4, 100)})
    target = data.get("target") or {}
    symbol = target.get("approvedSymbol", gene_id)
    rows = (target.get("credibleSets") or {}).get("rows") or []

    all_hits: list[ColocHit] = []
    for row in rows:
        if row.get("studyType") not in _QTL_TYPES:
            continue
        for coloc in (row.get("colocalisation") or {}).get("rows") or []:
            if coloc.get("rightStudyType") != "gwas":
                continue
            h4 = float(coloc.get("h4", 0.0))
            if h4 < h4_threshold:
                continue
            other = coloc.get("otherStudyLocus") or {}
            other_study = other.get("study") or {}
            gwas_disease_ids = [d["id"] for d in (other_study.get("diseases") or []) if d.get("id")]
            all_hits.append(
                ColocHit(
                    study_locus_id=row.get("studyLocusId", ""),
                    qtl_study_id=row.get("studyId", ""),
                    qtl_study_type=row.get("studyType", ""),
                    gwas_study_id=other.get("studyId", ""),
                    gwas_trait=other_study.get("traitFromSource", ""),
                    gwas_efo_ids=gwas_disease_ids,
                    h4=h4,
                    clpp=float(coloc.get("clpp", 0.0)),
                    coloc_method=coloc.get("colocalisationMethod", ""),
                    source_link=f"{_OT_BASE}/target/{gene_id}",
                )
            )

    # Collect all distinct traits before disease-scope filtering.
    all_traits = list(dict.fromkeys(h.gwas_trait for h in all_hits if h.gwas_trait))

    # Apply disease-scope filter when requested.
    dropped_off_target = 0
    hits: list[ColocHit]
    if efo_ids is not None or trait_terms:
        terms = list(trait_terms or [])
        kept: list[ColocHit] = []
        for h in all_hits:
            hit_efos = set(h.gwas_efo_ids)
            if (efo_ids and hit_efos & efo_ids) or (terms and trait_matches(h.gwas_trait, terms)):
                kept.append(h)
            else:
                dropped_off_target += 1
        hits = kept[:max_results]
    else:
        hits = all_hits[:max_results]

    kept_traits = list(dict.fromkeys(h.gwas_trait for h in hits if h.gwas_trait))

    if not hits:
        if dropped_off_target:
            off_sample = ", ".join(all_traits[:5])
            text = (
                f"Open Targets Genetics colocalisation: {symbol} has colocalizations "
                f"with {len(all_traits)} distinct GWAS trait(s) "
                f"(e.g. {off_sample or 'various'}); 0 matched the target indication. "
                f"All {dropped_off_target} coloc hit(s) excluded as off-indication."
            )
        else:
            text = (
                f"No QTL ↔ GWAS colocalisations (H4 ≥ {h4_threshold}) found for "
                f"{symbol} ({gene_id})."
            )
    else:
        top_traits = ", ".join(dict.fromkeys(h.gwas_trait for h in hits[:5] if h.gwas_trait))
        text = (
            f"Open Targets Genetics colocalisation: {symbol} has {len(hits)} "
            f"QTL ↔ GWAS colocalisation(s) (H4 ≥ {h4_threshold}) matched the target indication. "
            f"Colocalising GWAS traits: {top_traits or 'N/A'}."
        )
        if dropped_off_target:
            text += f" {dropped_off_target} coloc hit(s) excluded as off-indication."
    return ColocBundle(
        gene_id=gene_id,
        hits=hits,
        text=text,
        dropped_off_target=dropped_off_target,
        all_traits=all_traits,
        kept_traits=kept_traits,
    )
