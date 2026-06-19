# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic gnomAD constraint interpretation and mechanism direction inference.

Pure module — no I/O, fully unit-testable.

    gnomAD floats ──▶ interpret_constraint() ──▶ ConstraintReading (labelled bands, correct)
    ClinVar P/LP list ┐
    ConstraintReading  ┴──▶ infer_mechanism_direction() ──▶ MechanismDirection

Reference bands correct errors E1–E3 from the TRPC6×FSGS report:
  E1: LOEUF 0.759 → "relatively LoF-tolerant", NOT haploinsufficient.
  E2: mis_z 1.70 (direction: higher = more constrained) → "no meaningful missense constraint".
  E3: raw pLoF count is NOT a selection statistic; selection signal is LOEUF/o-e.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from schemas.evidence import Direction

# ---------------------------------------------------------------------------
# Banding functions (deterministic, reference-based)
# ---------------------------------------------------------------------------


def loeuf_band(loeuf: float) -> str:
    """Band LOEUF (oe_lof_upper) into a labelled constraint tier.

    Lower LOEUF = more LoF-intolerant.  Classic haploinsufficient genes sit ~0.1–0.3.
    LOEUF 0.759 is LoF-tolerant — NOT haploinsufficient.
    """
    if loeuf < 0.35:
        return "strong LoF constraint (candidate haploinsufficient)"
    if loeuf < 0.75:
        return "moderate LoF constraint"
    return "relatively LoF-tolerant (NOT haploinsufficient)"


def pli_band(pli: float) -> str:
    if pli >= 0.9:
        return "pLI-intolerant (consistent with LoF constraint)"
    if pli <= 0.1:
        return "pLI-tolerant (consistent with LoF tolerance)"
    return "pLI-indeterminate"


def misz_band(mis_z: float) -> str:
    """Band mis_z — HIGHER value = MORE constrained (direction often inverted by LLMs)."""
    if mis_z >= 3.09:
        return "significant missense constraint"
    if mis_z >= 2.0:
        return "mild missense constraint"
    return "no meaningful missense constraint"


def moeuf_band(moeuf: float) -> str:
    """Band missense OEUF (oe_mis_upper).  Lower = more missense-intolerant."""
    if moeuf < 0.6:
        return "missense-intolerant (specific residues functionally critical)"
    if moeuf <= 0.8:
        return "intermediate missense constraint"
    return "missense-tolerant (gene accepts amino-acid change globally)"


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class ConstraintReading(BaseModel):
    gene_symbol: str

    loeuf: float | None = None
    loeuf_band: str = ""

    pli: float | None = None
    pli_band: str = ""

    mis_z: float | None = None
    misz_band: str = ""

    moeuf: float | None = None
    moeuf_band: str = ""

    # Homozygous-LoF evidence (from get_lof_variants)
    hom_lof_present: bool | None = None  # None = not queried
    hom_lof_note: str = ""

    # Pre-computed booleans consumed by guards and direction logic.
    is_lof_constrained: bool = False  # LOEUF < 0.35 or pLI > 0.9
    is_lof_tolerant: bool = False  # LOEUF ≥ 0.75 or pLI ≤ 0.1
    is_missense_constrained: bool = False  # mis_z ≥ 2.0 or moeuf < 0.8
    claims_haploinsufficiency_ok: bool = False  # True only when LOEUF < 0.35

    # Human-readable summary for LLM prompts — pre-interpreted, no raw floats.
    summary_text: str = ""


