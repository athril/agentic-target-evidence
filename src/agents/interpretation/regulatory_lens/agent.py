# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""RegulatoryLensAgent — approval precedent + label safety axes."""

from __future__ import annotations

from typing import Literal

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, map_lens_verdict, run_lens
from agents.interpretation._lens_confidence import reaggregate
from agents.interpretation._lens_precedent import apply_precedent_to_result
from agents.interpretation.regulatory_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import CoreClaim
from schemas.messages import AgentMessage
from schemas.verdicts import COUNTED_BY_GATE_RULE, AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.claim_extraction import LABEL_MOA_NAMES_TARGET, LABEL_MOA_OFF_TARGET
from services.evidence.disease_class_rules import build_disease_class_note


def _labels_off_target(texts: list[str]) -> bool:
    """True when label claims were shown and none names the target in its mechanism."""
    if any(LABEL_MOA_NAMES_TARGET in t for t in texts):
        return False
    return any(LABEL_MOA_OFF_TARGET in t for t in texts)


def _apply_in_class_rules(verdict: LensVerdict, claims: list[CoreClaim]) -> LensVerdict:
    """Pre-scoring rules for when every label shown is for a drug whose mechanism does not
    name the target (skills/regulatory_lens.md axis definitions):

    - approval_precedent is about the target/MoA, so another mechanism's approval in the
      indication makes it unfavourable ("label data confirms no approved drug targeting
      this gene"), not favourable;
    - label_safety cannot be assessed without an in-class drug, so it is uncertain;
    - regulatory_de_risking is unfavourable only for a liability attributable to the
      target, so an unfavourable call becomes uncertain.
    """
    if not _labels_off_target([c.claim_text for c in claims]):
        return verdict
    gene = verdict.target_gene
    changed: list[str] = []
    axes: list[AxisVerdict] = []
    for ax in verdict.axes:
        new: bool | None = ax.verdict
        if ax.axis == "approval_precedent":
            new = False
        elif ax.axis == "label_safety" or (ax.axis == "regulatory_de_risking" and new is False):
            new = None
        if new is not ax.verdict:
            changed.append(f"{ax.axis} → {_word(new)}")
            ax = ax.model_copy(update={"verdict": new})
        axes.append(ax)
    if not changed:
        return verdict
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="medium",
        rule_id="off_target_label",
        claim_excerpt="",
        message=(
            f"No FDA label shown names {gene} in its mechanism of action: an approval or label "
            f"liability of another mechanism is not {gene} precedent or a {gene} effect; "
            f"{', '.join(changed)}."
        ),
    )
    return verdict.model_copy(
        update={"axes": axes, "validation_flags": [*verdict.validation_flags, flag]}
    )


def _off_target_note(gene: str) -> str:
    """Prompt line stating the in-class fact the pre-scoring rule enforces, so the prose
    agrees with the axes instead of being corrected afterwards."""
    return (
        f"In-class check: no FDA label shown names {gene} in its mechanism of action. These "
        f"drugs act on other targets, so their approvals are not {gene} precedent and their "
        f"boxed warnings and contraindications are drug effects, not {gene} effects — do not "
        f"call them mechanism-relevant or a regulatory risk for drugs targeting {gene}."
    )


def _word(v: bool | None) -> str:
    return {True: "favourable", False: "unfavourable", None: "uncertain"}[v]


def neutral_when_labels_shown(verdict: LensVerdict) -> LensVerdict:
    """``insufficient_evidence`` becomes ``neutral`` when the lens was shown at least one
    FDA label claim: "insufficient" then means no label data at all. A canned verdict
    (no axes: unparseable reply or no evidence) is left alone, so it is recomputed."""
    if (
        verdict.overall_verdict != "insufficient_evidence"
        or not verdict.axes
        or not verdict.shown_inputs.get("label")
    ):
        return verdict
    flag = ValidationFlag(
        lens="regulatory",
        severity="low",
        rule_id="regulatory.labels_shown",
        claim_excerpt="",
        message=(
            f"The lens was shown {verdict.shown_inputs['label']} label claim(s), so the "
            "evidence is not missing: insufficient_evidence → neutral."
        ),
    )
    return reaggregate(
        verdict.model_copy(
            update={
                "overall_verdict": "neutral",
                "validation_flags": [*verdict.validation_flags, flag],
            }
        ),
        align=False,
    )


