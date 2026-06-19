# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ReportAgent.

Renders the final gene-target validation dossier to markdown and persists the
artifact_uri in the reports table.

Input payload (dict):
  lens_verdicts      — list[dict] from the five LensAgents
  agreement_map      — dict | None from reconciler service
  experiment_results — list[dict] from ExperimentAgent
  critiques          — list[dict] from CriticAgent (all three passes)
  review_gaps        — list[dict] from ReviewerAgent
  evidence_summary   — list[dict] — {source, evidence_type, verdict}

Output payload (dict):
  artifact_uri — file:// path to the rendered dossier
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.synthesis.report.citations import (
    cite,
    collapse_by_url,
    esc,
    evidence_detail,
    first_author,
    is_opentargets,
    link,
    pub_year,
    quality_rank,
    quality_stars,
    row_extra,
    row_type,
)
from agents.synthesis.report.contract import CONTRACT
from core.persistence.artifact_store import _safe_direction, _safe_id, export_summary_csv
from core.persistence.db import get_session
from core.persistence.models import Report
from core.persistence.repos.evidence import EvidenceRepository
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage

_REPORT_ROOT = Path(os.getenv("RESULTS_DIR", "./results")) / "report"

_VERDICT_LABEL = {
    "support": "Support",
    "oppose": "Oppose",
    "neutral": "Neutral",
    "insufficient_evidence": "Insufficient evidence",
}

_VERDICT_EMOJI = {
    "support": "✅",
    "oppose": "❌",
    "neutral": "⚖️",
    "insufficient_evidence": "❓",
}

_LENS_ORDER = ["genetics", "biology", "clinical", "safety", "commercial", "regulatory"]

_LIT_CAP = 12  # max literature rows in report.md kept section; rest in full_report.md


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------


