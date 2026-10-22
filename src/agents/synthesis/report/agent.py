# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ReportAgent.

Renders the final gene-target validation dossier to markdown and persists the
artifact_uri in the reports table.

Input payload (dict):
  lens_verdicts      — list[dict] from the six LensAgents
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
from collections.abc import Callable
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
from agents.synthesis.report.lens_report import confidence_text
from agents.synthesis.report.overrides import overrides_block
from agents.synthesis.report.provenance import (
    Provenance,
    collect_provenance,
    run_info_md,
    version_line,
)
from core.degradations import render_degradations
from core.exceptions import describe_exception
from core.persistence.artifact_store import _safe_direction, _safe_id, export_summary_csv
from core.persistence.db import get_session
from core.persistence.models import Report
from core.persistence.repos.evidence import EvidenceRepository
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.drug_precedent import requested_effect
from services.evidence.guard_core import strip_annotations

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


def _kept_evidence_section(
    kept_db_rows: list[Any], quality_map: dict[str, Any] | None = None
) -> str:
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

    lit_rows: list[Any] = []
    ot_rows: list[Any] = []
    genetics_rows: list[Any] = []
    grouped: dict[str, list[Any]] = {}

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

    def _mini(rows: list[Any], cap: int | None = None) -> str:
        shown = rows[:cap] if cap else rows
        lines = ["| Source | Detail | Quality |", "| --- | --- | --- |"]
        for citation, detail, rep in collapse_by_url(shown):
            q = quality_map.get(str(getattr(rep, "evidence_id", "")))
            lines.append(f"| {citation} | {detail} | {quality_stars(q)} |")
        text = "\n".join(lines)
        if cap and len(rows) > cap:
            text += f"\n\n_… and {len(rows) - cap} more — see [full\\_report.md](./full_report.md)_"
        return text

    def _lit_mini(rows: list[Any], cap: int | None = None) -> str:
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

    empirical_parts: list[str] = []
    if ot_rows:
        empirical_parts.append(f"#### OpenTargets ({len(ot_rows)})\n\n{_mini(ot_rows)}")
    if genetics_rows:
        empirical_parts.append(f"#### Genetics ({len(genetics_rows)})\n\n{_mini(genetics_rows)}")
    for display_name in _display_order:
        rows = grouped.get(display_name, [])
        if rows:
            empirical_parts.append(f"#### {display_name} ({len(rows)})\n\n{_mini(rows)}")
    for display_name, rows in grouped.items():
        if display_name not in _display_order and rows:
            empirical_parts.append(f"#### {display_name} ({len(rows)})\n\n{_mini(rows)}")

    empirical_count = (
        len(ot_rows) + len(genetics_rows) + sum(len(rows) for rows in grouped.values())
    )
    if empirical_parts:
        parts.append(f"### Empirical ({empirical_count})\n\n" + "\n\n".join(empirical_parts))

    return "\n\n".join(parts) + "\n"