class MechanismDirection(BaseModel):
    direction: Direction
    mechanism: Literal["gof", "lof", "ambiguous"]
    confidence: float
    rationale: str
    supporting_variant_ids: list[str] = []


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def interpret_constraint(
    gene_symbol: str,
    loeuf: float | None = None,
    pli: float | None = None,
    mis_z: float | None = None,
    moeuf: float | None = None,
    hc_lof_count: int | None = None,
    any_homozygous: bool | None = None,
) -> ConstraintReading:
    """Deterministically band gnomAD constraint metrics into labelled, correct interpretations.

    Accepts primitives rather than the ConstraintBundle model to avoid circular imports.
    All inputs are optional; only available metrics are included in summary_text.
    """
    lb = loeuf_band(loeuf) if loeuf is not None else ""
    pb = pli_band(pli) if pli is not None else ""
    mzb = misz_band(mis_z) if mis_z is not None else ""
    mb = moeuf_band(moeuf) if moeuf is not None else ""

    is_lof_constrained = (loeuf is not None and loeuf < 0.35) or (pli is not None and pli > 0.9)
    is_lof_tolerant = (loeuf is not None and loeuf >= 0.75) or (pli is not None and pli <= 0.1)
    is_missense_constrained = (mis_z is not None and mis_z >= 2.0) or (
        moeuf is not None and moeuf < 0.8
    )
    claims_hi_ok = loeuf is not None and loeuf < 0.35

    # Homozygous-LoF note (E3 companion: absence at low AC ≠ biallelic lethality)
    hom_lof_note = ""
    if any_homozygous is True:
        hom_lof_note = (
            "Homozygous LoF carriers observed in gnomAD — biallelic loss is tolerated "
            "in the general population."
        )
    elif any_homozygous is False:
        count_str = f" (HC pLoF count={hc_lof_count})" if hc_lof_count is not None else ""
        hom_lof_note = (
            f"No homozygous LoF carriers in gnomAD{count_str}. "
            "Absence at this allele count is uninformative — it is NOT evidence of biallelic lethality. "
            "Selection signal comes from LOEUF/o-e, not the raw pLoF count."
        )

    # Build summary text with banded interpretations so LLMs have nothing to invert.
    parts: list[str] = []
    if loeuf is not None:
        parts.append(f"LOEUF={loeuf:.3f} ({lb})")
    if mis_z is not None:
        # mis_z direction note is critical: higher = more constrained
        parts.append(f"mis_z={mis_z:.2f} ({mzb}; note: higher mis_z = more missense-constrained)")
    if pli is not None:
        parts.append(f"pLI={pli:.4f} ({pb})")
    if moeuf is not None:
        parts.append(f"missense OEUF={moeuf:.3f} ({mb})")

    summary = f"Constraint interpretation for {gene_symbol}: " + "; ".join(parts) + "."
    if hom_lof_note:
        summary += " " + hom_lof_note

    return ConstraintReading(
        gene_symbol=gene_symbol,
        loeuf=loeuf,
        loeuf_band=lb,
        pli=pli,
        pli_band=pb,
        mis_z=mis_z,
        misz_band=mzb,
        moeuf=moeuf,
        moeuf_band=mb,
        hom_lof_present=any_homozygous,
        hom_lof_note=hom_lof_note,
        is_lof_constrained=is_lof_constrained,
        is_lof_tolerant=is_lof_tolerant,
        is_missense_constrained=is_missense_constrained,
        claims_haploinsufficiency_ok=claims_hi_ok,
        summary_text=summary,
    )


# Consequences classified as missense/gain-of-function-compatible
_MISSENSE_CONS = frozenset(
    {
        "missense_variant",
        "missense",
        "protein_altering_variant",
        "inframe_insertion",
        "inframe_deletion",
    }
)

# Consequences classified as truncating/LoF
_TRUNCATING_CONS = frozenset(
    {
        "stop_gained",
        "frameshift_variant",
        "splice_acceptor_variant",
        "splice_donor_variant",
        "start_lost",
        "stop_lost",
        "transcript_ablation",
    }
)

# Consequence calls that are data-quality artifacts, not mechanism votes — a
# "Pathogenic" synonymous call almost never reflects a true protein-coding
# mechanism and must not dilute/inflate either consequence bucket.
_SYNONYMOUS_CONS = frozenset({"synonymous_variant", "synonymous"})

_GOF_MISSENSE_FRAC_THRESHOLD = 0.70
_GOF_LEAN_MISSENSE_FRAC_FLOOR = 0.50


def _filter_plp_votes(all_plp: list[dict]) -> list[dict]:
    """Drop no-assertion/no-criteria calls and synonymous-pathogenic artifacts.

    ``gold_stars in {None, 0}`` means no expert-panel criteria were applied —
    such calls should not vote on mechanism direction. Synonymous variants are
    very rarely the true pathogenic mechanism in a missense/truncating spectrum
    and should never count toward either consequence bucket.
    """
    filtered = []
    for v in all_plp:
        gold_stars = v.get("gold_stars")
        if gold_stars in (None, 0):
            continue
        if (v.get("major_consequence") or "").lower() in _SYNONYMOUS_CONS:
            continue
        filtered.append(v)
    return filtered


