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

from typing import Any, cast

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import post_with_retry

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


_DISEASE_DESCENDANTS_QUERY = """
query Descendants($efoId: String!) {
  disease(efoId: $efoId) {
    id
    descendants
    therapeuticAreas { id }
  }
}
"""

# Module-level cache keyed by EFO ID; descendants are static per OT release.
_disease_ontology_cache: dict[str, DiseaseOntology] = {}


async def get_disease_descendants(disease_id: str) -> DiseaseOntology:
    """Return self EFO ID ∪ all descendant EFO IDs plus therapeutic-area IDs.

    Used to scope GWAS/coloc evidence to the target indication (and its
    children in the EFO hierarchy) without false-matching unrelated traits.
    Falls back gracefully to {disease_id} only on network error or unknown ID.
    """
    if disease_id in _disease_ontology_cache:
        return _disease_ontology_cache[disease_id]
    try:
        data = await _graphql(_DISEASE_DESCENDANTS_QUERY, {"efoId": disease_id})
        d = data.get("disease") or {}
        ids: set[str] = {disease_id}
        ids.update(d.get("descendants") or [])
        areas: set[str] = {a["id"] for a in (d.get("therapeuticAreas") or [])}
        result = DiseaseOntology(efo_ids=ids, therapeutic_areas=areas)
    except Exception:
        result = DiseaseOntology(efo_ids={disease_id})
    _disease_ontology_cache[disease_id] = result
    return result


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
    max_phase: float
    is_approved: bool
    mechanism_of_action: str
    action_type: str
    disease_name: str
    disease_id: str
    trial_status: str


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
        clinicalReports { trialOverallStatus }
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
}


def _stage_to_float(stage: str | None) -> float:
    return _PHASE_MAP.get(stage or "", 0.0)


async def get_known_drugs(gene_id: str, max_results: int = 50) -> KnownDrugsBundle:
    """Fetch known drugs that target a gene from Open Targets.

    Returns drugs with their clinical phase, approval status, mechanism of action,
    and the indication they are being developed/approved for.
    gene_id must be an Ensembl ID.
    """
    data = await _graphql(_KNOWN_DRUGS_QUERY, {"geneId": gene_id})
    target = data.get("target") or {}
    symbol = target.get("approvedSymbol", gene_id)
    kd = target.get("drugAndClinicalCandidates") or {}
    total = int(kd.get("count", 0))
    rows = kd.get("rows") or []

    drugs: list[KnownDrug] = []
    for row in rows[:max_results]:
        drug = row.get("drug") or {}
        max_phase = _stage_to_float(row.get("maxClinicalStage"))
        is_approved = _stage_to_float(drug.get("maximumClinicalStage")) >= 4.0
        moa_rows = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        mechanism = moa_rows[0].get("mechanismOfAction", "") if moa_rows else ""
        action_type = moa_rows[0].get("actionType", "") if moa_rows else ""
        statuses = [
            cr.get("trialOverallStatus", "") or ""
            for cr in (row.get("clinicalReports") or [])
            if cr.get("trialOverallStatus")
        ]
        trial_status = statuses[0] if statuses else ""

        seen_disease_ids: set[str] = set()
        for d_item in row.get("diseases") or []:
            disease = d_item.get("disease") or {}
            d_id = disease.get("id", "")
            if not d_id or d_id in seen_disease_ids:
                continue
            seen_disease_ids.add(d_id)
            drugs.append(
                KnownDrug(
                    drug_id=drug.get("id", ""),
                    drug_name=drug.get("name", ""),
                    drug_type=drug.get("drugType", ""),
                    max_phase=max_phase,
                    is_approved=is_approved,
                    mechanism_of_action=mechanism,
                    action_type=action_type,
                    disease_name=disease.get("name", ""),
                    disease_id=d_id,
                    trial_status=trial_status,
                )
            )

    if not drugs:
        text = f"No known drugs found targeting {symbol} ({gene_id}) in Open Targets."
    else:
        approved = [d for d in drugs if d.is_approved]
        phase3 = [d for d in drugs if not d.is_approved and d.max_phase >= 3]
        top_names = ", ".join(dict.fromkeys(d.drug_name for d in drugs[:5]))
        text = (
            f"Open Targets known drugs for {symbol}: {total} drug(s). "
            f"Approved: {len(approved)}, Phase 3: {len(phase3)}. "
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
        lc_terms = [t.lower() for t in (trait_terms or [])]
        kept: list[ColocHit] = []
        for h in all_hits:
            hit_efos = set(h.gwas_efo_ids)
            if (
                efo_ids
                and hit_efos & efo_ids
                or lc_terms
                and any(t in h.gwas_trait.lower() for t in lc_terms)
            ):
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