def _evidence_table(
    evidence_summary: list[dict[str, Any]],
    kept_db_rows: list[Any] | None = None,
    quality_map: dict[str, Any] | None = None,
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


_BAND_EMOJI = {
    "PROCEED": "✅",
    "PROCEED WITH CAUTION": "⚠️",
    "INVESTIGATE FURTHER": "🔍",
    "DEPRIORITISE": "❌",
}


def _lens_tally(lens_verdicts: list[dict[str, Any]]) -> str:
    """ "3 support · 2 oppose · 1 neutral" — how the lenses split, unweighted."""
    counts: dict[str, int] = {}
    for lv in lens_verdicts:
        ov = str(lv.get("overall_verdict") or "insufficient_evidence")
        counts[ov] = counts.get(ov, 0) + 1
    order = ("support", "oppose", "neutral", "insufficient_evidence")
    words = {"insufficient_evidence": "insufficient"}
    return " · ".join(f"{counts[v]} {words.get(v, v)}" for v in order if counts.get(v))


def _executive_summary(
    target_gene: str,
    disease: str,
    lens_verdicts: list[dict[str, Any]],
    agreement_map: dict[str, Any] | None,
    experiment_results: list[dict[str, Any]],
) -> str:
    consensus = (agreement_map or {}).get("consensus_verdict", "insufficient_evidence")
    conf = (agreement_map or {}).get("consensus_confidence", 0.0)
    label = _VERDICT_LABEL.get(consensus, consensus)
    emoji = _VERDICT_EMOJI.get(consensus, "❓")
    scores = [r.get("score", 0) for r in experiment_results]
    avg_score = int(sum(scores) / len(scores)) if scores else 0
    band = next((f" ({r['band']})" for r in experiment_results if r.get("band")), "")

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
        rationale = _summary_copy(lv.get("rationale", ""))
        shown = confidence_text(ov, lc, [a.get("verdict") for a in lv.get("axes") or []])
        # A rule set the verdict: the rationale is the model's reading of its own verdict.
        mv = lv.get("model_verdict")
        if mv and mv != ov:
            shown += f"; set by rule, model said {_VERDICT_LABEL.get(mv, mv).lower()}"
        lens_summary_lines.append(f"- **{lens.title()} lens** {le}: {ll} ({shown}) — {rationale}")

    lens_block = (
        "\n".join(lens_summary_lines) if lens_summary_lines else "_No lens verdicts available._"
    )

    tally = _lens_tally(lens_verdicts)
    if band:
        # The decision leads: the score carries the precedent and direction gates, while
        # the lens balance does not (three supporting lenses printed "Support" above a
        # DEPRIORITISE score for a target whose phase 3 programmes failed).
        name = band.strip(" ()")
        headline = (
            f"{_BAND_EMOJI.get(name, '❓')} **Recommendation: {name}** — suitability score "
            f"{avg_score}/100 | Lens balance: {label} ({tally})"
        )
    else:
        headline = (
            f"{emoji} **Lens balance: {label}** (confidence {conf:.0%}; {tally}) | "
            f"**Suitability score: {avg_score}/100**"
        )
    return f"""\
{headline}

### Lens Summary

{lens_block}"""


def _discovery_section(lens_verdicts: list[dict[str, Any]]) -> str:
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
            f"### {emoji} {lens.title()} Lens — {label} ("
            f"{confidence_text(lv.get('overall_verdict', ''), conf, [a.get('verdict') for a in lv.get('axes') or []])})\n\n"
            f"{overrides_block(lv.get('validation_flags'))}"
            f"> {rationale}\n"
            f"{narrative_block}\n"
            f"{axes_table}\n"
        )

    return "\n---\n\n".join(sections)


def _agreement_section(agreement_map: dict[str, Any] | None) -> str:
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
        f"**Lens balance** (lens verdicts weighted as in the score; not the decision): "
        f"{label} — {conf:.0%} of the voting lens weight agrees",
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


def _experiment_section(results: list[dict[str, Any]]) -> str:
    if not results:
        return "_No suitability scores computed._\n"
    lines = []
    for r in results:
        score = r.get("score", 0)
        target = r.get("target", "unknown")
        rationale = r.get("rationale", "")
        band = f" ({r['band']})" if r.get("band") else ""
        lines.append(f"**{target}** — Score: {score}/100{band}\n> {rationale}")
        if r.get("rationale_source") == "deterministic":
            lines.append(
                "_The model's explanation of the score could not be used; the summary above "
                "is generated from the score breakdown._"
            )
        bd = r.get("score_breakdown") or {}
        if bd.get("contributions"):
            rows = "\n".join(
                f"| {c['lens'].title()} | {_VERDICT_LABEL.get(c['verdict'], 'Not run')} | "
                f"{c['confidence']:.0%} | {c['weight']:.2f} | {c['points']:+.1f} |"
                for c in bd["contributions"]
            )
            notes = "".join(f"\n- {n}" for n in bd.get("notes") or [])
            lines.append(
                "_Computed deterministically from the lens verdicts and candidate liabilities "
                "(config/scoring.yaml): 50 + weighted signed lens confidences − liability "
                "penalty._\n\n"
                "| Lens | Verdict | Confidence | Weight | Points |\n|---|---|---|---|---|\n"
                f"{rows}\n\n"
                f"Evidence score {bd.get('evidence_score', 0):.1f} − liability penalty "
                f"{bd.get('liability_penalty', 0):.1f} → **{score}**{notes}"
            )
    return "\n\n".join(lines)


def _summary_copy(text: str) -> str:
    """A lens rationale as repeated in the summary sections: its guard notes, already
    printed under the lens's own section, replaced by one pointer — the same note was
    otherwise printed up to four times per report."""
    stripped = strip_annotations(text)
    if stripped == (text or "").strip():
        return stripped
    return f"{stripped} _(⚠ flagged by a prose check — see the lens section)_"


_PRECEDENT_ICON = {
    "validated": "✅",
    "opposed": "⛔",
    "failed": "❌",
    "stalled": "⚠️",
    "late_stage": "🔬",
    "in_clinic": "🔬",
    "untested": "❓",
    "undetermined": "❓",
}