def _moi_flags(inheritance_mode: str | None) -> tuple[bool, bool]:
    """(is_dominant, is_recessive) substring check over a normalized HPO MOI label.

    Catches "Autosomal dominant"/"X-linked dominant"/"Semidominant" and
    "Autosomal recessive"/"X-linked recessive" — the only two axes that matter
    for the GoF/LoF tie-breaker (Mitochondrial/Unspecified/Y-linked/Gonosomal
    deliberately match neither and have no effect).
    """
    moi = (inheritance_mode or "").lower()
    return "dominant" in moi, "recessive" in moi


# Confidence adjustments applied when inheritance mode corroborates or
# conflicts with a spectrum-driven mechanism call. Ties only break within the
# lean zone; firm calls only get a confidence nudge — the spectrum always wins.
_MOI_CORROBORATE_BONUS = 0.05
_MOI_CONFLICT_MALUS = 0.15
_MOI_TIE_BREAK_CONFIDENCE_FLOOR = 0.60
_MOI_TIE_BREAK_CONFIDENCE_PER_MISSENSE = 0.02
_MOI_TIE_BREAK_CONFIDENCE_CAP = 0.75


def infer_mechanism_direction(
    reading: ConstraintReading,
    all_plp: list[dict],
    inheritance_mode: str | None = None,
) -> MechanismDirection:
    """Deterministically infer which direction of perturbation drives the disease.

    Uses a conservative decision table; emits UNSPECIFIED rather than guessing.

    Args:
        reading: Pre-computed ConstraintReading from interpret_constraint().
        all_plp: List of P/LP variant dicts, each with at minimum a
                 ``major_consequence`` key.  Dicts follow the ClinVarVariant
                 field names (major_consequence, variant_id, gold_stars).
        inheritance_mode: Optional HPO mode-of-inheritance label (e.g.
                 "Autosomal dominant", from ClinGen or HPO/Monarch). Acts only
                 as a tie-breaker in the 0.5-0.7 missense-frac lean zone, or a
                 confidence nudge on an already-firm call — it never flips a
                 clear spectrum-driven direction.

    Returns:
        MechanismDirection with Direction.INHIBIT (GoF), Direction.ACTIVATE (LoF),
        or Direction.UNSPECIFIED (ambiguous).
    """
    moi_dominant, moi_recessive = _moi_flags(inheritance_mode)
    votes = _filter_plp_votes(all_plp)

    if len(votes) < 2:
        return MechanismDirection(
            direction=Direction.UNSPECIFIED,
            mechanism="ambiguous",
            confidence=0.0,
            rationale=(
                "Insufficient gold-star (≥1), non-synonymous P/LP variants (<2) "
                "to infer mechanism direction."
            ),
        )

    n_total = len(votes)
    n_missense = sum(
        1 for v in votes if (v.get("major_consequence") or "").lower() in _MISSENSE_CONS
    )
    n_truncating = sum(
        1 for v in votes if (v.get("major_consequence") or "").lower() in _TRUNCATING_CONS
    )

    missense_frac = n_missense / n_total
    loeuf_str = f"LOEUF={reading.loeuf:.3f}" if reading.loeuf is not None else "LoF-tolerant"

    # GoF pattern: missense-predominant + LoF-tolerant. Either the gene clears the
    # ≥70% missense fast path, or missense dominates truncating by a 2:1 ratio
    # (replaces the old rigid "n_truncating <= 1" gate, which a handful of
    # low-confidence truncating calls could defeat even with a clear GoF signal).
    if (
        reading.is_lof_tolerant
        and n_missense >= 2
        and (missense_frac >= _GOF_MISSENSE_FRAC_THRESHOLD or n_missense >= 2 * n_truncating)
    ):
        supporting = [
            v.get("variant_id", "")
            for v in votes
            if (v.get("major_consequence") or "").lower() in _MISSENSE_CONS and v.get("variant_id")
        ][:10]
        confidence = min(0.85, 0.55 + 0.03 * n_missense)
        rationale = (
            f"Dominant missense P/LP spectrum: {n_missense}/{n_total} missense variants "
            f"(gold-star ≥1, synonymous excluded) in a LoF-tolerant gene "
            f"({loeuf_str}, {reading.loeuf_band}). "
            "Pattern is consistent with gain-of-function Mendelian disease — "
            "genetics support target INHIBITION."
        )
        if moi_recessive:
            confidence = max(0.0, confidence - _MOI_CONFLICT_MALUS)
            rationale += (
                " NOTE: reported inheritance is recessive, which is atypical for a "
                "gain-of-function mechanism (true GoF variants are virtually always dominant) — "
                "the spectrum-driven call is retained but confidence is reduced; manual review recommended."
            )
        elif moi_dominant:
            confidence = min(0.85, confidence + _MOI_CORROBORATE_BONUS)
            rationale += (
                f" Reported {inheritance_mode} inheritance corroborates this gain-of-function call."
            )
        return MechanismDirection(
            direction=Direction.INHIBIT,
            mechanism="gof",
            confidence=confidence,
            rationale=rationale,
            supporting_variant_ids=supporting,
        )

    # LoF/haploinsufficiency pattern: truncating-predominant + LoF-constrained.
    if n_truncating >= 2 and reading.is_lof_constrained:
        supporting = [
            v.get("variant_id", "")
            for v in votes
            if (v.get("major_consequence") or "").lower() in _TRUNCATING_CONS
            and v.get("variant_id")
        ][:10]
        loeuf_str = f"LOEUF={reading.loeuf:.3f}" if reading.loeuf is not None else "LoF-constrained"
        confidence = min(0.85, 0.55 + 0.03 * n_truncating)
        rationale = (
            f"Pathogenic truncating/LoF variants: {n_truncating}/{n_total} truncating "
            f"(gold-star ≥1, synonymous excluded) in a LoF-constrained gene "
            f"({loeuf_str}, {reading.loeuf_band}). "
            "Pattern is consistent with haploinsufficiency/LoF mechanism — "
            "inhibition is contraindicated; genetics support target ACTIVATION/restoration."
        )
        if moi_dominant or moi_recessive:
            confidence = min(0.85, confidence + _MOI_CORROBORATE_BONUS)
            rationale += (
                f" Reported {inheritance_mode} inheritance is compatible with this "
                "haploinsufficiency/LoF call."
            )
        return MechanismDirection(
            direction=Direction.ACTIVATE,
            mechanism="lof",
            confidence=confidence,
            rationale=rationale,
            supporting_variant_ids=supporting,
        )

    # GoF lean: missense merely predominates in a LoF-tolerant gene without
    # clearing the fast path or dominance ratio. Inject a low-confidence
    # direction rather than going silent on "ambiguous" — the lens should
    # always see a lean when one exists, even a weak one.
    if (
        reading.is_lof_tolerant
        and n_missense >= 2
        and _GOF_LEAN_MISSENSE_FRAC_FLOOR <= missense_frac < _GOF_MISSENSE_FRAC_THRESHOLD
    ):
        supporting = [
            v.get("variant_id", "")
            for v in votes
            if (v.get("major_consequence") or "").lower() in _MISSENSE_CONS and v.get("variant_id")
        ][:10]

        if moi_dominant:
            # Tie-break: dominant inheritance + missense-predominant + LoF-tolerant
            # promotes the lean into a firm-strength GoF call (WS3).
            confidence = min(
                _MOI_TIE_BREAK_CONFIDENCE_CAP,
                _MOI_TIE_BREAK_CONFIDENCE_FLOOR
                + _MOI_TIE_BREAK_CONFIDENCE_PER_MISSENSE * n_missense,
            )
            rationale = (
                f"Missense P/LP variants predominate ({n_missense}/{n_total} missense vs. "
                f"{n_truncating} truncating, gold-star ≥1, synonymous excluded) in a LoF-tolerant "
                f"gene ({loeuf_str}, {reading.loeuf_band}). Reported {inheritance_mode} inheritance "
                "breaks the tie toward gain-of-function — genetics support target INHIBITION."
            )
            return MechanismDirection(
                direction=Direction.INHIBIT,
                mechanism="gof",
                confidence=confidence,
                rationale=rationale,
                supporting_variant_ids=supporting,
            )

        confidence = min(0.50, 0.30 + 0.02 * n_missense)
        rationale = (
            f"Missense P/LP variants predominate but do not clearly dominate: "
            f"{n_missense}/{n_total} missense vs. {n_truncating} truncating "
            f"(gold-star ≥1, synonymous excluded) in a LoF-tolerant gene "
            f"({loeuf_str}, {reading.loeuf_band}). "
            "Low-confidence lean toward gain-of-function — genetics weakly support "
            "target INHIBITION; manual review recommended."
        )
        if moi_recessive:
            # Conflict: recessive inheritance is atypical for GoF. Spectrum still
            # wins (it's the only signal pointing anywhere), but confidence drops.
            confidence = max(0.0, confidence - _MOI_CONFLICT_MALUS)
            rationale += (
                " NOTE: reported inheritance is recessive, which conflicts with this "
                "gain-of-function lean — confidence reduced; manual review recommended."
            )
        return MechanismDirection(
            direction=Direction.INHIBIT,
            mechanism="gof",
            confidence=confidence,
            rationale=rationale,
            supporting_variant_ids=supporting,
        )

    return MechanismDirection(
        direction=Direction.UNSPECIFIED,
        mechanism="ambiguous",
        confidence=0.0,
        rationale=(
            f"Mixed or insufficient P/LP consequence spectrum "
            f"({n_missense} missense, {n_truncating} truncating of {n_total} total, "
            "gold-star ≥1, synonymous excluded) "
            "or constraint does not clearly support either GoF or LoF — "
            "mechanism direction is ambiguous; manual review recommended."
        ),
    )


