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

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, claim_matches_lens
from agents.interpretation._lens_confidence import contradicts_axes
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
from agents.synthesis.report.overrides import overrides_block
from agents.synthesis.report.provenance import (
    Provenance,
    lens_stages,
    run_info_md,
    version_line,
)
from core.persistence.artifact_store import _safe_id
from mcp_servers.opentargets.tools import unique_drug_counts
from schemas.evidence import CoreClaim, Evidence, EvidenceType
from schemas.verdicts import LensVerdict
from services.evidence.claim_effect import effect_label
from services.evidence.guard_rules import GUARD_RULES
from services.evidence.liabilities import target_safety

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
    ot_extra: dict[str, Any] | None = None,
    quality_map: dict[str, Any] | None = None,
    provenance: Provenance | None = None,
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
    ``provenance`` fills the closing Run Information section (build, per-step models).
    """
    try:
        lens_dir = (
            _REPORT_ROOT
            / verdict.target_gene
            / _safe_id(disease_id)
            / verdict.direction.value
            / "lenses"
        )
        lens_dir.mkdir(parents=True, exist_ok=True)
        path = lens_dir / f"{verdict.lens}.md"
        path.write_text(
            _render(
                verdict,
                evidence_rows or [],
                claims or [],
                ot_extra or {},
                quality_map or {},
                provenance,
            ),
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


def _sort_key(row: Any, quality_map: dict[str, Any]) -> tuple[Any, ...]:
    # Literature sorts highest-quality-first then most-recent-first — same ordering
    # as report.md's `_lit_sort_key` in agent.py — so citation numbers and reading
    # order agree across the dossier and the per-lens reports.
    if is_literature(row):
        q = quality_map.get(str(getattr(row, "evidence_id", "")))
        return (0, -quality_rank(q), -_pub_year_int(row))
    return (1, row_type(row), str(getattr(row, "source", "")))


def _build_citation_index(
    evidence_rows: list[Evidence],
    quality_map: dict[str, Any] | None = None,
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
    exclude: set[int] | None = None,
) -> str:
    nums = _axis_source_nums(claim_ids, claim_source_num, num_by_evid) - (exclude or set())
    return ", ".join(f"[{n}]" for n in sorted(nums)) if nums else "—"


def _axis_source_nums(
    claim_ids: list[str],
    claim_source_num: dict[str, int],
    num_by_evid: dict[UUID, int],
) -> set[int]:
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
    return nums


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
        added = set(ax.added_claim_ids)
        cited_ids = [i for i in ax.supporting_claim_ids if i not in added]
        sources = _axis_sources(cited_ids, claim_source_num, num_by_evid)
        if added:
            # Added after the verdict: structured records by config, literature by the
            # citation step — kept apart from what the lens model cited itself.
            # A source the model already cited is not listed again as added.
            shown = _axis_source_nums(cited_ids, claim_source_num, num_by_evid)
            extra = _axis_sources(ax.added_claim_ids, claim_source_num, num_by_evid, shown)
            if extra != "—":
                sources = f"{sources} · added: {extra}" if sources != "—" else f"added: {extra}"
        lines.append(f"| {ax_name} | {ax_label} | {ax.confidence:.0%} | {rationale} | {sources} |")
    return "\n".join(lines)


def _literature_table(
    evidence: list[Evidence],
    num_by_evid: dict[UUID, int],
    quality_map: dict[str, Any],
) -> str:
    lines = [
        "| # | Source | Detail | Quality | Year | First Author |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in evidence:
        n = num_by_evid.get(row.evidence_id, 0)
        quality = quality_stars(quality_map.get(str(row.evidence_id)))
        lines.append(
            f"| {n} | {cite(row)} | {evidence_detail(row) or '—'} "
            f"| {quality} | {pub_year(row)} | {first_author(row)} |"
        )
    return "\n".join(lines)


def _empirical_table(
    evidence: list[Evidence],
    num_by_evid: dict[UUID, int],
    quality_map: dict[str, Any],
) -> str:
    lines = [
        "| # | Source | Type | Detail | Quality |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in evidence:
        n = num_by_evid.get(row.evidence_id, 0)
        group = _TYPE_GROUP.get(row_type(row), row_type(row))
        quality = quality_stars(quality_map.get(str(row.evidence_id)))
        lines.append(f"| {n} | {cite(row)} | {group} | {evidence_detail(row) or '—'} | {quality} |")
    return "\n".join(lines)


def _evidence_section(
    evidence: list[Evidence],
    num_by_evid: dict[UUID, int],
    quality_map: dict[str, Any] | None = None,
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


def coverage_line(v: LensVerdict) -> str:
    """How much of what was routed to the lens it was shown, per evidence kind."""
    if not v.routed_inputs:
        return ""
    kinds = ", ".join(
        f"{kind} {v.shown_inputs.get(kind, 0):,}/{routed:,}"
        for kind, routed in v.routed_inputs.items()
    )
    shown = sum(v.shown_inputs.values())
    routed = sum(v.routed_inputs.values())
    return f"**Shown to the model:** {shown:,} of {routed:,} claims ({kinds})\n"


def _claims_section(
    claims: list[CoreClaim],
    claim_source_num: dict[str, int],
) -> str:
    if not claims:
        return "_No atomic claims were extracted for this lens._"
    lines = [
        "| Source | Claim | Effect | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    ranked_claims = sorted(
        claims, key=lambda c: c.confidence if c.confidence is not None else -1.0, reverse=True
    )
    for c in ranked_claims:
        src_num = claim_source_num.get(str(c.evidence_id))
        src = f"[{src_num}]" if src_num else "—"
        text = (c.claim_text or "").replace("|", "\\|").replace("\n", " ").strip()
        effect = effect_label(c) or "—"
        conf = f"{c.confidence:.0%}" if c.confidence is not None else "—"
        lines.append(f"| {src} | {text} | {effect} | {conf} |")
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
    lineage_rows: list[dict[str, Any]] = ex.get("lineage_breakdown") or []

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
    act_counts: dict[str, int] = ex.get("activity_type_counts") or {}
    assay_counts: dict[str, int] = ex.get("assay_type_counts") or {}
    candidates: list[dict[str, Any]] = ex.get("clinical_candidates") or []

    phase_str = f"{max_phase:.0f}" if max_phase is not None else "none"
    header = f"**Max clinical phase:** {phase_str}"
    if moas:
        header += f" · **MoA:** {'; '.join(moas[:3])}"

    def pct(n: float) -> str:
        return f"{n / num_q:.0%}" if num_q else "—"

    sample_note = f" (top-{num_q} sample)" if num_q >= 1000 else ""

    metrics = [
        (
            "Total bioactivities",
            f"≥ {num_bio:,}" if ex.get("num_bioactivities_is_lower_bound") else f"{num_bio:,}",
        ),
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


def _ot_tractability_section(ot: dict[str, Any]) -> str:
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
    phenotypes: list[dict[str, Any]] = ot.get("mouse_phenotypes") or []
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


def _ot_safety_section(ot: dict[str, Any]) -> str:
    """Render Open Targets safety liabilities + mouse phenotype block for the safety lens."""
    parts: list[str] = []

    safety = target_safety(ot)
    liabilities: list[dict[str, Any]] = safety["liabilities"]
    pharmacogenomic: list[dict[str, Any]] = safety["pharmacogenomic"]
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
    if pharmacogenomic:
        events = ", ".join(
            dict.fromkeys(str(li.get("event") or "—") for li in pharmacogenomic[:10])
        )
        parts.append(
            "## Open Targets Pharmacogenomic Records\n\n"
            f"{len(pharmacogenomic)} record(s) of variants changing a drug's response "
            f"({events}); not toxicities of acting on the target, so not counted as "
            "liabilities."
        )
    listed: list[dict[str, Any]] = safety["listed"]
    if listed:
        rows = "\n".join(
            f"| {li.get('event', '—')} | {li.get('datasource', '—')} | "
            f"{', '.join(e.get('direction', '') for e in (li.get('effects') or [])[:2]) or '—'} |"
            for li in listed[:20]
        )
        parts.append(
            "## Open Targets Safety Records Not Counted\n\n"
            f"{len(listed)} record(s) of in vitro assays (ToxCast), adverse-outcome pathways "
            "(AOP-Wiki) or naming no change of the target; listed, not counted as "
            "liabilities.\n\n"
            f"| Event | Source | Direction |\n| --- | --- | --- |\n{rows}"
        )

    phenotype_labels: list[str] = ot.get("mouse_phenotype_labels") or []
    phenotypes: list[dict[str, Any]] = ot.get("mouse_phenotypes") or []
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


def _ot_known_drugs_section(ot: dict[str, Any]) -> str:
    """Render Open Targets known drugs block for the commercial lens."""
    drugs: list[dict[str, Any]] = ot.get("known_drugs") or []
    total = ot.get("known_drugs_count", len(drugs))
    if not drugs and not total:
        return ""

    # Distinct drugs, not drug-indication rows: Open Targets lists a drug once per
    # indication, so counting rows reported "9 drug-indication pair(s) — 72 approved".
    counts = unique_drug_counts(drugs)
    total = counts["total"] or total

    def _drug_status(d: dict[str, Any]) -> str:
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

    shown = min(len(drugs), 20)
    summary = (
        f"{total} distinct drug(s) — **{counts['approved']} approved** (in at least one "
        f"indication), {counts['phase3']} more reaching Phase 3; {len(drugs)} "
        f"drug-indication row(s)" + (f", first {shown} shown." if len(drugs) > shown else ".")
    )

    return f"{header}\n\n{summary}\n\n{table}\n\n---\n\n"


_GUARD_FAMILY_LABEL = {
    "constraint": "gnomAD constraint wording",
    "mendelian_floor": "Mendelian floor (GWAS absence)",
    "commercial": "Commercial overstatement",
    "clinical_trial": "Trial phase / status",
    "trial_outcome": "Trial outcome per arm",
    "tissue_relevance": "Tissue relevance (bulk TPM)",
    "dependency_relevance": "Cell-line dependency relevance",
    "quantity": "Quantities vs. data bands",
}
_GUARD_STATUS_LABEL = {
    "passed": "passed",
    "flagged": "**flagged**",
    "not_evaluated": "not evaluated",
}


# Evidence bands for a directional lens confidence, on the rounded percentage shown.
# One top-weight source scores 50% (moderate); two of the same kind 75% (strong).
_EVIDENCE_BANDS = ((70, "strong"), (40, "moderate"), (0, "weak"))


def confidence_text(overall: str, confidence: float, axis_verdicts: list[bool | None]) -> str:
    """The lens confidence as shown to readers.

    The number is how well-sourced the verdict's direction is, not a probability split
    between support and oppose ("oppose 30%" is not "support 70%"), so it is shown with
    its evidence band. A neutral or insufficient-evidence verdict asserts no finding, so
    its number is a capped placeholder, not a degree of belief; show the axis tally
    instead.
    """
    if overall in ("support", "oppose"):
        pct = round(confidence * 100)
        band = next(name for floor, name in _EVIDENCE_BANDS if pct >= floor)
        return f"{band} evidence, {pct}%"
    if overall == "insufficient_evidence":
        return "— (insufficient evidence)"
    fav = sum(1 for v in axis_verdicts if v is True)
    unfav = sum(1 for v in axis_verdicts if v is False)
    unsure = sum(1 for v in axis_verdicts if v is None)
    tally = ", ".join(
        f"{n} {word}"
        for n, word in ((fav, "favourable"), (unfav, "unfavourable"), (unsure, "uncertain"))
        if n
    )
    return f"— (mixed: {tally})" if tally else "—"


_SEEN = {"this indication": "this indication", "other indications": "other indications only"}


def _findings_section(
    v: LensVerdict, claim_source_num: dict[str, int], num_by_evid: dict[UUID, int]
) -> str:
    """The safety lens's candidate liabilities with the status human data gave each.

    The verdict above is computed from these statuses by fixed rules, so this is the
    complete reasoning, not a summary of it.
    """
    if not v.liability_statuses:
        return ""

    def esc(text: str) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ")

    rows = [
        f"| {esc(r.get('system') or '—')} | {r.get('severity') or '—'} | "
        f"{_SEEN.get(str(r.get('seen') or ''), '—')} | "
        f"{str(r.get('status') or 'unresolved').replace('_', ' ')} | "
        f"{_axis_sources(list(r.get('claim_ids') or []), claim_source_num, num_by_evid)} | "
        f"{esc(r.get('note') or '—')} |"
        for r in v.liability_statuses
    ]
    return (
        "## Findings\n\n"
        "_The verdict is computed from these findings by fixed rules; nothing here was decided "
        "by the model's prose. A finding is open until people taking a requested-direction "
        "drug approved in this indication settle it._\n\n"
        "| Liability | Severity | Seen in people | Status | Sources | Note |\n"
        "| --- | --- | --- | --- | --- | --- |\n" + "\n".join(rows) + "\n\n---\n\n"
    )


def _decision_trace_section(
    v: LensVerdict, claim_source_num: dict[str, int], num_by_evid: dict[UUID, int]
) -> str:
    """For a split lens: each question it asked, the answer it got and the sources cited.

    The verdict above is computed from these answers by fixed rules, so this is the
    complete reasoning, not a summary of it. Citation numbers are the numbered sources
    below; claim ids the model cited without having been shown them are listed as dropped.
    """
    if not v.question_results:
        return ""

    parts = [
        "## Decision Trace\n",
        "_This lens asked narrow questions and computed its verdict from the answers by fixed "
        "rules; nothing below was decided by the model's prose._\n",
    ]
    for r in v.question_results:
        qid = str(r.get("question_id") or "")
        answer: dict[str, Any] = r.get("answer") or {}
        parts.append(f"### {qid.replace('_', ' ').title()}\n")
        parts.append(f"```json\n{json.dumps(answer, indent=2)}\n```\n")
        shown = len(r.get("shown_claim_ids") or [])
        attempts = r.get("attempts")
        meta = f"{shown} claim(s) shown · {attempts} attempt(s)"
        if r.get("dropped_claim_ids"):
            meta += f" · {len(r['dropped_claim_ids'])} citation(s) dropped (ids the model was not shown)"
        parts.append(f"_{meta}_\n")
        for note in r.get("notes") or []:
            parts.append(f"- Corrected in code: {note}")
        if r.get("notes"):
            parts.append("")
    return "\n".join(parts) + "\n---\n\n"


def _guard_checks_section(v: LensVerdict) -> str:
    """Which deterministic guards ran on this lens's text, and with what outcome.

    "Not evaluated" rows matter as much as flags: they show a check whose inputs were
    missing (e.g. DepMap and Project Score both unavailable) instead of letting the
    absence of a warning read as a pass.
    """
    repaired = [f for f in v.validation_flags if f.rule_id == "prose_repaired"]
    guard_rule_ids = {
        GUARD_RULES[c.family].flag_rule_id for c in v.guard_checks if c.family in GUARD_RULES
    }
    other = [
        f
        for f in v.validation_flags
        if f.severity in ("medium", "high")
        and f.rule_id not in guard_rule_ids
        and f.rule_id != "prose_repaired"
    ]
    if not v.guard_checks and not repaired and not other:
        return ""
    parts = ["## Automated Checks\n"]
    if v.guard_checks:
        rows = "\n".join(
            f"| {_GUARD_FAMILY_LABEL.get(c.family, c.family)} "
            f"| {_GUARD_STATUS_LABEL.get(c.status, c.status)} | {c.detail or '—'} |"
            for c in v.guard_checks
        )
        parts.append(f"| Check | Outcome | Detail |\n| --- | --- | --- |\n{rows}\n")
    if other:
        parts.append(
            "**Other flags:**\n\n"
            + "\n".join(f"- `{f.rule_id}` ({f.severity}): {f.message}" for f in other)
            + "\n"
        )
    if repaired:
        parts.append(
            "**Corrected to match the data** (original → as published):\n\n"
            + "\n".join(
                f"- “{f.claim_excerpt}” → {f.message.removeprefix('Rewritten to match the data: ')}"
                for f in repaired
            )
            + "\n"
        )
    return "\n".join(parts) + "\n---\n\n"


def _render(
    v: LensVerdict,
    evidence_rows: list[Evidence],
    claims: list[CoreClaim],
    ot_extra: dict[str, Any] | None = None,
    quality_map: dict[str, Any] | None = None,
    provenance: Provenance | None = None,
) -> str:
    ot_extra = ot_extra or {}
    quality_map = quality_map or {}
    label = _VERDICT_LABEL.get(v.overall_verdict, v.overall_verdict)
    now = datetime.now(UTC)
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

    # The direction-of-effect reading the lens reasoned with. The headline used to print
    # the *requested* direction as if genetics had derived it, and every request that
    # was not "inhibit" (degrade included) as "ACTIVATE / RESTORE".
    direction_headline = (
        f"> **Direction of effect:** {v.direction_of_effect}\n\n" if v.direction_of_effect else ""
    )

    direction_headline = (
        overrides_block([f.model_dump(mode="json") for f in v.validation_flags])
        + direction_headline
    )
    narrative_section = (
        f"## Analysis\n\n{direction_headline}{v.narrative}\n\n"
        if v.narrative
        else (f"## Analysis\n\n{direction_headline}\n\n" if direction_headline else "")
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

    model_stated = (
        f" (from cited evidence; model stated {v.model_confidence:.0%})"
        if v.model_confidence is not None
        else ""
    )
    # Judged on the final verdict: a flag raised on the model's verdict goes stale when a
    # rule sets the verdict from the axes.
    if contradicts_axes(v):
        model_stated += " — ⚠ the verdict contradicts every scored axis below"
    if v.model_verdict and v.model_verdict != v.overall_verdict:
        model_stated += (
            f"\n**Set by rule:** the lens model's verdict was "
            f"{v.model_verdict.replace('_', ' ')}; the summary and analysis below are its reading"
        )
    return f"""# {v.lens.title()} Lens — {v.target_gene} × {v.disease}

**Verdict:** {label} · **Strength:** {confidence_text(v.overall_verdict, v.confidence, [a.verdict for a in v.axes])}{model_stated}
**Therapeutic direction:** {direction}
**Evidence considered:** {len(ordered_ev)} source(s) · {len(relevant_claims)} claim(s)
{coverage_line(v)}{version_line()}

---

## Summary{" (the model's reading)" if v.model_verdict and v.model_verdict != v.overall_verdict else ""}

> {v.rationale or "_No rationale provided._"}

---

{narrative_section}{depmap_section}{chemistry_section}{ot_section}## Axis Breakdown

{_axis_table(v, claim_source_num, num_by_evid)}

---

{_findings_section(v, claim_source_num, num_by_evid)}{_decision_trace_section(v, claim_source_num, num_by_evid)}{_guard_checks_section(v)}## Evidence Considered

{_evidence_section(ordered_ev, num_by_evid, quality_map)}

---

## Extracted Claims

{extracted_section}

---

*Intermediate lens report. Numbered sources above are the original records this
lens reasoned over. See [report.md](../../report.md) for cross-lens synthesis and
[full_report.md](../../full_report.md) for the complete linked evidence dossier.*

---

{run_info_md(now, provenance, lens_stages(v.lens))}"""