_OUTCOME_LABEL = {
    "approved": "approved",
    "ongoing": "still in trials",
    "failed_efficacy": "failed — efficacy",
    "failed_hard_endpoint": "failed — hard endpoint or excess deaths",
    "failed_safety": "stopped — safety",
    "discontinued_business": "discontinued — business, enrolment or design",
    "positive_unfiled": "met a phase 3 endpoint, not approved",
    "stalled_unexplained": "reached phase 3, no result or reason on record",
    "early": "early stage",
}


def _programme_table(programme: dict[str, Any] | None) -> str:
    """One row per same-direction molecule: what happened to it and how much it counted
    against the mechanism (services/evidence/programme_outcomes.py)."""
    if not programme or not programme.get("available") or not programme.get("programmes"):
        return ""
    lines = [
        "",
        "**Programme outcomes** — each molecule with the requested effect, classified from "
        "its Open Targets clinical reports and registry results; the strength is how much the "
        "failed programmes falsify the mechanism (0–1) and replaces the flat failed/stalled "
        "caps in the score.",
        "",
        f"Falsification strength **{float(programme.get('falsification') or 0.0):.2f}**"
        + (
            f" — {_esc(str((programme.get('gate') or {}).get('reason') or ''))}"
            if programme.get("gate")
            else ""
        ),
        "",
        "| Molecule | Best stage | Outcome | Counted | Reason on record | Trials |",
        "|---|---|---|---|---|---|",
    ]
    for p in (programme.get("programmes") or [])[:12]:
        stage = float(p.get("stage") or 0)
        best = "approved" if stage >= 4 else (f"phase {stage:g}" if stage else "—")
        counted = float(p.get("contribution") or 0.0)
        outcome = _OUTCOME_LABEL.get(str(p.get("outcome")), str(p.get("outcome")))
        if p.get("outcome") in ("failed_efficacy", "failed_hard_endpoint"):
            # Whether the drug moved a pharmacodynamic marker of the target decides how
            # much its failure counts against the mechanism.
            outcome += f" (target engagement: {str(p.get('engagement') or '').replace('_', ' ')})"
        lines.append(
            f"| {_esc(str(p.get('drug') or '').lower())} | {best} | {outcome} | "
            f"{counted:.2f} | {_esc('; '.join(p.get('reasons') or [])[:200] or '—')} | "
            f"{_esc(', '.join((p.get('trials') or [])[:3]) or '—')} |"
        )
    return "\n".join(lines)


def _precedent_section(
    precedent: dict[str, Any] | None, programme: dict[str, Any] | None = None
) -> str:
    """What drugs acting on the target did in this indication, against the run's direction."""
    if not precedent:
        return "_Clinical precedent not computed for this run._"
    status = str(precedent.get("status") or "")
    lines = [
        "_Derived deterministically from Open Targets drug records for this indication and its "
        "subtypes: each drug's action type (inhibitor, agonist, degrader, …) is compared with the "
        "requested direction. It bounds the suitability score (config/scoring.yaml)._",
        "",
        f"{_PRECEDENT_ICON.get(status, '')} **{status.replace('_', ' ').upper()}** — "
        f"{_esc(str(precedent.get('summary') or ''))}",
    ]
    rows = [
        (d, label)
        for key, label in (
            ("concordant", "same as requested"),
            ("discordant", "opposite"),
            ("unclassified", "not stated"),
        )
        for d in (precedent.get(key) or [])[:8]
    ]
    if rows:
        lines += [
            "",
            "| Drug | Action | Direction | Best stage here | Indications | Stopped for cause |",
            "|---|---|---|---|---|---|",
        ]
        for d, label in rows:
            stage = float(d.get("stage") or 0)
            best = "approved" if d.get("approved") else (f"phase {stage:g}" if stage else "—")
            stops = "; ".join(d.get("stopped_for_cause") or []) or "—"
            lines.append(
                f"| {_esc(str(d.get('drug') or '').lower())} | "
                f"{_esc(str(d.get('action_type') or '—').lower())} | {label} | {best} | "
                f"{_esc(', '.join((d.get('indications') or [])[:3]))} | {_esc(stops[:300])} |"
            )
    return "\n".join(lines) + _programme_table(programme)


_PERTURBATION_LABEL = {"decrease": "lowering", "increase": "raising", "either": "either"}


def _liability_rows(items: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"| {_esc(str(li.get('system', '')))} | {li.get('severity', '')} | "
        f"{_PERTURBATION_LABEL.get(str(li.get('perturbation') or 'either'), 'either')} | "
        f"{_esc(str(li.get('source', '')))} | {_esc('; '.join(li.get('evidence') or []))} |"
        for li in items
    )


