# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-lens intermediate report writer.

Each interpretation lens writes a self-contained markdown file immediately
after it runs. Beyond the verdict, the report cross-references the **original
source evidence** the lens reasoned over — every kept PMID / NCT / patent /
gene record is listed with a clickable link, and each axis cites the numbered
sources behind it (resolved through ``CoreClaim.source_evidence_id``).

Output path: results/report/{gene}/{disease_id}/{direction}/lenses/{lens_name}.md
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, claim_matches_lens
from agents.synthesis.report.citations import (
    cite,
    evidence_detail,
    first_author,
    is_literature,
    pub_year,
    quality_rank,
    quality_stars,
    row_extra,
    row_type,
)
from core.persistence.artifact_store import _safe_id
from schemas.evidence import CoreClaim, Evidence, EvidenceType
from schemas.verdicts import LensVerdict

logger = logging.getLogger(__name__)

_REPORT_ROOT = Path(os.getenv("RESULTS_DIR", "./results")) / "report"

_VERDICT_LABEL = {
    "support": "Support",
    "oppose": "Oppose",
    "neutral": "Neutral",
    "insufficient_evidence": "Insufficient evidence",
}

# Friendly group labels for the empirical (non-literature) evidence-type buckets
# a lens may surface — used in the Empirical table's Type column.
_TYPE_GROUP = {
    "patent": "Patents",
    "clinical_trial": "Clinical Trials",
    "genetics": "Genetics & Associations",
    "constraint": "Constraint",
    "omics": "Omics & Expression",
    "expression": "Omics & Expression",
    "functional_genomics": "Functional Genomics",
    "druggability": "Druggability",
    "regulatory": "Regulatory",
}


def write_lens_report(
    verdict: LensVerdict,
    disease_id: str,
    evidence_rows: list[Evidence] | None = None,
    claims: list[CoreClaim] | None = None,
    ot_extra: dict | None = None,
    quality_map: dict | None = None,
) -> str | None:
    """Write a per-lens markdown report and return its file path (or None on error).

    ``evidence_rows`` are the run's kept ``Evidence`` (system of record); ``claims``
    are the extracted ``CoreClaim`` rows. Both are filtered to this lens's evidence
    types so the report cites only the sources the lens actually considered.
    ``ot_extra`` is the ``Evidence.extra`` dict from the OpenTargets retrieval step;
    it is rendered as a structured data block (tractability, known drugs, safety
    liabilities, mouse phenotypes) depending on which lens is being written.
    ``quality_map`` is the run's source-quality assessment, keyed by evidence_id
    string — same shape as the one consumed by report.md/full_report.md.
    """
    try:
        lens_dir = (
            _REPORT_ROOT / verdict.target_gene / _safe_id(disease_id) / verdict.direction.value / "lenses"
        )
        lens_dir.mkdir(parents=True, exist_ok=True)
        path = lens_dir / f"{verdict.lens}.md"
        path.write_text(
            _render(verdict, evidence_rows or [], claims or [], ot_extra or {}, quality_map or {}),
            encoding="utf-8",
        )
        return str(path.resolve())
    except Exception as exc:
        logger.warning("lens_report: failed to write %s report: %s", verdict.lens, exc)
        return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _pub_year_int(row: Any) -> int:
    try:
        return int(row_extra(row).get("pub_year") or 0)
    except (TypeError, ValueError):
        return 0


def _sort_key(row: Any, quality_map: dict) -> tuple:
    # Literature sorts highest-quality-first then most-recent-first — same ordering
    # as report.md's `_lit_sort_key` in agent.py — so citation numbers and reading
    # order agree across the dossier and the per-lens reports.
    if is_literature(row):
        q = quality_map.get(str(getattr(row, "evidence_id", "")))
        return (0, -quality_rank(q), -_pub_year_int(row))
    return (1, row_type(row), str(getattr(row, "source", "")))


def _build_citation_index(
    evidence_rows: list[Evidence],
    quality_map: dict | None = None,
) -> tuple[list[Evidence], dict[UUID, int]]:
    """Order the relevant evidence and assign each a stable citation number."""
    quality_map = quality_map or {}
    ordered = sorted(evidence_rows, key=lambda row: _sort_key(row, quality_map))
    num_by_evid: dict[UUID, int] = {}
    deduped: list[Evidence] = []
    for row in ordered:
        evid = getattr(row, "evidence_id", None)
        if evid is None or evid in num_by_evid:
            continue
        num_by_evid[evid] = len(deduped) + 1
        deduped.append(row)
    return deduped, num_by_evid