# ---------------------------------------------------------------------------
# Additional reading functions
# ---------------------------------------------------------------------------


def interpret_ot_genetic_score(
    score: float,
    datatype_name: str = "genetic_association",
) -> str:
    """Return a context string for an Open Targets genetic-association datatype score.

    OT datatype-level scores (e.g. genetic_association) can exceed the overall gene-disease
    score and are ClinVar/curation-driven — they are NOT a population-genetics validity score.
    A score of ~0.95 reflects depth of ClinVar annotation, not independent replication.
    """
    if score >= 0.90:
        tier = "very high annotation depth"
        caveat = (
            "This score reflects ClinVar curation depth / expert-panel classifications, "
            "NOT independent population-genetics replication. "
            "Treat as strong prior, not as overwhelming standalone proof — "
            "corroborate with variant-level P/LP review and segregation data."
        )
    elif score >= 0.70:
        tier = "moderate-to-high annotation"
        caveat = (
            "Moderate OT genetic_association score. "
            "Supports but does not alone establish causality — combine with variant-level evidence."
        )
    else:
        tier = "limited annotation"
        caveat = (
            "Low OT genetic_association score; treat as weak prior. "
            "Check for Mendelian-disease ClinVar variants that may not be captured here."
        )

    return (
        f"OT {datatype_name} score={score:.3f} ({tier}). "
        f"Note: this is a datatype-level score, not an overall gene-disease score — "
        f"it can exceed the overall score and is driven by ClinVar/expert-panel curation. "
        f"{caveat}"
    )