_LIABILITY_HEADER = "| System | Severity | Caused by | Source | Evidence |\n|---|---|---|---|---|\n"


def _liabilities_section(
    liabilities: list[dict[str, Any]] | None,
    opposite: list[dict[str, Any]] | None = None,
    *,
    assessed: bool = True,
    direction: str = "",
) -> str:
    """Candidate on-target liabilities from the gene's structured data, every run: those
    of the requested change of the target counted, those of the other change listed apart,
    and a line when no source covers the requested change."""
    if liabilities is None:
        return "_Liabilities not computed for this run._"
    intro = (
        "_Derived deterministically from curated Open Targets safety events (split by the "
        "change of the target that causes them), mouse knockout phenotypes, gnomAD "
        "constraint versus the therapeutic direction, cell-line essentiality, boxed "
        "warnings of drugs acting on the target and harms the literature reports when the "
        "target was lowered or raised. Each is a candidate to monitor, weighed in the "
        "safety lens; none is a finding of harm._"
    )
    parts = [intro]
    if not assessed:
        change = "raising" if requested_effect(direction) == "increase" else "lowering"
        parts.append(
            f"**Harms of {change} the target: not assessed** — no source in this run "
            "covers that change, and no one has been given a drug acting that way."
        )
    if liabilities:
        parts.append(_LIABILITY_HEADER + _liability_rows(liabilities))
    else:
        parts.append(
            "No candidate liability was found in these sources — absence of evidence, not "
            "evidence of safety."
        )
    if opposite:
        parts.append(
            "Seen with the opposite change (not counted):\n\n"
            + _LIABILITY_HEADER
            + _liability_rows(opposite)
        )
    return "\n\n".join(parts)


def synthesis_issues(
    experiment_results: list[dict[str, Any]], investigation_error: str
) -> list[str]:
    """Stages after the review that failed or fell back — the reviewer runs before them,
    so its coverage checks cannot see these."""
    issues: list[str] = []
    if any(r.get("rationale_source") == "deterministic" for r in experiment_results):
        issues.append(
            "**Score rationale**: the model's explanation could not be used; the summary is "
            "generated from the score breakdown."
        )
    if investigation_error:
        issues.append(
            f"**Investigation**: did not complete ({investigation_error}); no follow-up "
            "evidence was gathered for the open gaps and conflicts."
        )
    return issues


def _gap_section(review_gaps: list[dict[str, Any]], extra_issues: list[str] | None = None) -> str:
    """Deterministic coverage checks: open issues first, clean stages in one line.

    The scores measure whether each stage produced usable evidence and whether known
    checks failed — not whether the evidence is complete, so "100%" is never shown as
    "completeness". ``extra_issues`` are failures of stages that run after the review
    (see ``synthesis_issues``).
    """
    extra_issues = extra_issues or []
    if not review_gaps and not extra_issues:
        return "_No gap analysis available._\n"
    open_lines: list[str] = list(extra_issues)
    clean: list[str] = []
    for g in review_gaps:
        stage = str(g.get("stage", "unknown")).title()
        aspects = g.get("missing_aspects", [])
        if aspects:
            items = "\n".join(f"  - {a}" for a in aspects)
            open_lines.append(
                f"**{stage}** (coverage check {g.get('completeness_score', 0)}%)\n{items}"
            )
        else:
            clean.append(stage)
    parts = [
        "_Deterministic coverage checks — they detect missing or failed evidence and open "
        "guard flags, not every gap a reviewer would find._"
    ]
    parts += open_lines or ["No open issues found by these checks."]
    if clean:
        parts.append(f"No issue detected: {', '.join(clean)}.")
    return "\n\n".join(parts)


def _scope_section(scope: dict[str, Any] | None) -> str:
    """The names the run matched in text for the disease, by relation; each related name
    from another ontology carries the term id it came from."""
    if not scope:
        return ""

    def names(values: Any) -> str:
        return ", ".join(sorted(str(v) for v in values or [])) or "—"

    lines = [
        "\n## Disease Scope\n",
        "_Names matched in claims and papers. In scope counts as the disease; related as a "
        "broader or neighbouring condition. Trial and label endpoints are matched separately._",
        "",
        f"- **In scope:** {names([*(scope.get('names') or []), *(scope.get('abbreviations') or [])])}",
    ]
    if scope.get("descendant_names"):
        lines.append(f"- **Subtypes:** {len(scope['descendant_names'])} names")
    sources: dict[str, str] = scope.get("related_sources") or {}
    own_related = [
        n
        for n in [*(scope.get("related_names") or []), *(scope.get("related_abbreviations") or [])]
        if n not in sources
    ]
    lines.append(f"- **Related (Open Targets lineage):** {names(own_related)}")
    by_term: dict[str, list[str]] = {}
    for name, term in sources.items():
        by_term.setdefault(term or "unknown term", []).append(name)
    if by_term:
        lines.append("- **Related (parents of cross-referenced terms):**")
        lines += [f"  - `{term}`: {names(ns)}" for term, ns in sorted(by_term.items())]
    if scope.get("partial"):
        lines.append(
            "- _The scope is partial: subtype names were capped or an ontology lookup failed._"
        )
    return "\n".join(lines) + "\n\n---\n"