def verdict_from_axes(verdict: LensVerdict) -> LensVerdict:
    """The overall verdict from the lens's own axes (skills/regulatory_lens.md, overall
    verdict guide): oppose when an approved in-class drug carries a mechanism-relevant
    label liability (``label_safety`` false), support when approval precedent exists,
    neutral otherwise. ``insufficient_evidence`` and a canned verdict are left alone.

    An opposed or class-failed precedent also opposes: the precedent rule marks it
    (``COUNTED_BY_GATE_RULE``) and the score counts it once, through its gates.
    ``regulatory_de_risking`` is false only for a label liability of the target's biology,
    which ``label_safety`` already carries, so it does not vote.
    """
    if verdict.overall_verdict == "insufficient_evidence" or not verdict.axes:
        return verdict
    axis = {a.axis: a.verdict for a in verdict.axes}
    overall: Literal["support", "oppose", "neutral"]
    reports_precedent = any(f.rule_id == COUNTED_BY_GATE_RULE for f in verdict.validation_flags)
    if axis.get("label_safety") is False or reports_precedent:
        overall = "oppose"
    elif axis.get("approval_precedent") is True:
        overall = "support"
    else:
        overall = "neutral"
    if overall == verdict.overall_verdict:
        return verdict
    flag = ValidationFlag(
        lens="regulatory",
        severity="low",
        rule_id="regulatory.verdict_from_axes",
        claim_excerpt="",
        message=f"Overall verdict computed from the axes: {verdict.overall_verdict} → {overall}.",
    )
    return reaggregate(
        verdict.model_copy(
            update={
                "overall_verdict": overall,
                "validation_flags": [*verdict.validation_flags, flag],
            }
        ),
        align=False,
    )


class RegulatoryLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        has_label = bool(spec.get("fda_label_text"))
        parts: list[str] = []
        disease_class_note = build_disease_class_note(
            spec.get("disease_classes") or (), "regulatory"
        )
        if disease_class_note:
            parts.append(disease_class_note)
        if has_label:
            parts.append(spec["fda_label_text"])
        precedent = spec.get("drug_precedent") or {}
        if precedent.get("text"):
            parts.append(str(precedent["text"]))
        claim_texts = [str(c.get("claim_text") or "") for c in spec.get("extracted_claims") or []]
        if _labels_off_target(claim_texts):
            parts.append(_off_target_note(spec.get("target_gene", "the target")))
        extra = "\n".join(parts) + "\n" if parts else ""
        target_gene = spec.get("target_gene", "this target")
        empty_note = (
            f"No FDA drug-label records were retrieved for {target_gene} in this run. "
            "This lens reasons only over FDA label data; an absence of records most often "
            "indicates that no approved drug targets this gene (a first-in-class signal) but "
            "cannot by itself confirm it. Disease-space regulatory context — approved therapies "
            "of other mechanisms, accepted endpoints, established trial pathways for the "
            "indication — is outside this lens's scope and is assessed by the clinical and "
            "commercial lenses. No label-level safety liabilities (black-box warnings, "
            "contraindications) can be inferred where no in-class approved drug exists."
        )
        result = await run_lens(
            msg,
            ctx,
            lens="regulatory",
            evidence_types=LENS_EVIDENCE_TYPES["regulatory"],
            skill_name="regulatory_lens",
            extra_context=extra,
            guard_empty=True,
            has_fallback_evidence=has_label,
            empty_evidence_note=empty_note,
            adjust_verdict=_apply_in_class_rules,
        )
        result = map_lens_verdict(result, neutral_when_labels_shown)
        result = apply_precedent_to_result(result, precedent, spec.get("programme_reading"))
        return map_lens_verdict(result, verdict_from_axes)