def _kept_evidence_section(kept_db_rows: list, quality_map: dict | None = None) -> str:
    """Render kept evidence grouped by type with clickable links.

    Structured evidence types are shown in full; literature is capped at
    _LIT_CAP (newest-first). A footer per capped group notes the remainder.
    """
    quality_map = quality_map or {}
    if not kept_db_rows:
        return "_No kept evidence available. See [full\\_report.md](./full_report.md)._\n"

    def _year(r: Any) -> int:
        try:
            return int(row_extra(r).get("pub_year") or 0)
        except (TypeError, ValueError):
            return 0

    _lit_types = {"article", "abstract", "conference", "book"}
    _type_display = {
        "expression": "Expression & Omics",
        "omics": "Expression & Omics",
        "constraint": "Constraint",
        "functional_genomics": "Functional Genomics",
        "druggability": "Druggability",
        "regulatory": "Regulatory",
        "patent": "Patents",
        "clinical_trial": "Clinical Trials",
    }
    _display_order = [
        "Expression & Omics",
        "Constraint",
        "Functional Genomics",
        "Druggability",
        "Regulatory",
        "Patents",
        "Clinical Trials",
    ]

    lit_rows: list = []
    ot_rows: list = []
    genetics_rows: list = []
    grouped: dict[str, list] = {}

    for r in kept_db_rows:
        rt = row_type(r)
        if is_opentargets(r):
            ot_rows.append(r)
        elif rt in _lit_types:
            lit_rows.append(r)
        elif rt == "genetics":
            genetics_rows.append(r)
        else:
            display = _type_display.get(rt, rt.replace("_", " ").title())
            grouped.setdefault(display, []).append(r)

    def _lit_sort_key(r: Any) -> tuple[int, int]:
        q = quality_map.get(str(getattr(r, "evidence_id", "")))
        return (quality_rank(q), _year(r))

    lit_rows = sorted(lit_rows, key=_lit_sort_key, reverse=True)

    def _mini(rows: list, cap: int | None = None) -> str:
        shown = rows[:cap] if cap else rows
        lines = ["| Source | Detail |", "| --- | --- |"]
        for citation, detail, _rep in collapse_by_url(shown):
            lines.append(f"| {citation} | {detail} |")
        text = "\n".join(lines)
        if cap and len(rows) > cap:
            text += f"\n\n_… and {len(rows) - cap} more — see [full\\_report.md](./full_report.md)_"
        return text

    def _lit_mini(rows: list, cap: int | None = None) -> str:
        shown = rows[:cap] if cap else rows
        lines = [
            "| Source | Detail | Quality | Year | First Author |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in shown:
            q = quality_map.get(str(getattr(r, "evidence_id", "")))
            lines.append(
                f"| {cite(r)} | {evidence_detail(r)} | {quality_stars(q)} "
                f"| {pub_year(r)} | {first_author(r)} |"
            )
        text = "\n".join(lines)
        if cap and len(rows) > cap:
            text += f"\n\n_… and {len(rows) - cap} more — see [full\\_report.md](./full_report.md)_"
        return text

    parts: list[str] = []
    if lit_rows:
        parts.append(f"### Literature ({len(lit_rows)})\n\n{_lit_mini(lit_rows, _LIT_CAP)}")
    if ot_rows:
        parts.append(f"### OpenTargets ({len(ot_rows)})\n\n{_mini(ot_rows)}")
    if genetics_rows:
        parts.append(f"### Genetics ({len(genetics_rows)})\n\n{_mini(genetics_rows)}")
    for display_name in _display_order:
        rows = grouped.get(display_name, [])
        if rows:
            parts.append(f"### {display_name} ({len(rows)})\n\n{_mini(rows)}")
    for display_name, rows in grouped.items():
        if display_name not in _display_order and rows:
            parts.append(f"### {display_name} ({len(rows)})\n\n{_mini(rows)}")

    return "\n\n".join(parts) + "\n"


def _evidence_table(
    evidence_summary: list[dict],
    kept_db_rows: list | None = None,
    quality_map: dict | None = None,
) -> str:
    if not evidence_summary:
        return "_No evidence collected._\n"

    kept = [e for e in evidence_summary if e.get("verdict") == "keep"]
    dropped = [e for e in evidence_summary if e.get("verdict") != "keep"]

    lines = [
        f"**{len(kept)} kept** / {len(dropped)} dropped (total: {len(evidence_summary)})\n",
    ]

    if kept:
        lines.append("### Kept Evidence\n")
        if kept_db_rows is not None:
            lines.append(_kept_evidence_section(kept_db_rows, quality_map))
        else:
            lines.append("| Source | Type | Rationale |")
            lines.append("| --- | --- | --- |")
            for e in kept[:50]:
                rationale = (e.get("screening_rationale") or "").replace("|", "\\|")
                lines.append(
                    f"| {e.get('source', '')} | {e.get('evidence_type', '')} | {rationale} |"
                )
            if len(kept) > 50:
                lines.append(f"_… and {len(kept) - 50} more kept items (see summary.csv)_")

    if dropped:
        lines.append(f"\n<details><summary>Dropped evidence ({len(dropped)} items)</summary>\n")
        lines.append("| Source | Type |")
        lines.append("| --- | --- |")
        for e in dropped[:100]:
            lines.append(f"| {e.get('source', '')} | {e.get('evidence_type', '')} |")
        if len(dropped) > 100:
            lines.append(f"_… and {len(dropped) - 100} more dropped items (see summary.csv)_")
        lines.append("\n</details>")

    return "\n".join(lines)


def _executive_summary(
    target_gene: str,
    disease: str,
    lens_verdicts: list[dict],
    agreement_map: dict | None,
    experiment_results: list[dict],
    gap_guidance: str,
) -> str:
    consensus = (agreement_map or {}).get("consensus_verdict", "insufficient_evidence")
    conf = (agreement_map or {}).get("consensus_confidence", 0.0)
    label = _VERDICT_LABEL.get(consensus, consensus)
    emoji = _VERDICT_EMOJI.get(consensus, "❓")
    scores = [r.get("score", 0) for r in experiment_results]
    avg_score = int(sum(scores) / len(scores)) if scores else 0

    lens_summary_lines = []
    # Sort by canonical order
    ordered = sorted(
        lens_verdicts,
        key=lambda lv: (
            _LENS_ORDER.index(lv.get("lens", "")) if lv.get("lens") in _LENS_ORDER else 99
        ),
    )
    for lv in ordered:
        lens = lv.get("lens", "?")
        ov = lv.get("overall_verdict", "insufficient_evidence")
        lc = lv.get("confidence", 0.0)
        ll = _VERDICT_LABEL.get(ov, ov)
        le = _VERDICT_EMOJI.get(ov, "❓")
        rationale = lv.get("rationale", "")
        lens_summary_lines.append(f"- **{lens.title()} lens** {le}: {ll} ({lc:.0%}) — {rationale}")

    lens_block = (
        "\n".join(lens_summary_lines) if lens_summary_lines else "_No lens verdicts available._"
    )

    gap_note = f"\n\n> **Evidence note:** {gap_guidance}" if gap_guidance else ""

    return f"""\
{emoji} **Overall consensus: {label}** (confidence {conf:.0%}) | **Suitability score: {avg_score}/100**

### Lens Summary

{lens_block}{gap_note}"""


def _discovery_section(lens_verdicts: list[dict]) -> str:
    if not lens_verdicts:
        return "_No lens analysis available._\n"

    ordered = sorted(
        lens_verdicts,
        key=lambda lv: (
            _LENS_ORDER.index(lv.get("lens", "")) if lv.get("lens") in _LENS_ORDER else 99
        ),
    )

    sections: list[str] = []
    for lv in ordered:
        lens = lv.get("lens", "?")
        ov = lv.get("overall_verdict", "insufficient_evidence")
        conf = lv.get("confidence", 0.0)
        label = _VERDICT_LABEL.get(ov, ov)
        emoji = _VERDICT_EMOJI.get(ov, "❓")
        rationale = lv.get("rationale", "")
        narrative = lv.get("narrative", "")
        axes = lv.get("axes") or []

        axes_lines = ["| Axis | Verdict | Confidence | Rationale |", "| --- | --- | --- |--- |"]
        for ax in axes:
            ax_name = ax.get("axis", "?").replace("_", " ").title()
            ax_verdict = ax.get("verdict")
            ax_conf = ax.get("confidence", 0.0)
            ax_label = (
                "Yes" if ax_verdict is True else ("No" if ax_verdict is False else "Uncertain")
            )
            ax_rat = (ax.get("rationale") or "").replace("|", "\\|")
            axes_lines.append(f"| {ax_name} | {ax_label} | {ax_conf:.0%} | {ax_rat} |")
            claim_ids = ax.get("supporting_claim_ids") or []
            if claim_ids:
                ids_str = ", ".join(f"`{cid[:8]}…`" for cid in claim_ids[:5])
                axes_lines.append(f"| | | | *Evidence: {ids_str}* |")
        axes_table = "\n".join(axes_lines)

        narrative_block = f"\n{narrative}\n" if narrative else ""

        sections.append(
            f"### {emoji} {lens.title()} Lens — {label} (confidence {conf:.0%})\n\n"
            f"> {rationale}\n"
            f"{narrative_block}\n"
            f"{axes_table}\n"
        )

    return "\n---\n\n".join(sections)


def _agreement_section(agreement_map: dict | None) -> str:
    if not agreement_map:
        return "_No agreement map available._\n"
    consensus = agreement_map.get("consensus_verdict", "insufficient_evidence")
    conf = agreement_map.get("consensus_confidence", 0.0)
    agreeing = agreement_map.get("agreeing_lenses") or []
    dissenting = agreement_map.get("dissenting_lenses") or []
    conflicts = agreement_map.get("conflicts") or []
    shared = agreement_map.get("shared_claim_conflicts") or []
    label = _VERDICT_LABEL.get(consensus, consensus)

    lines = [
        f"**Consensus verdict:** {label} (confidence {conf:.0%})",
        f"**Agreeing lenses:** {', '.join(agreeing) or 'none'}",
        f"**Dissenting lenses:** {', '.join(dissenting) or 'none'}",
    ]
    if conflicts:
        lines.append("")
        lines.append("**Conflicts (require human review):**")
        for c in conflicts:
            lines.append(f"- {c.get('description', str(c))}")
    if shared:
        lines.append("")
        lines.append(
            f"**Shared-claim conflicts:** {len(shared)} claim(s) cited by both supporting and opposing lenses."
        )
    return "\n".join(lines)


def _experiment_section(results: list[dict]) -> str:
    if not results:
        return "_No suitability scores computed._\n"
    lines = []
    for r in results:
        score = r.get("score", 0)
        target = r.get("target", "unknown")
        rationale = r.get("rationale", "")
        lines.append(f"**{target}** — Score: {score}/100\n> {rationale}")
    return "\n\n".join(lines)


def _gap_section(review_gaps: list[dict]) -> str:
    if not review_gaps:
        return "_No gap analysis available._\n"
    lines = []
    for g in review_gaps:
        stage = g.get("stage", "unknown")
        score = g.get("completeness_score", 0)
        aspects = g.get("missing_aspects", [])
        missing = "\n  ".join(f"- {a}" for a in aspects) if aspects else "  - None identified"
        lines.append(f"**{stage.title()}** (completeness {score}%)\n  {missing}")
    return "\n\n".join(lines)


def _recommendations(
    agreement_map: dict | None,
    experiment_results: list[dict],
    lens_verdicts: list[dict],
) -> str:
    consensus = (agreement_map or {}).get("consensus_verdict") if agreement_map else None
    scores = [r.get("score", 0) for r in experiment_results]
    avg_score = sum(scores) / len(scores) if scores else 0

    if consensus == "support" or avg_score >= 75:
        verdict = "**PROCEED** — Target shows strong validation across multiple lenses."
    elif consensus == "oppose":
        verdict = "**DEPRIORITISE** — Multiple lenses oppose this target."
    elif avg_score >= 50:
        verdict = "**CONDITIONAL** — Target is promising but evidence gaps or conflicts must be addressed."
    else:
        verdict = "**DEPRIORITISE** — Current evidence does not support advancement."

    # Supporting statements from lenses with actual verdicts
    support_lines: list[str] = []
    oppose_lines: list[str] = []
    for lv in lens_verdicts:
        lens = lv.get("lens", "?")
        ov = lv.get("overall_verdict", "insufficient_evidence")
        rat = lv.get("rationale", "")
        if ov == "support" and rat:
            support_lines.append(f"- **{lens.title()}**: {rat}")
        elif ov == "oppose" and rat:
            oppose_lines.append(f"- **{lens.title()}**: {rat}")

    supporting_block = ""
    if support_lines:
        supporting_block += "\n\n**Supporting evidence:**\n" + "\n".join(support_lines)
    if oppose_lines:
        supporting_block += "\n\n**Opposing evidence:**\n" + "\n".join(oppose_lines)

    conflicts = (agreement_map or {}).get("conflicts") or []
    conflict_text = (
        f"\n\n**Noted conflicts:** {'; '.join(c.get('description', '') for c in conflicts[:3])}."
        if conflicts
        else ""
    )
    return f"{verdict}{supporting_block}{conflict_text}"


def render_report(
    target_gene: str,
    disease: str,
    lens_verdicts: list[dict],
    agreement_map: dict | None,
    experiment_results: list[dict],
    critiques: list[dict],
    review_gaps: list[dict],
    evidence_summary: list[dict],
    generated_at: datetime,
    gap_guidance: str = "",
    kept_db_rows: list | None = None,
) -> str:
    kept_count = sum(1 for e in evidence_summary if e.get("verdict") == "keep")
    total_count = len(evidence_summary)
    quality_map = {c["evidence_id"]: c for c in critiques if c.get("evidence_id")}
    lens_uris: list[str] = [
        lv.get("lens_report_uri", "") for lv in lens_verdicts if lv.get("lens_report_uri")
    ]
    lens_links = (
        "**Intermediate lens reports:** " + " · ".join(f"[{Path(u).stem}]({u})" for u in lens_uris)
        if lens_uris
        else ""
    )

    return f"""# Gene Target Validation Dossier

**Target gene:** {target_gene}
**Disease:** {disease}
**Generated:** {generated_at.strftime("%Y-%m-%d %H:%M UTC")}
**Evidence:** {kept_count} kept / {total_count - kept_count} dropped

**Full evidence report:** [full_report.md](./full_report.md) — categorized kept sources with external links

{lens_links}

---

## Executive Summary

{_executive_summary(target_gene, disease, lens_verdicts, agreement_map, experiment_results, gap_guidance)}

---

## Evidence Summary

{_evidence_table(evidence_summary, kept_db_rows, quality_map)}

---

## Discovery — Analysis by Lens

{_discovery_section(lens_verdicts)}

---

## Cross-Lens Agreement

{_agreement_section(agreement_map)}

---

## Target Suitability Score

{_experiment_section(experiment_results)}

---

## Gap Analysis

{_gap_section(review_gaps)}

---

## Recommendations

{_recommendations(agreement_map, experiment_results, lens_verdicts)}

---

## Source Quality Notes

{f"Quality assessments made on {len(critiques)} evidence sources." if critiques else "_No source quality assessments available._"}
"""


# ---------------------------------------------------------------------------
# Full report — categorized kept evidence with external links
# ---------------------------------------------------------------------------


# Citation/link primitives are shared with the per-lens report writer so the
# external-link logic stays in one place (see citations.py).
_esc = esc
_link = link
_row_type = row_type
_row_extra = row_extra
_is_opentargets = is_opentargets


def _kept_rows(evidence_rows: list) -> list:
    return [
        r
        for r in evidence_rows
        if (_row_extra(r).get("screening_verdict") or {}).get("verdict") == "keep"
    ]


def _literature_section(rows: list, quality_map: dict | None = None) -> str:
    quality_map = quality_map or {}

    def _year(r: Any) -> int:
        try:
            return int(_row_extra(r).get("pub_year") or 0)
        except (TypeError, ValueError):
            return 0

    def _sort_key(r: Any) -> tuple[int, int]:
        q = quality_map.get(str(getattr(r, "evidence_id", "")))
        return (quality_rank(q), _year(r))

    rows = sorted(rows, key=_sort_key, reverse=True)
    lines = [
        "| Paper | Journal | Year | First Author | Quality | Why kept |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        ex = _row_extra(r)
        title = _esc(ex.get("title") or r.source)
        paper = f"{_link(r.source, r.source_link)} — {title}"
        rationale = _esc((ex.get("screening_verdict") or {}).get("rationale"))
        q = quality_map.get(str(getattr(r, "evidence_id", "")))
        lines.append(
            f"| {paper} | {_esc(ex.get('journal'))} | {_esc(ex.get('pub_year'))} "
            f"| {first_author(r)} | {quality_stars(q)} | {rationale} |"
        )
    return "\n".join(lines)


def _patent_section(rows: list) -> str:
    lines = ["| Patent | Title | Assignee | Filing date |", "| --- | --- | --- | --- |"]
    for r in rows:
        ex = _row_extra(r)
        # source_link from the MCP server is already a Google Patents URL; fall back to one.
        url = (
            str(getattr(r, "source_link", "") or "").strip()
            or f"https://patents.google.com/patent/{r.source}"
        )
        lines.append(
            f"| {_link(r.source, url)} | {_esc(ex.get('title'))} "
            f"| {_esc(ex.get('assignee'))} | {_esc(ex.get('filing_date'))} |"
        )
    return "\n".join(lines)


def _trial_section(rows: list) -> str:
    lines = ["| Trial | Title | Phase | Status | Sponsor |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        ex = _row_extra(r)
        lines.append(
            f"| {_link(r.source, r.source_link)} | {_esc(ex.get('title'))} "
            f"| {_esc(ex.get('phase'))} | {_esc(ex.get('status'))} | {_esc(ex.get('sponsor'))} |"
        )
    return "\n".join(lines)


def _opentargets_section(rows: list) -> str:
    blocks: list[str] = []
    for r in rows:
        ex = _row_extra(r)
        links = []
        if ex.get("assoc_source_link"):
            links.append(f"[Association profile]({ex['assoc_source_link']})")
        if ex.get("tract_source_link"):
            links.append(f"[Tractability]({ex['tract_source_link']})")
        link_line = " · ".join(links) if links else _link(r.source, r.source_link)
        blocks.append(
            f"**{_esc(r.source)}** — {link_line}\n\n"
            "| Metric | Score |\n| --- | --- |\n"
            f"| Overall association | {ex.get('overall_score', '—')} |\n"
            f"| Genetic | {ex.get('genetic_score', '—')} |\n"
            f"| Literature | {ex.get('literature_score', '—')} |\n"
            f"| Known drugs | {ex.get('known_drugs_score', '—')} |\n"
            f"| Tractability (small molecule / antibody) "
            f"| {ex.get('tractability_small_molecule', '—')} / {ex.get('tractability_antibody', '—')} |"
        )
    return "\n\n".join(blocks)


def _generic_section(rows: list) -> str:
    lines = ["| Source | Type | Detail |", "| --- | --- | --- |"]
    for citation, detail, rep in collapse_by_url(rows):
        lines.append(f"| {citation} | {_row_type(rep)} | {detail} |")
    return "\n".join(lines)


def _service_links_section(kept_rows: list) -> str:
    seen: set[str] = set()
    items: list[str] = []

    def _add(label: str, url: Any) -> None:
        url = str(url or "").strip()
        if url and url not in seen:
            seen.add(url)
            items.append(f"- **{label}:** [{url}]({url})")

    for r in kept_rows:
        et = _row_type(r)
        ex = _row_extra(r)
        if _is_opentargets(r):
            _add("OpenTargets — association", ex.get("assoc_source_link"))
            _add("OpenTargets — tractability", ex.get("tract_source_link"))
        elif et == "constraint":
            _add("gnomAD — constraint", getattr(r, "source_link", ""))
        elif et == "expression":
            src = str(getattr(r, "source", "") or "").lower()
            if src.startswith("uniprot:"):
                _add("UniProt — subcellular location", getattr(r, "source_link", ""))
            else:
                _add("GTEx / HPA — expression", getattr(r, "source_link", ""))
        elif et == "functional_genomics":
            _add("DepMap — dependency", getattr(r, "source_link", ""))

    return "\n".join(items) if items else "_No external service reports among the kept evidence._"


def _full_section(title: str, rows: list, renderer) -> str:
    body = renderer(rows) if rows else "_None kept._"
    return f"## {title} ({len(rows)})\n\n{body}\n"


def render_full_report(
    target_gene: str,
    disease: str,
    disease_id: str,
    gene_id: str,
    evidence_rows: list,
    generated_at: datetime,
    critiques: list[dict] | None = None,
) -> str:
    """Render the detailed, link-rich companion dossier of kept evidence.

    Operates on the persisted ``EvidenceRow`` objects (system of record), so the
    artifact stays regenerable from Postgres. Only ``keep``-verdict rows appear.
    """
    quality_map = {c["evidence_id"]: c for c in (critiques or []) if c.get("evidence_id")}
    kept = _kept_rows(evidence_rows)
    groups: dict[str, list] = {}
    for r in kept:
        groups.setdefault(_row_type(r), []).append(r)

    genetics_all = groups.get("genetics", [])
    ot_rows = [r for r in genetics_all if _is_opentargets(r)]
    genetics_rows = [r for r in genetics_all if not _is_opentargets(r)]

    literature_rows = (
        groups.get("article", [])
        + groups.get("abstract", [])
        + groups.get("conference", [])
        + groups.get("book", [])
    )
    patent_rows = groups.get("patent", [])
    trial_rows = groups.get("clinical_trial", [])
    omics_rows = groups.get("omics", []) + groups.get("expression", [])
    functional_rows = groups.get("functional_genomics", [])
    constraint_rows = groups.get("constraint", [])
    regulatory_rows = groups.get("regulatory", [])
    druggability_rows = groups.get("druggability", [])

    sections = [
        _full_section(
            "Literature — Prioritized Papers",
            literature_rows,
            lambda rows: _literature_section(rows, quality_map),
        ),
        _full_section("Patents", patent_rows, _patent_section),
        _full_section("Clinical Trials", trial_rows, _trial_section),
        _full_section("Regulatory", regulatory_rows, _generic_section),
        _full_section("OpenTargets Associations", ot_rows, _opentargets_section),
        _full_section("Druggability", druggability_rows, _generic_section),
        _full_section("Genetics", genetics_rows, _generic_section),
        _full_section("Omics & Expression", omics_rows, _generic_section),
        _full_section("Functional Genomics", functional_rows, _generic_section),
        _full_section("Constraint", constraint_rows, _generic_section),
    ]
    body = "\n---\n\n".join(sections)

    return f"""# Full Evidence Report — {target_gene} / {disease}

**Target gene:** {target_gene}{f" (`{gene_id}`)" if gene_id else ""}
**Disease:** {disease}{f" (`{disease_id}`)" if disease_id else ""}
**Generated:** {generated_at.strftime("%Y-%m-%d %H:%M UTC")}
**Kept evidence:** {len(kept)} sources

This document lists every source that passed screening (verdict **keep**), grouped by type,
with links to the original record. See [report.md](./report.md) for the executive synthesis.

---

## Service Reports & External Resources

{_service_links_section(kept)}

---

{body}"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ReportAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        import logging as _logging

        _log = _logging.getLogger(__name__)

        spec = msg.task_spec or {}
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")
        disease_id = spec.get("disease_id") or ""
        direction = spec.get("direction") or "unspecified"

        data: dict[str, Any] = msg.payload if isinstance(msg.payload, dict) else {}

        lens_verdicts = data.get("lens_verdicts", [])
        agreement_map = data.get("agreement_map")
        experiment_results = data.get("experiment_results", [])
        critiques = data.get("critiques", [])
        review_gaps = data.get("review_gaps", [])
        evidence_summary = data.get("evidence_summary", [])
        gap_guidance = data.get("gap_guidance", "")

        now = datetime.now(UTC)
        report_dir = _REPORT_ROOT / target_gene / _safe_id(disease_id) / _safe_direction(direction)
        report_dir.mkdir(parents=True, exist_ok=True)

        async with get_session() as session:
            evidence_rows = await EvidenceRepository(session).get_by_run(msg.run_id)
            if not evidence_rows:
                _log.warning(
                    "report: no DB evidence rows for run_id=%s — summary.csv will be empty. "
                    "Check that _persist_evidence() succeeded in screening/extraction nodes.",
                    msg.run_id,
                )
            kept_rows = _kept_rows(evidence_rows)

            content = render_report(
                target_gene=target_gene,
                disease=disease,
                lens_verdicts=lens_verdicts,
                agreement_map=agreement_map,
                experiment_results=experiment_results,
                critiques=critiques,
                review_gaps=review_gaps,
                evidence_summary=evidence_summary,
                generated_at=now,
                gap_guidance=gap_guidance,
                kept_db_rows=kept_rows,
            )
            report_path = report_dir / "report.md"
            report_path.write_text(content, encoding="utf-8")
            artifact_uri = f"file://{report_path.resolve()}"

            export_summary_csv(target_gene, disease_id, direction, evidence_rows)

            full_content = render_full_report(
                target_gene=target_gene,
                disease=disease,
                disease_id=disease_id,
                gene_id=spec.get("gene_id") or "",
                evidence_rows=evidence_rows,
                generated_at=now,
                critiques=critiques,
            )
            full_report_path = report_dir / "full_report.md"
            full_report_path.write_text(full_content, encoding="utf-8")
            full_report_uri = f"file://{full_report_path.resolve()}"

            row = Report(
                id=uuid.uuid4(),
                run_id=msg.run_id,
                artifact_uri=artifact_uri,
                created_at=now,
            )
            session.add(row)

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"artifact_uri": artifact_uri, "full_report_uri": full_report_uri},
            trace_id=msg.trace_id,
        )