def _investigation_section(investigation_summary: str, investigation_error: str = "") -> str:
    """Empty when the Investigator was skipped or found nothing to add; a failure note when
    it ran but did not complete, so a missing section is never mistaken for a clean run."""
    if not investigation_summary and investigation_error:
        return (
            "\n## Investigation\n\n_The investigation did not complete "
            f"({investigation_error}); the open gaps and lens conflicts were not followed "
            "up._\n\n---\n"
        )
    if not investigation_summary:
        return ""
    return f"\n## Investigation\n\n{investigation_summary}\n\n---\n"


_BAND_TEXT: dict[str, str] = {
    "PROCEED": "Target shows strong validation across multiple lenses.",
    "PROCEED WITH CAUTION": (
        "Target is supported, but open evidence gaps or liabilities must be resolved "
        "before committing."
    ),
    "INVESTIGATE FURTHER": "Evidence is mixed or incomplete; resolve the gaps below first.",
    "DEPRIORITISE": "Current evidence does not support advancement.",
}


def _recommendations(
    agreement_map: dict[str, Any] | None,
    experiment_results: list[dict[str, Any]],
    lens_verdicts: list[dict[str, Any]],
    liabilities: list[dict[str, Any]] | None = None,
) -> str:
    consensus = (agreement_map or {}).get("consensus_verdict") if agreement_map else None
    scores = [r.get("score", 0) for r in experiment_results]
    avg_score = sum(scores) / len(scores) if scores else 0
    band = next((r["band"] for r in experiment_results if r.get("band")), "")

    if band:
        verdict = f"**{band}** — {_BAND_TEXT.get(band, '')}".rstrip(" —")
    elif consensus == "support" or avg_score >= 75:
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
        rat = _summary_copy(lv.get("rationale", ""))
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
    monitor = [li for li in liabilities or [] if li.get("severity") in ("high", "medium")]
    monitor_text = (
        "\n\n**Liabilities to monitor:** "
        + "; ".join(f"{li.get('system')} ({li.get('source')})" for li in monitor[:8])
        + "."
        if monitor
        else ""
    )
    return f"{verdict}{supporting_block}{conflict_text}{monitor_text}"


def _prose_checks_section(critiques: list[dict[str, Any]]) -> str:
    """The critic's prose-QA pass: sentences the lens repair step corrected and the
    medium/high flags still open on the published lens text."""
    # Latest pass: critiques accumulate (append reducer), and an earlier pass describes
    # lens text that has since been replaced.
    qa = next((c for c in reversed(critiques) if c.get("pass") == "prose_qa"), None)
    if not qa:
        return ""
    issues = qa.get("issues") or []
    lines = [
        "## Prose Checks",
        "",
        f"{qa.get('repaired', 0)} sentence(s) corrected to match the data; "
        f"{qa.get('open', 0)} flag(s) still open. Details are in each lens report.",
    ]
    for i in issues:
        if i.get("issue_type") == "repaired":
            lines.append(f"- **{i['lens']}** — corrected: “{str(i.get('original', ''))[:160]}”")
        else:
            lines.append(
                f"- **{i['lens']}** — open `{i.get('rule_id', '')}` ({i.get('severity', '')}): "
                f"{str(i.get('description', ''))[:160]}"
            )
    return "\n".join(lines) + "\n"


_DROP_REASONS = {
    "number_not_in_source": "a number not in the source",
    "example_identifier": "copied from the prompt's example",
    # Older runs, when the faithfulness check still changed claims.
    "contradicted": "contradicted by the source",
    "effect_unconfirmed": "kept without its effect (not confirmed by the source)",
}
_MAX_FLAGS_SHOWN = 5


