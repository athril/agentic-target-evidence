# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""BiologyLensAgent — druggability + mechanism-of-action axes."""

from __future__ import annotations

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, run_lens
from agents.interpretation.biology_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.constraint_interpret import (
    interpret_depmap_relevance,
    interpret_expression_context_for_mechanism,
)
from services.evidence.mouse_phenotype import render_mouse_phenotype


class BiologyLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        parts: list[str] = []

        if spec.get("ot_tractability_text"):
            parts.append(f"Tractability (Open Targets): {spec['ot_tractability_text']}")
        if spec.get("ot_mouse_text"):
            # Render and clean the mouse phenotype text before injecting
            cleaned_mouse = render_mouse_phenotype(spec["ot_mouse_text"])
            parts.append(f"Mouse KO phenotypes (Open Targets): {cleaned_mouse}")
        if spec.get("omics_expression_text"):
            parts.append(spec["omics_expression_text"])
        if spec.get("regulatory_element_text"):
            parts.append(spec["regulatory_element_text"])
        if spec.get("disease_tissue_expression_note"):
            parts.append(
                f"Disease-tissue expression grounding: {spec['disease_tissue_expression_note']}"
            )
        bulk_tpm = spec.get("bulk_tpm")
        disease_tissue = spec.get("disease_tissue") or spec.get("disease") or "disease tissue"
        expr_caveat = interpret_expression_context_for_mechanism(
            bulk_tpm, spec.get("hpa_specificity") or "", disease_tissue
        )
        if expr_caveat:
            parts.append(expr_caveat)

        # DepMap CRISPR dependency block
        depmap_text = spec.get("depmap_text", "")
        if depmap_text:
            mean = spec.get("depmap_mean_chronos")
            std = spec.get("depmap_std_chronos")
            dep_frac = spec.get("depmap_dependency_fraction")
            is_common = spec.get("depmap_is_common_essential", False)
            is_selective = spec.get("depmap_is_strongly_selective", False)
            sel_lineages = spec.get("depmap_selective_lineages") or []
            lineage_rows = spec.get("depmap_lineage_breakdown") or []

            dm_lines = [f"DepMap CRISPR dependency: {depmap_text}"]

            if mean is not None:
                score_line = f"  Mean Chronos score: {mean:.3f}"
                if std is not None:
                    score_line += f" (SD {std:.3f})"
                dm_lines.append(score_line)

            if dep_frac is not None:
                dm_lines.append(
                    f"  Dependency fraction: {dep_frac:.1%} of cell lines (threshold ≤ −0.5)"
                )

            if is_common:
                dm_lines.append(
                    "  STATUS: Common essential (pan-cancer) — indiscriminate lethality; high safety risk."
                )
            elif is_selective and sel_lineages:
                dm_lines.append(
                    f"  STATUS: Strongly selective — high dependency in: {', '.join(sel_lineages[:5])}"
                )
            elif is_selective:
                # Flag is set but no lineage qualifies — find the best candidate to show
                # the actual numbers so the model cannot "resolve" the ambiguity itself.
                if lineage_rows:
                    best = max(
                        lineage_rows,
                        key=lambda r: (
                            (r.get("n_dependent", 0) / r["n_total"]) if r.get("n_total") else 0
                        ),
                    )
                    n_dep_b = best.get("n_dependent", 0)
                    n_tot_b = best.get("n_total", 0)
                    me_b = best.get("mean_effect")
                    score_b = f" (mean {me_b:.3f})" if me_b is not None else ""
                    dm_lines.append(
                        f"  STATUS: 'Strongly selective' flag set, but NO lineage reaches the dependency"
                        f" threshold — highest is {best['lineage']} at {n_dep_b}/{n_tot_b}{score_b}."
                        f" No lineage-specific essentiality."
                    )
                else:
                    dm_lines.append(
                        "  STATUS: 'Strongly selective' flag set, but no per-lineage data available"
                        " — no lineage-specific essentiality can be established."
                    )

            if lineage_rows:
                top = sorted(
                    lineage_rows,
                    key=lambda r: (
                        (r.get("n_dependent", 0) / r["n_total"]) if r.get("n_total") else 0
                    ),
                    reverse=True,
                )[:8]
                dm_lines.append("  Top lineages by dependency fraction:")
                for lr in top:
                    n_dep = lr.get("n_dependent", 0)
                    n_tot = lr.get("n_total", 0)
                    mean_eff = lr.get("mean_effect")
                    frac = f"{n_dep}/{n_tot}"
                    score_str = f", mean {mean_eff:.3f}" if mean_eff is not None else ""
                    dm_lines.append(f"    • {lr['lineage']}: {frac} dependent{score_str}")
                if all(lr.get("n_dependent", 0) == 0 for lr in top):
                    dm_lines.append(
                        "  NOTE: all lineages above show 0 dependent lines —"
                        " do not describe any lineage as 'essential' or a 'dependency'."
                    )

            relevance_caveat = interpret_depmap_relevance(
                mean_chronos=mean,
                dependency_fraction=dep_frac,
                is_common_essential=is_common,
                is_oncology_indication=spec.get("is_oncology_indication", False),
            )
            if relevance_caveat:
                dm_lines.append(f"  {relevance_caveat}")

            parts.append("\n".join(dm_lines))

        extra = "\n\n".join(parts) + "\n\n" if parts else ""
        return await run_lens(
            msg,
            ctx,
            lens="biology",
            evidence_types=LENS_EVIDENCE_TYPES["biology"],
            skill_name="biology_lens",
            extra_context=extra,
        )
