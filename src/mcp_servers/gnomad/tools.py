# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""gnomAD constraint, ClinVar, and pLoF-variant tools via the public GraphQL API."""

from __future__ import annotations

from collections import Counter

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import post_with_retry

_GNOMAD_GRAPHQL = "https://gnomad.broadinstitute.org/api"

_CONSTRAINT_QUERY = """
query Constraint($symbol: String!) {
  gene(gene_symbol: $symbol, reference_genome: GRCh38) {
    gene_id
    gnomad_constraint {
      pLI
      oe_lof oe_lof_lower oe_lof_upper
      oe_mis oe_mis_lower oe_mis_upper
      obs_lof exp_lof
      obs_mis exp_mis
      obs_syn exp_syn
      syn_z mis_z
    }
  }
}
"""

_CONSTRAINT_BY_ID_QUERY = """
query ConstraintById($geneId: String!) {
  gene(gene_id: $geneId, reference_genome: GRCh38) {
    gene_id
    gnomad_constraint {
      pLI
      oe_lof oe_lof_lower oe_lof_upper
      oe_mis oe_mis_lower oe_mis_upper
      obs_lof exp_lof
      obs_mis exp_mis
      obs_syn exp_syn
      syn_z mis_z
    }
  }
}
"""

_CLINVAR_QUERY = """
query ClinVar($geneId: String!) {
  gene(gene_id: $geneId, reference_genome: GRCh38) {
    clinvar_variants {
      variant_id
      clinical_significance
      gold_stars
      hgvsc
      hgvsp
      major_consequence
      in_gnomad
    }
  }
}
"""

_LOF_VARIANTS_QUERY = """
query LoFVariants($geneId: String!) {
  gene(gene_id: $geneId, reference_genome: GRCh38) {
    variants(dataset: gnomad_r4) {
      variant_id
      consequence
      hgvsc
      hgvsp
      lof
      lof_filter
      lof_flags
      genome {
        af ac an homozygote_count
        populations { id ac an }
      }
    }
  }
}
"""

# Variants with AF below this are not reported as natural knockouts.
_MIN_LOF_AF = 1e-6
_MAX_LOF_VARIANTS = 25

# Population AF ratio (max/min, among populations with an AN floor) above which
# an LoF variant's frequency is reported as ancestry-skewed rather than uniform.
_ANCESTRY_SKEW_RATIO = 3.0
_MIN_POPULATION_AN = 2000


class ConstraintBundle(BaseModel):
    gene_symbol: str
    ensembl_id: str = ""
    # LoF constraint
    loeuf: float | None = None  # oe_lof_upper — lower = more LoF-intolerant
    oe_lof: float | None = None  # raw O/E ratio
    oe_lof_lower: float | None = None
    pli: float | None = None
    obs_lof: int | None = None
    exp_lof: float | None = None
    # Missense constraint
    oe_mis: float | None = None  # missense O/E ratio
    oe_mis_lower: float | None = None
    moeuf: float | None = None  # oe_mis_upper — missense OEUF
    obs_mis: int | None = None
    exp_mis: float | None = None
    mis_z: float | None = None
    # Synonymous (quality signal)
    obs_syn: int | None = None
    exp_syn: float | None = None
    syn_z: float | None = None
    source_link: str = ""
    text: str = ""


class ClinVarVariant(BaseModel):
    variant_id: str
    clinical_significance: str | None = None
    gold_stars: int | None = None
    hgvsc: str | None = None
    hgvsp: str | None = None
    major_consequence: str | None = None
    in_gnomad: bool | None = None


class ClinVarBundle(BaseModel):
    gene_symbol: str
    ensembl_id: str
    pathogenic: list[ClinVarVariant] = []
    likely_pathogenic: list[ClinVarVariant] = []
    benign: list[ClinVarVariant] = []
    total_clinvar: int = 0
    text: str = ""


class LofVariant(BaseModel):
    variant_id: str
    consequence: str | None = None
    hgvsc: str | None = None
    hgvsp: str | None = None
    lof: str | None = None  # "HC" or "LC"
    lof_filter: str | None = None
    lof_flags: str | None = None
    af: float | None = None
    ac: int | None = None
    an: int | None = None
    homozygote_count: int | None = None
    population_af: dict[str, float] = {}  # gnomAD population id -> allele frequency