def interpret_depmap_relevance(
    mean_chronos: float | None,
    dependency_fraction: float | None,
    is_common_essential: bool,
    is_oncology_indication: bool,
) -> str:
    """Return a context string for DepMap CRISPR dependency data.

    For non-oncology indications with no meaningful cancer-cell-line dependency,
    this function enforces the rule that cancer-lineage data cannot establish a
    therapeutic window for a non-cancer disease target.
    """
    parts: list[str] = []

    if not is_oncology_indication:
        parts.append(
            "IMPORTANT — non-oncology indication: DepMap measures dependency in *cancer* "
            "cell lines. Cancer-lineage essentiality is at best indirect mechanism evidence "
            "for a non-cancer disease target and must NOT be presented as a therapeutic window."
        )

    if is_common_essential:
        parts.append(
            "Gene is pan-cancer common essential (DepMap). "
            "High on-target toxicity risk in normal tissue — "
            "do NOT describe any cancer lineage as a 'therapeutic window' without "
            "strong structural or expression selectivity evidence."
        )
    elif (
        mean_chronos is not None
        and dependency_fraction is not None
        and mean_chronos > -0.5
        and dependency_fraction < 0.10
    ):
        parts.append(
            f"Gene shows near-zero DepMap dependency (mean Chronos={mean_chronos:.3f}, "
            f"dependency fraction={dependency_fraction:.1%}). "
            "Do NOT describe this gene as 'selectively essential' or claim a 'therapeutic window' "
            "from cancer-cell-line data — the data show no meaningful cancer dependency. "
            "For a non-oncology target this is expected; for oncology this is a negative signal."
        )

    return " ".join(parts) if parts else ""