def render_dropped_claims(dropped: list[dict[str, Any]] | None) -> str:
    """Dossier block counting the claims extraction left out, by reason, and apart from
    them the claims the faithfulness check flagged but kept, with up to five flags and
    their quotes; "" when none."""
    if not dropped:
        return ""
    flags = [d for d in dropped if d.get("reason") == "faithfulness_flag"]
    left_out = [d for d in dropped if d.get("reason") != "faithfulness_flag"]
    blocks: list[str] = []
    if left_out:
        counts: dict[str, int] = {}
        for d in left_out:
            counts[str(d.get("reason", ""))] = counts.get(str(d.get("reason", "")), 0) + 1
        lines = [
            f"- {_DROP_REASONS.get(reason, reason)}: {n}"
            for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        for d in [d for d in left_out if d.get("reason") == "contradicted"][:_MAX_FLAGS_SHOWN]:
            lines.append(
                f'  - {d.get("source", "")}: "{str(d.get("claim_text", ""))[:160]}" — the '
                f'source: "{str(d.get("quote", ""))[:200]}"'
            )
        blocks.append(f"**Claims left out:** {len(left_out)}\n\n" + "\n".join(lines) + "\n")
    if flags:
        lines = []
        for d in flags[:_MAX_FLAGS_SHOWN]:
            quote = str(d.get("quote", ""))[:200]
            lines.append(
                f'- {d.get("source", "")}: "{str(d.get("claim_text", ""))[:160]}"'
                + (f' — the source: "{quote}"' if quote else "")
            )
        blocks.append(
            f"**Claims flagged by the faithfulness check (kept unchanged):** {len(flags)}\n\n"
            + "\n".join(lines)
            + "\n"
        )
    return "\n".join(blocks)


def render_report(
    target_gene: str,
    disease: str,
    lens_verdicts: list[dict[str, Any]],
    agreement_map: dict[str, Any] | None,
    experiment_results: list[dict[str, Any]],
    critiques: list[dict[str, Any]],
    review_gaps: list[dict[str, Any]],
    evidence_summary: list[dict[str, Any]],
    generated_at: datetime,
    investigation_summary: str = "",
    kept_db_rows: list[Any] | None = None,
    provenance: Provenance | None = None,
    liabilities: list[dict[str, Any]] | None = None,
    investigation_error: str = "",
    precedent: dict[str, Any] | None = None,
    programme: dict[str, Any] | None = None,
    disease_scope: dict[str, Any] | None = None,
    degradations: list[dict[str, Any]] | None = None,
    dropped_claims: list[dict[str, Any]] | None = None,
    opposite_liabilities: list[dict[str, Any]] | None = None,
    liability_assessed: bool = True,
    direction: str = "",
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
**Evidence:** {kept_count} kept / {total_count - kept_count} dropped
{version_line()}

{render_degradations(degradations)}
{render_dropped_claims(dropped_claims)}
**Full evidence report:** [full_report.md](./full_report.md) — categorized kept sources with external links

{lens_links}

---

## Executive Summary

{_executive_summary(target_gene, disease, lens_verdicts, agreement_map, experiment_results)}

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

## Clinical Precedent for the Hypothesis

{_precedent_section(precedent, programme)}

---

## Key Liabilities

{_liabilities_section(liabilities, opposite_liabilities, assessed=liability_assessed, direction=direction)}

---

## Target Suitability Score

{_experiment_section(experiment_results)}

---

## Gap Analysis

{_gap_section(review_gaps, synthesis_issues(experiment_results, investigation_error))}

---
{_investigation_section(investigation_summary, investigation_error)}
## Recommendations

{_recommendations(agreement_map, experiment_results, lens_verdicts, liabilities)}

---

## Source Quality Notes

{f"Quality assessments made on {len(quality_map)} evidence sources." if quality_map else "_No source quality assessments available._"}

{_prose_checks_section(critiques)}

---
{_scope_section(disease_scope)}
{run_info_md(generated_at, provenance)}"""


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


def _kept_rows(evidence_rows: list[Any]) -> list[Any]:
    return [
        r
        for r in evidence_rows
        if (_row_extra(r).get("screening_verdict") or {}).get("verdict") == "keep"
    ]


def _literature_section(rows: list[Any], quality_map: dict[str, Any] | None = None) -> str:
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


def _patent_section(rows: list[Any], quality_map: dict[str, Any] | None = None) -> str:
    quality_map = quality_map or {}
    lines = [
        "| Patent | Title | Assignee | Filing date | Quality |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        ex = _row_extra(r)
        # source_link from the MCP server is already a Google Patents URL; fall back to one.
        url = (
            str(getattr(r, "source_link", "") or "").strip()
            or f"https://patents.google.com/patent/{r.source}"
        )
        q = quality_map.get(str(getattr(r, "evidence_id", "")))
        lines.append(
            f"| {_link(r.source, url)} | {_esc(ex.get('title'))} "
            f"| {_esc(ex.get('assignee'))} | {_esc(ex.get('filing_date'))} | {quality_stars(q)} |"
        )
    return "\n".join(lines)


def _trial_section(rows: list[Any], quality_map: dict[str, Any] | None = None) -> str:
    quality_map = quality_map or {}
    lines = [
        "| Trial | Title | Phase | Status | Sponsor | Quality |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        ex = _row_extra(r)
        q = quality_map.get(str(getattr(r, "evidence_id", "")))
        lines.append(
            f"| {_link(r.source, r.source_link)} | {_esc(ex.get('title'))} "
            f"| {_esc(ex.get('phase'))} | {_esc(ex.get('status'))} | {_esc(ex.get('sponsor'))} "
            f"| {quality_stars(q)} |"
        )
    return "\n".join(lines)


def _opentargets_section(rows: list[Any], quality_map: dict[str, Any] | None = None) -> str:
    quality_map = quality_map or {}
    blocks: list[str] = []
    for r in rows:
        ex = _row_extra(r)
        links = []
        if ex.get("assoc_source_link"):
            links.append(f"[Association profile]({ex['assoc_source_link']})")
        if ex.get("tract_source_link"):
            links.append(f"[Tractability]({ex['tract_source_link']})")
        link_line = " · ".join(links) if links else _link(r.source, r.source_link)
        q = quality_map.get(str(getattr(r, "evidence_id", "")))
        blocks.append(
            f"**{_esc(r.source)}** — {link_line}\n\n"
            "| Metric | Score |\n| --- | --- |\n"
            f"| Overall association | {ex.get('overall_score', '—')} |\n"
            f"| Genetic | {ex.get('genetic_score', '—')} |\n"
            f"| Literature | {ex.get('literature_score', '—')} |\n"
            f"| Known drugs | {ex.get('known_drugs_score', '—')} |\n"
            f"| Tractability (small molecule / antibody) "
            f"| {ex.get('tractability_small_molecule', '—')} / {ex.get('tractability_antibody', '—')} |\n"
            f"| Source quality | {quality_stars(q)} |"
        )
    return "\n\n".join(blocks)


def _generic_section(rows: list[Any], quality_map: dict[str, Any] | None = None) -> str:
    quality_map = quality_map or {}
    lines = ["| Source | Type | Detail | Quality |", "| --- | --- | --- | --- |"]
    for citation, detail, rep in collapse_by_url(rows):
        q = quality_map.get(str(getattr(rep, "evidence_id", "")))
        lines.append(f"| {citation} | {_row_type(rep)} | {detail} | {quality_stars(q)} |")
    return "\n".join(lines)


# functional_genomics rows come from several sources; label each by its own source.
_FUNCTIONAL_LABELS = {
    "depmap": "DepMap — dependency",
    "project_score": "Project Score — CRISPR fitness",
    "impc": "IMPC — knockout phenotypes",
}


def _service_links_section(kept_rows: list[Any]) -> str:
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
            src = str(getattr(r, "source", "") or "").split(":", 1)[0].lower()
            _add(_FUNCTIONAL_LABELS.get(src, "Functional genomics"), getattr(r, "source_link", ""))

    return "\n".join(items) if items else "_No external service reports among the kept evidence._"


def _full_section(
    title: str, rows: list[Any], renderer: Callable[[list[Any]], str], level: str = "##"
) -> str:
    body = renderer(rows) if rows else "_None kept._"
    return f"{level} {title} ({len(rows)})\n\n{body}\n"


def render_full_report(
    target_gene: str,
    disease: str,
    disease_id: str,
    gene_id: str,
    evidence_rows: list[Any],
    generated_at: datetime,
    critiques: list[dict[str, Any]] | None = None,
    provenance: Provenance | None = None,
) -> str:
    """Render the detailed, link-rich companion dossier of kept evidence.

    Operates on the persisted ``EvidenceRow`` objects (system of record), so the
    artifact stays regenerable from Postgres. Only ``keep``-verdict rows appear.
    """
    quality_map = {c["evidence_id"]: c for c in (critiques or []) if c.get("evidence_id")}
    kept = _kept_rows(evidence_rows)
    groups: dict[str, list[Any]] = {}
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

    literature_section = _full_section(
        "Literature — Prioritized Papers",
        literature_rows,
        lambda rows: _literature_section(rows, quality_map),
    )
    empirical_sections = [
        _full_section(
            "Patents", patent_rows, lambda rows: _patent_section(rows, quality_map), "###"
        ),
        _full_section(
            "Clinical Trials", trial_rows, lambda rows: _trial_section(rows, quality_map), "###"
        ),
        _full_section(
            "Regulatory", regulatory_rows, lambda rows: _generic_section(rows, quality_map), "###"
        ),
        _full_section(
            "OpenTargets Associations",
            ot_rows,
            lambda rows: _opentargets_section(rows, quality_map),
            "###",
        ),
        _full_section(
            "Druggability",
            druggability_rows,
            lambda rows: _generic_section(rows, quality_map),
            "###",
        ),
        _full_section(
            "Genetics", genetics_rows, lambda rows: _generic_section(rows, quality_map), "###"
        ),
        _full_section(
            "Omics & Expression",
            omics_rows,
            lambda rows: _generic_section(rows, quality_map),
            "###",
        ),
        _full_section(
            "Functional Genomics",
            functional_rows,
            lambda rows: _generic_section(rows, quality_map),
            "###",
        ),
        _full_section(
            "Constraint", constraint_rows, lambda rows: _generic_section(rows, quality_map), "###"
        ),
    ]
    empirical_count = (
        len(patent_rows)
        + len(trial_rows)
        + len(regulatory_rows)
        + len(ot_rows)
        + len(druggability_rows)
        + len(genetics_rows)
        + len(omics_rows)
        + len(functional_rows)
        + len(constraint_rows)
    )
    body = "\n---\n\n".join(
        [
            literature_section,
            f"## Empirical ({empirical_count})\n\n" + "\n---\n\n".join(empirical_sections),
        ]
    )

    return f"""# Full Evidence Report — {target_gene} / {disease}

**Target gene:** {target_gene}{f" (`{gene_id}`)" if gene_id else ""}
**Disease:** {disease}{f" (`{disease_id}`)" if disease_id else ""}
**Kept evidence:** {len(kept)} sources
{version_line()}

This document lists every source that passed screening (verdict **keep**), grouped by type,
with links to the original record. See [report.md](./report.md) for the executive synthesis.

---

## Service Reports & External Resources

{_service_links_section(kept)}

---

{body}

---

{run_info_md(generated_at, provenance)}"""


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
        investigation_summary = data.get("investigation_summary", "")
        investigation_error = data.get("investigation_error", "")
        liabilities = data.get("candidate_liabilities")
        opposite_liabilities = data.get("opposite_liabilities") or []
        liability_assessed = bool(data.get("liability_assessed", True))
        precedent = data.get("drug_precedent")
        programme = data.get("programme_reading")
        disease_scope = data.get("disease_scope")
        degradations = data.get("degradations") or []
        dropped_claims = data.get("dropped_claims") or []

        now = datetime.now(UTC)
        try:
            provenance: Provenance | None = collect_provenance(ctx.router)
        except Exception as exc:  # provenance is metadata — never block the report on it
            _log.warning("report: could not resolve run provenance: %s", describe_exception(exc))
            provenance = None
        report_dir = _REPORT_ROOT / target_gene / _safe_id(disease_id) / _safe_direction(direction)
        report_dir.mkdir(parents=True, exist_ok=True)

        # This run's verdicts, by evidence id: rows taken from the evidence cache belong to
        # the run that fetched them and carry whichever verdict was stored last.
        verdicts = {
            str(e["evidence_id"]): {
                "verdict": e.get("verdict", ""),
                "rationale": e.get("screening_rationale", ""),
            }
            for e in evidence_summary
            if e.get("evidence_id")
        }
        async with get_session() as session:
            evidence_rows = await EvidenceRepository(session).get_for_run(msg.run_id, verdicts)
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
                investigation_summary=investigation_summary,
                kept_db_rows=kept_rows,
                provenance=provenance,
                liabilities=liabilities,
                investigation_error=investigation_error,
                precedent=precedent,
                programme=programme,
                disease_scope=disease_scope,
                degradations=degradations,
                dropped_claims=dropped_claims,
                opposite_liabilities=opposite_liabilities,
                liability_assessed=liability_assessed,
                direction=direction,
            )
            report_path = report_dir / "report.md"
            report_path.write_text(content, encoding="utf-8")
            artifact_uri = f"file://{report_path.resolve()}"

            export_summary_csv(
                target_gene, disease_id, direction, evidence_rows, run_id=str(msg.run_id)
            )

            full_content = render_full_report(
                target_gene=target_gene,
                disease=disease,
                disease_id=disease_id,
                gene_id=spec.get("gene_id") or "",
                evidence_rows=evidence_rows,
                generated_at=now,
                critiques=critiques,
                provenance=provenance,
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