class LofVariantBundle(BaseModel):
    gene_symbol: str
    ensembl_id: str
    hc_lof_count: int = 0  # total HC pLoF variants in gnomAD
    reported_variants: list[LofVariant] = []
    max_af: float | None = None  # AF of most common HC pLoF variant
    any_homozygous: bool = False
    ancestry_skewed: bool = (
        False  # top variant's AF varies >_ANCESTRY_SKEW_RATIO across populations
    )
    text: str = ""


def _population_skew_note(population_af: dict[str, float]) -> str | None:
    """Describe ancestry skew in a variant's population AF breakdown, or None if uniform/insufficient data."""
    if len(population_af) < 2:
        return None
    highest = max(population_af, key=lambda p: population_af[p])
    lowest = min(population_af, key=lambda p: population_af[p])
    hi, lo = population_af[highest], population_af[lowest]
    if lo <= 0 or hi / lo < _ANCESTRY_SKEW_RATIO:
        return None
    return (
        f"allele frequency is ancestry-skewed: {hi:.2e} in {highest} vs {lo:.2e} in {lowest} "
        f"({hi / lo:.1f}x) — natural-knockout evidence is not uniform across populations."
    )


async def _graphql(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
    resp = await post_with_retry(
        client,
        _GNOMAD_GRAPHQL,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code != 200:
        raise MCPToolError(f"gnomAD API returned HTTP {resp.status_code}")
    data = resp.json()
    if data.get("errors"):
        raise MCPToolError(f"gnomAD GraphQL error: {data['errors']}")
    return data.get("data") or {}


async def get_constraint(gene_symbol: str, ensembl_id: str = "") -> ConstraintBundle:
    """Fetch gnomAD gene-level LoF/missense/synonymous constraint metrics.

    Falls back to querying by Ensembl ID when the symbol is an alias not
    recognised by gnomAD (e.g. CRF1 instead of the approved HGNC symbol CRHR1).
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            data = await _graphql(client, _CONSTRAINT_QUERY, {"symbol": gene_symbol})
        except MCPToolError as exc:
            if "Gene not found" not in str(exc) or not ensembl_id:
                raise
            data = await _graphql(client, _CONSTRAINT_BY_ID_QUERY, {"geneId": ensembl_id})

    gene = data.get("gene") or {}
    c = gene.get("gnomad_constraint") or {}
    gene_id = gene.get("gene_id", gene_symbol)
    loeuf = c.get("oe_lof_upper")
    pli = c.get("pLI")
    moeuf = c.get("oe_mis_upper")

    from services.evidence.constraint_interpret import interpret_constraint as _ic

    _reading = _ic(
        gene_symbol=gene_symbol,
        loeuf=loeuf,
        pli=pli,
        mis_z=c.get("mis_z"),
        moeuf=moeuf,
    )
    syn_z = c.get("syn_z")
    syn_note = (
        f" syn_z={syn_z:.3f} (≈0 expected; |syn_z|>2 flags data quality)."
        if syn_z is not None
        else ""
    )
    text = (
        (_reading.summary_text + syn_note)
        if _reading.summary_text
        else f"No gnomAD constraint data available for {gene_symbol}."
    )

    return ConstraintBundle(
        gene_symbol=gene_symbol,
        ensembl_id=gene_id,
        loeuf=loeuf,
        oe_lof=c.get("oe_lof"),
        oe_lof_lower=c.get("oe_lof_lower"),
        pli=pli,
        obs_lof=c.get("obs_lof"),
        exp_lof=c.get("exp_lof"),
        oe_mis=c.get("oe_mis"),
        oe_mis_lower=c.get("oe_mis_lower"),
        moeuf=moeuf,
        obs_mis=c.get("obs_mis"),
        exp_mis=c.get("exp_mis"),
        mis_z=c.get("mis_z"),
        obs_syn=c.get("obs_syn"),
        exp_syn=c.get("exp_syn"),
        syn_z=c.get("syn_z"),
        source_link=f"https://gnomad.broadinstitute.org/gene/{gene_id}",
        text=text,
    )


def _consequence_summary(variants: list[ClinVarVariant]) -> str:
    counts = Counter(v.major_consequence or "unknown" for v in variants)
    return ", ".join(f"{n} {c}" for c, n in counts.most_common())


async def get_clinvar_variants(ensembl_id: str, gene_symbol: str = "") -> ClinVarBundle:
    """Fetch ClinVar variants overlapping this gene from gnomAD's integrated dataset."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _graphql(client, _CLINVAR_QUERY, {"geneId": ensembl_id})

    raw: list[dict] = (data.get("gene") or {}).get("clinvar_variants") or []
    pathogenic, likely_pathogenic, benign = [], [], []

    for v in raw:
        sig = (v.get("clinical_significance") or "").lower()
        variant = ClinVarVariant(
            variant_id=v.get("variant_id", ""),
            clinical_significance=v.get("clinical_significance"),
            gold_stars=v.get("gold_stars"),
            hgvsc=v.get("hgvsc"),
            hgvsp=v.get("hgvsp"),
            major_consequence=v.get("major_consequence"),
            in_gnomad=v.get("in_gnomad"),
        )
        if "pathogenic" in sig and "likely" not in sig:
            pathogenic.append(variant)
        elif "likely pathogenic" in sig:
            likely_pathogenic.append(variant)
        elif "benign" in sig:
            benign.append(variant)

    label = gene_symbol or ensembl_id
    parts = []
    if pathogenic:
        parts.append(f"{len(pathogenic)} Pathogenic ({_consequence_summary(pathogenic)})")
    if likely_pathogenic:
        parts.append(
            f"{len(likely_pathogenic)} Likely Pathogenic ({_consequence_summary(likely_pathogenic)})"
        )
    if benign:
        parts.append(f"{len(benign)} Benign/Likely Benign")
    text = (
        f"ClinVar variants in {label} (via gnomAD): {', '.join(parts)} out of {len(raw)} total."
        if raw
        else f"No ClinVar variants found in gnomAD for {label}."
    )

    return ClinVarBundle(
        gene_symbol=gene_symbol,
        ensembl_id=ensembl_id,
        pathogenic=pathogenic,
        likely_pathogenic=likely_pathogenic,
        benign=benign,
        total_clinvar=len(raw),
        text=text,
    )


async def get_lof_variants(ensembl_id: str, gene_symbol: str = "") -> LofVariantBundle:
    """Fetch observed high-confidence pLoF variants (natural heterozygous knockouts) from gnomAD v4."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        data = await _graphql(client, _LOF_VARIANTS_QUERY, {"geneId": ensembl_id})

    all_variants: list[dict] = (data.get("gene") or {}).get("variants") or []

    # Filter to HC pLoF variants only; sort by AF descending.
    hc_lof = [v for v in all_variants if v.get("lof") == "HC"]
    hc_lof.sort(key=lambda v: (v.get("genome") or {}).get("af") or 0.0, reverse=True)

    reported: list[LofVariant] = []
    any_hom = False
    for v in hc_lof[:_MAX_LOF_VARIANTS]:
        g = v.get("genome") or {}
        af = g.get("af")
        if af is not None and af < _MIN_LOF_AF:
            continue
        hom = g.get("homozygote_count") or 0
        if hom > 0:
            any_hom = True
        population_af = {
            p["id"]: p["ac"] / p["an"]
            for p in (g.get("populations") or [])
            if (p.get("an") or 0) >= _MIN_POPULATION_AN
        }
        reported.append(
            LofVariant(
                variant_id=v.get("variant_id", ""),
                consequence=v.get("consequence"),
                hgvsc=v.get("hgvsc"),
                hgvsp=v.get("hgvsp"),
                lof=v.get("lof"),
                lof_filter=v.get("lof_filter"),
                lof_flags=v.get("lof_flags"),
                af=af,
                ac=g.get("ac"),
                an=g.get("an"),
                homozygote_count=hom,
                population_af=population_af,
            )
        )

    max_af = reported[0].af if reported else None
    skew_note = _population_skew_note(reported[0].population_af) if reported else None
    label = gene_symbol or ensembl_id
    if not hc_lof:
        text = f"No high-confidence pLoF variants observed in gnomAD v4 for {label}."
    else:
        text = (
            f"gnomAD v4: {len(hc_lof)} HC pLoF variants observed in {label}. "
            f"Most common AF={max_af:.2e}. "
            + (
                "Homozygous LoF carriers exist in gnomAD — biallelic loss is tolerated in the general population."
                if any_hom
                else "No homozygous carriers in gnomAD. Absence at this allele count is uninformative — not evidence of biallelic lethality; selection signal comes from LOEUF/o-e."
            )
            + (f" Note: {skew_note}" if skew_note else "")
        )

    return LofVariantBundle(
        gene_symbol=gene_symbol,
        ensembl_id=ensembl_id,
        hc_lof_count=len(hc_lof),
        reported_variants=reported,
        max_af=max_af,
        any_homozygous=any_hom,
        ancestry_skewed=bool(skew_note),
        text=text,
    )