def interpret_expression_context(
    bulk_tpm: float | None,
    hpa_specificity: str | None,
    disease_tissue: str,
) -> str:
    """Return a context string when bulk tissue TPM is low but cell-type context is relevant.

    Prevents the common error where low bulk GTEx TPM for a disease tissue is taken as
    evidence that the target is absent in that tissue — when the actual disease cell type
    is a minor population diluted in bulk RNA-seq.
    """
    if bulk_tpm is None:
        return ""

    low_bulk = bulk_tpm < 5.0  # TPM < 5 is commonly (mis)read as "absent"

    hpa_specific = hpa_specificity and hpa_specificity.lower() in (
        "tissue enhanced",
        "group enhanced",
        "cell type enhanced",
        "tissue specific",
        "cell type specific",
    )

    if low_bulk:
        caveat_parts: list[str] = [
            f"Bulk TPM={bulk_tpm:.2f} in {disease_tissue} tissue (low bulk expression). "
            "CRITICAL INTERPRETATION NOTE: low bulk TPM does NOT establish expression absence "
            "in the disease-relevant cell type."
        ]
        if hpa_specific:
            caveat_parts.append(
                f"HPA specificity='{hpa_specificity}' indicates cell-type-level enrichment "
                "that is diluted in bulk GTEx data. "
                "Only bulk data are available here — single-cell resolution is not available. "
                "State this limitation explicitly; do NOT conclude expression is 'absent' or "
                "'tissue-specificity unfavorable' based on bulk TPM alone."
            )
        else:
            caveat_parts.append(
                "Bulk GTEx can dilute expression in minor cell populations (e.g. podocytes in kidney). "
                "Absence of bulk signal is uninformative about cell-type-specific expression. "
                "State this limitation explicitly."
            )
        return " ".join(caveat_parts)

    broad_hpa = bool(hpa_specificity) and hpa_specificity.lower() == "low tissue specificity"
    high_bulk = bulk_tpm >= 20.0

    if high_bulk or broad_hpa:
        caveat_parts = [
            f"Bulk TPM={bulk_tpm:.2f} in {disease_tissue} tissue"
            + (f", HPA specificity='{hpa_specificity}'" if hpa_specificity else "")
            + " — broad/high expression. "
            "CRITICAL INTERPRETATION NOTE: expression breadth or magnitude alone is NOT "
            "evidence of toxicity. On-target toxicity risk must be assessed from target "
            "biology (is the gene's function essential in that tissue?), exposure margins, "
            "human genetics (LoF-tolerance, viable heterozygous/homozygous carriers), and "
            "clinical safety data — do NOT infer toxicity from tissue distribution alone."
        ]
        return " ".join(caveat_parts)

    return ""


def interpret_expression_context_for_mechanism(
    bulk_tpm: float | None,
    hpa_specificity: str | None,
    disease_tissue: str,
) -> str:
    """Mechanism-framed counterpart to `interpret_expression_context`, for the biology lens.

    Shares the low-bulk "cell-type dilution" caveat (mechanism-relevant for both lenses)
    but omits the high-bulk/broad-HPA branch, which is phrased in toxicity-risk language
    that has no place in a druggability/mechanism-of-action verdict.
    """
    if bulk_tpm is None or bulk_tpm >= 5.0:
        return ""

    hpa_specific = hpa_specificity and hpa_specificity.lower() in (
        "tissue enhanced",
        "group enhanced",
        "cell type enhanced",
        "tissue specific",
        "cell type specific",
    )

    caveat_parts: list[str] = [
        f"Bulk TPM={bulk_tpm:.2f} in {disease_tissue} tissue (low bulk expression). "
        "CRITICAL INTERPRETATION NOTE: low bulk TPM does NOT establish that the gene "
        "lacks a mechanistic role in this tissue — bulk RNA-seq dilutes signal from "
        "minor disease-relevant cell populations."
    ]
    if hpa_specific:
        caveat_parts.append(
            f"HPA specificity='{hpa_specificity}' indicates cell-type-level enrichment "
            "diluted in bulk GTEx data. Discuss mechanism in terms of disease cell-type "
            "localization (e.g. podocyte / slit diaphragm), not bulk tissue TPM rank."
        )
    else:
        caveat_parts.append(
            "Bulk GTEx can dilute expression in minor cell populations (e.g. podocytes in "
            "kidney). Discuss mechanism in terms of disease cell-type localization where "
            "the claims support it, rather than bulk tissue TPM rank."
        )
    return " ".join(caveat_parts)