def _axis_sources(
    claim_ids: list[str],
    claim_source_num: dict[str, int],
    num_by_evid: dict[UUID, int],
) -> str:
    nums: set[int] = set()
    for cid in claim_ids:
        if cid in claim_source_num:
            nums.add(claim_source_num[cid])
            continue
        # Some models cite the source evidence id directly rather than the claim id.
        try:
            evid = UUID(cid)
        except (ValueError, TypeError):
            continue
        if evid in num_by_evid:
            nums.add(num_by_evid[evid])
    return ", ".join(f"[{n}]" for n in sorted(nums)) if nums else "—"


def _axis_table(
    v: LensVerdict,
    claim_source_num: dict[str, int],
    num_by_evid: dict[UUID, int],
) -> str:
    if not v.axes:
        return "_No axes available._"
    lines = [
        "| Axis | Verdict | Confidence | Rationale | Sources |",
        "| --- | --- | --- | --- | --- |",
    ]
    for ax in v.axes:
        ax_name = ax.axis.replace("_", " ").title()
        ax_label = "Yes" if ax.verdict is True else ("No" if ax.verdict is False else "Uncertain")
        rationale = (ax.rationale or "").replace("|", "\\|")
        sources = _axis_sources(ax.supporting_claim_ids, claim_source_num, num_by_evid)
        lines.append(f"| {ax_name} | {ax_label} | {ax.confidence:.0%} | {rationale} | {sources} |")
    return "\n".join(lines)