def interpret_gof_tolerance_support(mechanism: str | None, is_lof_tolerant: bool) -> str:
    """Return a tolerability-supporting context string for a GoF mechanism + LoF-tolerant gene.

    For a Mendelian gain-of-function target, human population LoF-tolerance shows that
    reduced gene dosage/function is tolerated — this is a *supporting* signal for the
    safety of pharmacological inhibition, not a standalone toxicity concern. Without this
    guard, LoF-tolerance from gnomAD constraint is liable to be read as a druggability/
    safety negative regardless of mechanism direction.
    """
    if mechanism != "gof" or not is_lof_tolerant:
        return ""
    return (
        "Mechanism inference (pre-computed, deterministic): gain-of-function — inhibition is "
        "the corrective therapeutic direction. Gene is LoF-tolerant (gnomAD constraint): reduced "
        "gene dosage/function is tolerated in the human population. This SUPPORTS the tolerability "
        "of pharmacological inhibition and must NOT be presented as a safety negative by itself. "
        "COUNTERWEIGHT (do not drop this when writing the verdict): germline LoF-tolerance reflects "
        "evolutionary/developmental tolerance of reduced gene dosage — it does NOT by itself "
        "establish that *chronic pharmacological inhibition* is safe (dosing, timing, and "
        "reversibility differ from germline loss). Combine with mouse KO phenotype and clinical "
        "exposure data before drawing a final safety conclusion, and do not let this signal alone "
        "push toxicity-axis confidence above the no-clinical-exposure-data ceiling."
    )


def interpret_patent_landscape(patent_count: int) -> str:
    """Return a framing string for the IP landscape given the raw patent count.

    Prevents the self-contradictory error of claiming 'no patents / clean slate'
    when patent_count > 0.
    """
    if patent_count == 0:
        return (
            "No patents retrieved in this run. "
            "Note: absence of retrieved patents does NOT confirm a clear IP landscape — "
            "retrieval is limited by search scope. Recommend dedicated FTO analysis."
        )
    return (
        f"{patent_count} patent(s) retrieved. "
        "IMPORTANT: do NOT describe the IP landscape as 'free of patents', "
        "'clean slate', or 'no known patents' — patents exist in the retrieval. "
        "Assess claim scope and jurisdiction rather than raw count alone."
    )


# ---------------------------------------------------------------------------
# Output guard helpers
# ---------------------------------------------------------------------------

_HI_PATTERN = re.compile(r"haploinsufficien\w*", re.IGNORECASE)
_MISSENSE_CRIT_PATTERN = re.compile(
    # Match "missense" followed by up to 4 intermediate words then a criticality adjective.
    # Catches both "missense critical" and "missense variants are functionally critical".
    r"missense\s+(?:\w+\s*){0,4}(critical|intolerant|important|essential|crucial|conserved)",
    re.IGNORECASE,
)
_PLOF_SELECTION_PATTERN = re.compile(
    r"\d+\s+(observed|HC|high.confidence)\s+p?LoF\b.*?(?:indicat|suggest|support|select)",
    re.IGNORECASE | re.DOTALL,
)


def apply_constraint_guards(
    text: str,
    reading: ConstraintReading,
) -> str:
    """Annotate or strip constraint-interpretation errors that contradict the data.

    Primary fix is supplying banded text so the LLM has nothing to invert (Step 1).
    This is the safety net: runs on the LLM-generated narrative/rationale after parsing.

    Returns the (possibly annotated) text.
    """
    if not text:
        return text

    parts: list[str] = [text]

    if not reading.claims_haploinsufficiency_ok and _HI_PATTERN.search(text):
        parts.append(
            f"[⚠ CONSTRAINT GUARD: The text above mentions haploinsufficiency, "
            f"but LOEUF={reading.loeuf:.3f} ({reading.loeuf_band}) does not support this — "
            f"LOEUF ≥ 0.35 rules out haploinsufficiency. This claim should be removed.]"
            if reading.loeuf is not None
            else "[⚠ CONSTRAINT GUARD: Haploinsufficiency claim present but LOEUF does not support it.]"
        )

    if not reading.is_missense_constrained and _MISSENSE_CRIT_PATTERN.search(text):
        mz_str = (
            f"mis_z={reading.mis_z:.2f} ({reading.misz_band})" if reading.mis_z is not None else ""
        )
        mo_str = (
            f"missense OEUF={reading.moeuf:.3f} ({reading.moeuf_band})"
            if reading.moeuf is not None
            else ""
        )
        metrics = ", ".join(p for p in [mz_str, mo_str] if p) or "missense metrics"
        parts.append(
            f"[⚠ CONSTRAINT GUARD: The text claims missense criticality/intolerance, "
            f"but {metrics} — the gene does not show significant global missense constraint. "
            f"Pathogenic missense variants may still exist at specific residues (GoF), "
            f"but the gene is not globally missense-intolerant.]"
        )

    if _PLOF_SELECTION_PATTERN.search(text):
        parts.append(
            "[⚠ CONSTRAINT GUARD: Raw pLoF variant count cited as selection evidence — "
            "the count alone is NOT a selection metric. Selection signal comes from "
            "LOEUF/o-e ratio, not the observed variant count.]"
        )

    return "\n".join(parts) if len(parts) > 1 else text


# ---------------------------------------------------------------------------
# Mendelian causality floor (WS4) — genetics lens post-LLM reconciliation
# ---------------------------------------------------------------------------

_MENDELIAN_CLINGEN_GRADES = frozenset({"Definitive", "Strong"})
_MENDELIAN_GRAPH_SCORE_FLOOR = 0.70

_GWAS_ABSENCE_NEGATIVE_PATTERNS = (
    re.compile(
        r"(no|lack(?:s|ing)?|absence of|insufficient|limited|without)\s+"
        r"(?:\w+\s+){0,4}(GWAS|genome-wide association|colocali[sz]ation|\bcoloc\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(GWAS|genome-wide association|colocali[sz]ation|\bcoloc\b)"
        r"(?:\s+\w+){0,6}\s+"
        r"(absent|lacking|missing|weak|insufficient|fails? to support|"
        r"does not support|undermines?|argues? against)",
        re.IGNORECASE,
    ),
)


def compute_mendelian_grade(
    high_star_plp: int,
    plp_count: int,
    clingen_classification: str | None,
    graph_association: dict | None,
) -> bool:
    """True if the gene-disease pair has Mendelian-grade genetic validation.

    Gold-star P/LP support is required for the variant-count branch — a high
    raw `plp_count` of unreviewed/no-assertion variants must never qualify on
    its own (the same gold-star discipline WS1 applies to mechanism inference).
    """
    if high_star_plp >= 1 and plp_count >= 2:
        return True
    if clingen_classification in _MENDELIAN_CLINGEN_GRADES:
        return True
    if graph_association and graph_association.get("corroborates_causality"):
        score = graph_association.get("diseases_score")
        if isinstance(score, (int, float)) and score >= _MENDELIAN_GRAPH_SCORE_FLOOR:
            return True
    return False


def apply_mendelian_floor_guard(text: str) -> str:
    """Annotate rationale/narrative text that treats GWAS/coloc absence as a
    causality negative — expected and uninformative once Mendelian-grade
    P/LP, ClinGen, or graph evidence already establishes causality.

    Same annotate-don't-silently-rewrite pattern as `apply_constraint_guards`.
    """
    if not text:
        return text
    if any(p.search(text) for p in _GWAS_ABSENCE_NEGATIVE_PATTERNS):
        return text + (
            "\n[⚠ MENDELIAN FLOOR GUARD: This text treats GWAS/colocalization absence "
            "as negative evidence, but Mendelian-grade causality support is already "
            "established (gold-star P/LP, ClinGen Definitive/Strong, and/or strong "
            "knowledge-graph corroboration). Absence of common-variant signal is "
            "EXPECTED for a Mendelian disease gene and must not lower causality.]"
        )
    return text