def _literature_table(
    evidence: list[Evidence],
    num_by_evid: dict[UUID, int],
    quality_map: dict,
) -> str:
    lines = [
        "| # | Source | Detail | Quality | Year | First Author |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in evidence:
        n = num_by_evid.get(getattr(row, "evidence_id", None), 0)
        quality = quality_stars(quality_map.get(str(getattr(row, "evidence_id", ""))))
        lines.append(
            f"| {n} | {cite(row)} | {evidence_detail(row) or '—'} "
            f"| {quality} | {pub_year(row)} | {first_author(row)} |"
        )
    return "\n".join(lines)


def _empirical_table(
    evidence: list[Evidence],
    num_by_evid: dict[UUID, int],
    quality_map: dict,
) -> str:
    lines = [
        "| # | Source | Type | Detail | Quality |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in evidence:
        n = num_by_evid.get(getattr(row, "evidence_id", None), 0)
        group = _TYPE_GROUP.get(row_type(row), row_type(row))
        quality = quality_stars(quality_map.get(str(getattr(row, "evidence_id", ""))))
        lines.append(f"| {n} | {cite(row)} | {group} | {evidence_detail(row) or '—'} | {quality} |")
    return "\n".join(lines)


def _evidence_section(
    evidence: list[Evidence],
    num_by_evid: dict[UUID, int],
    quality_map: dict | None = None,
) -> str:
    if not evidence:
        return (
            "_No source evidence of this lens's type passed screening for this run._ "
            "The verdict above reflects that evidence gap rather than a contrary finding."
        )
    quality_map = quality_map or {}
    lit_rows = [r for r in evidence if is_literature(r)]
    empirical_rows = [r for r in evidence if not is_literature(r)]

    lit_block = (
        _literature_table(lit_rows, num_by_evid, quality_map)
        if lit_rows
        else "_No literature evidence for this lens._"
    )
    empirical_block = (
        _empirical_table(empirical_rows, num_by_evid, quality_map)
        if empirical_rows
        else "_No empirical (non-literature) evidence for this lens._"
    )

    return (
        f"### Literature ({len(lit_rows)})\n\n{lit_block}\n\n"
        f"### Empirical ({len(empirical_rows)})\n\n{empirical_block}"
    )


def _claims_section(
    claims: list[CoreClaim],
    claim_source_num: dict[str, int],
) -> str:
    if not claims:
        return "_No atomic claims were extracted for this lens._"
    lines = [
        "| Source | Claim | Direction | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    ranked_claims = sorted(claims, key=lambda c: c.confidence if c.confidence is not None else -1.0, reverse=True)
    for c in ranked_claims:
        src_num = claim_source_num.get(str(c.evidence_id))
        src = f"[{src_num}]" if src_num else "—"
        text = (c.claim_text or "").replace("|", "\\|").replace("\n", " ").strip()
        direction = c.direction.value if hasattr(c.direction, "value") else str(c.direction)
        conf = f"{c.confidence:.0%}" if c.confidence is not None else "—"
        lines.append(f"| {src} | {text} | {direction} | {conf} |")
    return "\n".join(lines)


def _depmap_section(functional_rows: list[Evidence]) -> str:
    """Render a DepMap CRISPR dependency block from functional_genomics evidence."""
    depmap_row: Evidence | None = None
    for row in functional_rows:
        if str(getattr(row, "source", "")).startswith("depmap:"):
            depmap_row = row
            break
    if depmap_row is None:
        return ""

    ex = row_extra(depmap_row)
    mean = ex.get("gene_effect_mean")
    std = ex.get("gene_effect_std")
    q1 = ex.get("gene_effect_q1")
    median = ex.get("gene_effect_median")
    q3 = ex.get("gene_effect_q3")
    n_dep = ex.get("num_dependent_lines")
    n_total = ex.get("total_lines")
    dep_frac = ex.get("dependency_fraction")
    is_common = ex.get("is_common_essential", False)
    is_selective = ex.get("is_strongly_selective", False)
    sel_lineages = ex.get("selective_lineages") or []
    lineage_rows: list[dict] = ex.get("lineage_breakdown") or []

    if mean is None and n_dep is None:
        return ""

    dep_str = f"{n_dep}/{n_total}" if n_dep is not None and n_total else "—"
    frac_str = f" ({dep_frac:.1%})" if dep_frac is not None else ""

    status_parts = []
    if is_common:
        status_parts.append("**Common essential (pan-cancer)** — indiscriminate lethality risk")
    if is_selective:
        status_parts.append("Strongly selective")
    status_str = " · ".join(status_parts) if status_parts else "Context-dependent"

    score_row = f"| Mean Chronos score | {mean:.3f} |" if mean is not None else ""
    sd_row = f"| SD | {std:.3f} |" if std is not None else ""
    iqr_row = (
        f"| Q1 / Median / Q3 | {q1:.3f} / {median:.3f} / {q3:.3f} |"
        if q1 is not None and median is not None and q3 is not None
        else ""
    )
    dep_row = f"| Dependent cell lines | {dep_str}{frac_str} |"
    metrics_rows = "\n".join(r for r in [score_row, sd_row, iqr_row, dep_row] if r)
    metrics_table = f"| Metric | Value |\n| --- | --- |\n{metrics_rows}"

    lineage_section = ""
    if lineage_rows:
        top = sorted(
            lineage_rows,
            key=lambda r: (r.get("n_dependent", 0) / r["n_total"]) if r.get("n_total") else 0,
            reverse=True,
        )[:12]
        lin_rows = "\n".join(
            f"| {lr['lineage']} | {lr.get('n_dependent', 0)}/{lr.get('n_total', '?')} "
            f"| {lr.get('n_dependent', 0) / lr['n_total']:.0%} "
            f"| {lr.get('mean_effect', '—'):.3f} |"
            if lr.get("n_total")
            else f"| {lr['lineage']} | — | — | — |"
            for lr in top
        )
        lineage_section = (
            f"\n\n**Per-lineage breakdown (top {len(top)}):**\n\n"
            f"| Lineage | Dep./Total | Dep. % | Mean Chronos |\n"
            f"| --- | --- | --- | --- |\n"
            f"{lin_rows}"
        )

    sel_str = (
        f"\n\n**High-dependency lineages (≥90%):** {', '.join(sel_lineages)}"
        if sel_lineages
        else ""
    )

    source_link = str(getattr(depmap_row, "source_link", "") or "")
    header_link = f"[DepMap]({source_link})" if source_link else "DepMap"

    return (
        f"## CRISPR Dependency ({header_link})\n\n"
        f"**Status:** {status_str}\n\n"
        f"{metrics_table}"
        f"{sel_str}"
        f"{lineage_section}"
    )


def _chemistry_section(druggability_rows: list[Evidence]) -> str:
    """Render a chemistry signal block from ChEMBL-sourced DRUGGABILITY evidence."""
    chembl_row: Evidence | None = None
    for row in druggability_rows:
        if str(getattr(row, "source", "")).startswith("chembl:"):
            chembl_row = row
            break
    if chembl_row is None:
        return ""

    ex = row_extra(chembl_row)
    # Scalar signals
    num_bio = ex.get("num_bioactivities", 0)
    num_q = ex.get("num_quantitative", 0)
    num_actives = ex.get("num_actives", 0)
    num_potent = ex.get("num_potent", 0)
    num_hpotent = ex.get("num_highly_potent", 0)
    median_pc = ex.get("median_pchembl")
    max_phase = ex.get("max_phase")
    moas = ex.get("mechanisms_of_action") or []
    act_counts: dict = ex.get("activity_type_counts") or {}
    assay_counts: dict = ex.get("assay_type_counts") or {}
    candidates: list[dict] = ex.get("clinical_candidates") or []

    phase_str = f"{max_phase:.0f}" if max_phase is not None else "none"
    header = f"**Max clinical phase:** {phase_str}"
    if moas:
        header += f" · **MoA:** {'; '.join(moas[:3])}"

    def pct(n: float) -> str:
        return f"{n / num_q:.0%}" if num_q else "—"

    sample_note = f" (top-{num_q} sample)" if num_q >= 1000 else ""

    metrics = [
        ("Total bioactivities", f"{num_bio:,}"),
        (f"Quantitative (pChEMBL){sample_note}", f"{num_q:,}"),
        ("Actives ≤1 µM", f"{num_actives:,} ({pct(num_actives)})"),
        ("Potent ≤100 nM", f"{num_potent:,} ({pct(num_potent)})"),
        ("Highly potent ≤10 nM", f"{num_hpotent:,} ({pct(num_hpotent)})"),
        ("Median pChEMBL", f"{median_pc:.1f}" if median_pc is not None else "—"),
    ]
    table_rows = "\n".join(f"| {label} | {val} |" for label, val in metrics)
    metrics_table = f"| Metric | Value |\n| --- | --- |\n{table_rows}"

    act_str = ", ".join(f"{k} ({v:,})" for k, v in list(act_counts.items())[:6])
    assay_str = ", ".join(f"{k} ({v:,})" for k, v in list(assay_counts.items())[:4])

    clin_section = ""
    if candidates:
        cand_rows = "\n".join(
            f"| [{c['molecule_chembl_id']}](https://www.ebi.ac.uk/chembl/compound_report_card/{c['molecule_chembl_id']}/) "
            f"| {c.get('pref_name') or '—'} | Phase {c['max_phase']:.0f} |"
            for c in candidates
        )
        clin_section = (
            f"\n\n**Clinical candidates ({len(candidates)}):**\n\n"
            f"| ChEMBL ID | Name | Phase |\n| --- | --- | --- |\n{cand_rows}"
        )

    return (
        f"## Chemistry Signal (ChEMBL)\n\n"
        f"{header}\n\n"
        f"{metrics_table}\n\n"
        f"**Activity types:** {act_str or '—'}  \n"
        f"**Assay types:** {assay_str or '—'}"
        f"{clin_section}"
    )


def _ot_tractability_section(ot: dict) -> str:
    """Render Open Targets tractability + mouse phenotype block for the biology lens."""
    parts: list[str] = []

    tract_sm = ot.get("tractability_small_molecule", False)
    tract_ab = ot.get("tractability_antibody", False)
    tract_other: list[str] = ot.get("tractability_other") or []
    tract_link = ot.get("tract_source_link", "")

    if tract_sm or tract_ab or tract_other:
        header = "## Open Targets Tractability"
        if tract_link:
            header = f"## Open Targets Tractability ([source]({tract_link}))"
        modalities: list[str] = []
        if tract_sm:
            modalities.append("**Small molecule** ✓")
        if tract_ab:
            modalities.append("**Antibody** ✓")
        modalities.extend(tract_other)
        parts.append(header + "\n\n" + " · ".join(modalities))

    phenotype_labels: list[str] = ot.get("mouse_phenotype_labels") or []
    phenotypes: list[dict] = ot.get("mouse_phenotypes") or []
    if phenotype_labels:
        pheno_rows = "\n".join(
            f"| {p.get('phenotype_label', '')} | "
            f"{', '.join(c['label'] for c in (p.get('phenotype_classes') or [])[:3])} |"
            for p in phenotypes[:15]
            if p.get("phenotype_label")
        )
        pheno_table = (
            f"| Phenotype | Class(es) |\n| --- | --- |\n{pheno_rows}"
            if pheno_rows
            else "_No phenotype detail available._"
        )
        parts.append(
            f"## Mouse KO Phenotypes (Open Targets / MGI·IMPC)\n\n"
            f"{len(phenotype_labels)} phenotype(s) observed in mouse orthologue knock-out models.\n\n"
            f"{pheno_table}"
        )

    return ("\n\n---\n\n".join(parts) + "\n\n---\n\n") if parts else ""


def _ot_safety_section(ot: dict) -> str:
    """Render Open Targets safety liabilities + mouse phenotype block for the safety lens."""
    parts: list[str] = []

    liabilities: list[dict] = ot.get("safety_liabilities") or []
    if liabilities:
        rows = "\n".join(
            f"| {li.get('event', '—')} | "
            f"{li.get('datasource', '—')} | "
            f"{', '.join(e.get('direction', '') for e in (li.get('effects') or [])[:2]) or '—'} |"
            for li in liabilities[:20]
        )
        table = (
            f"| Adverse Event | Source | Direction |\n| --- | --- | --- |\n{rows}"
            if rows
            else "_No liability details available._"
        )
        parts.append(
            f"## Open Targets Safety Liabilities\n\n"
            f"{len(liabilities)} curated adverse event/toxicity signal(s).\n\n"
            f"{table}"
        )

    phenotype_labels: list[str] = ot.get("mouse_phenotype_labels") or []
    phenotypes: list[dict] = ot.get("mouse_phenotypes") or []
    if phenotype_labels:
        pheno_rows = "\n".join(
            f"| {p.get('phenotype_label', '')} | "
            f"{', '.join(c['label'] for c in (p.get('phenotype_classes') or [])[:3])} |"
            for p in phenotypes[:15]
            if p.get("phenotype_label")
        )
        pheno_table = (
            f"| Phenotype | Class(es) |\n| --- | --- |\n{pheno_rows}"
            if pheno_rows
            else "_No phenotype detail available._"
        )
        parts.append(
            f"## Mouse KO Phenotypes (Open Targets / MGI·IMPC)\n\n"
            f"{len(phenotype_labels)} phenotype(s) observed in mouse orthologue knock-out models.\n\n"
            f"{pheno_table}"
        )

    return ("\n\n---\n\n".join(parts) + "\n\n---\n\n") if parts else ""


def _ot_known_drugs_section(ot: dict) -> str:
    """Render Open Targets known drugs block for the commercial lens."""
    drugs: list[dict] = ot.get("known_drugs") or []
    total = ot.get("known_drugs_count", len(drugs))
    if not drugs and not total:
        return ""

    approved = [d for d in drugs if d.get("is_approved")]
    phase3 = [d for d in drugs if not d.get("is_approved") and (d.get("max_phase") or 0) >= 3]

    def _drug_status(d: dict) -> str:
        return "Approved" if d.get("is_approved") else f"Phase {int(d.get('max_phase') or 0)}"

    rows = "\n".join(
        f"| {d.get('drug_name', '—')} | "
        f"{_drug_status(d)} | "
        f"{d.get('mechanism_of_action', '—')} | "
        f"{d.get('disease_name', '—')} |"
        for d in drugs[:20]
        if d.get("drug_name")
    )
    table = (
        f"| Drug | Status | Mechanism | Indication |\n| --- | --- | --- | --- |\n{rows}"
        if rows
        else "_No drug details available._"
    )

    ot_link = ot.get("assoc_source_link", "")
    header = "## Open Targets Known Drugs"
    if ot_link:
        base = ot_link.split("/associations")[0]
        header = f"## Open Targets Known Drugs ([source]({base}))"

    summary = (
        f"{total} drug-indication pair(s) total — "
        f"**{len(approved)} approved**, {len(phase3)} in Phase 3."
    )

    return f"{header}\n\n{summary}\n\n{table}\n\n---\n\n"


def _render(
    v: LensVerdict,
    evidence_rows: list[Evidence],
    claims: list[CoreClaim],
    ot_extra: dict | None = None,
    quality_map: dict | None = None,
) -> str:
    ot_extra = ot_extra or {}
    quality_map = quality_map or {}
    label = _VERDICT_LABEL.get(v.overall_verdict, v.overall_verdict)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    direction = v.direction.value if hasattr(v.direction, "value") else str(v.direction)

    lens_types = LENS_EVIDENCE_TYPES.get(v.lens, ())
    # Claims this lens reasoned over — same predicate as input routing, so the report
    # cites exactly what the lens consumed (structured by type; literature by topic).
    relevant_claims = [c for c in claims if claim_matches_lens(c, v.lens)]
    # Evidence rows: structured rows by type, plus the literature documents that
    # actually sourced this lens's topic-routed claims (literature rows carry no
    # topics themselves, so resolve them via the claims' source_evidence_id).
    relevant_src_ids = {
        c.source_evidence_id
        for c in relevant_claims
        if getattr(c, "source_evidence_id", None) is not None
    }
    relevant_ev = [
        e
        for e in evidence_rows
        if getattr(e, "evidence_type", None) in lens_types
        or getattr(e, "evidence_id", None) in relevant_src_ids
    ]

    ordered_ev, num_by_evid = _build_citation_index(relevant_ev, quality_map)
    # claim.evidence_id (str) → citation number of the document it came from
    claim_source_num: dict[str, int] = {}
    for c in relevant_claims:
        src = getattr(c, "source_evidence_id", None)
        if src is not None and src in num_by_evid:
            claim_source_num[str(c.evidence_id)] = num_by_evid[src]

    # For the genetics lens, prepend an actionable direction headline when a
    # non-ambiguous mechanism direction is available from floor_signals.
    genetics_headline = ""
    if v.lens == "genetics":
        direction_val = v.direction.value if hasattr(v.direction, "value") else str(v.direction)
        if direction_val not in ("unspecified", "modulate", ""):
            action_verb = "INHIBIT" if direction_val == "inhibit" else "ACTIVATE / RESTORE"
            genetics_headline = (
                f"> **Genetics-derived therapeutic direction: {action_verb} this target.**\n\n"
            )

    narrative_section = (
        f"## Analysis\n\n{genetics_headline}{v.narrative}\n\n"
        if v.narrative
        else (f"## Analysis\n\n{genetics_headline}\n\n" if genetics_headline else "")
    )

    druggability_rows = [
        e for e in ordered_ev if getattr(e, "evidence_type", None) == EvidenceType.DRUGGABILITY
    ]
    functional_rows = [
        e
        for e in ordered_ev
        if getattr(e, "evidence_type", None) == EvidenceType.FUNCTIONAL_GENOMICS
    ]
    chemistry_block = _chemistry_section(druggability_rows)
    depmap_block = _depmap_section(functional_rows)
    chemistry_section = f"{chemistry_block}\n\n---\n\n" if chemistry_block else ""
    depmap_section = f"{depmap_block}\n\n---\n\n" if depmap_block else ""

    # Lens-specific Open Targets structured data blocks
    if v.lens == "biology":
        ot_section = _ot_tractability_section(ot_extra)
    elif v.lens == "safety":
        ot_section = _ot_safety_section(ot_extra)
    elif v.lens == "commercial":
        ot_section = _ot_known_drugs_section(ot_extra)
    else:
        ot_section = ""

    # B4: Banner when 0 claims were extracted but source evidence exists.
    # Distinguishes "genuine evidence gap" from "extraction failure" for reviewers.
    if not relevant_claims and ordered_ev:
        extracted_section = (
            f"_Structured-claim extraction returned 0 atomic claims for this lens. "
            f"{len(ordered_ev)} source record(s) were reasoned over directly — "
            "see Langfuse span `extraction.dropped_structured` for details._"
        )
    else:
        extracted_section = _claims_section(relevant_claims, claim_source_num)

    return f"""# {v.lens.title()} Lens — {v.target_gene} × {v.disease}

**Verdict:** {label} · **Confidence:** {v.confidence:.0%}
**Therapeutic direction:** {direction}
**Evidence considered:** {len(ordered_ev)} source(s) · {len(relevant_claims)} claim(s)
**Generated:** {now}

---

## Summary

> {v.rationale or "_No rationale provided._"}

---

{narrative_section}{depmap_section}{chemistry_section}{ot_section}## Axis Breakdown

{_axis_table(v, claim_source_num, num_by_evid)}

---

## Evidence Considered

{_evidence_section(ordered_ev, num_by_evid, quality_map)}

---

## Extracted Claims

{extracted_section}

---

*Intermediate lens report. Numbered sources above are the original records this
lens reasoned over. See [report.md](../../report.md) for cross-lens synthesis and
[full_report.md](../../full_report.md) for the complete linked evidence dossier.*
"""
