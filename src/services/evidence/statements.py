# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Target statements: one reading per selected sentence, made by selection.

Asked "did the target's level go down?", the extractor answered for HDL-C; asked to type
a knockout, it wrote another gene's knockout as the target's. Here the model judges
nothing about entities. It sees a sentence's grounded mentions, numbered
(services/evidence/grounding.py), and picks fixed choices (skills/statement_reading.md):

* the exposure mention and its kind (a variant, a drug, a genetic perturbation of the
  gene, or a level only observed);
* for a genetic perturbation only, whether the experiment lowered or raised the gene;
* the outcome mention, which way it moved in the exposed group, and whether the sentence
  reports that change as significant (a reported null result has no direction);
* the study design, used only where PubMed's publication types and MeSH say nothing.

Code assembles the statement (``assemble``): the exposure must be a mention of the right
kind — a variant tied to the target, one of the target's drugs, the target or its
ortholog — and the effect on the target is filled from data where the exposure is a
variant (``variant_effect``: by default its consequence, a truncating change) or a drug
(Open Targets known drugs). An observed level never sets an effect,
and a mention of another gene cannot be chosen. The study design comes from a fixed map
of NLM's own terms (``design_from_metadata``).

About 12 sentences go in one call; answers are cached by the caller per (PMID, sentence,
question digest).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from core.degradations import record_degradation
from core.json_utils import strip_json_fence
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import DataClass
from schemas.statement import (
    EffectSource,
    ExposureKind,
    OutcomeDirection,
    Significance,
    StudyDesign,
    TargetChange,
    TargetStatement,
)
from services.evidence.grounding import GroundedSentence, Mention

logger = logging.getLogger(__name__)

SKILL = "statement_reading"
# Bump when the rendering of the sentences or the parsing of the answers changes.
# 2: the significance of the outcome's change.
READER_VERSION = "2"
BATCH = 12
# Output budget per sentence. At 70 the two batches that gave no usable reply on
# 2026-10-22 had stopped at exactly the budget (920 tokens for 12), mid-JSON.
_TOKENS_PER_SENTENCE = 100
_TOKENS_PER_CALL = 80

_EXPOSURE_KINDS: frozenset[str] = frozenset(
    {"variant", "drug", "genetic_perturbation", "observed_level", "none"}
)
_SIGNIFICANCE: frozenset[str] = frozenset({"yes", "no", "not_stated"})
_DESIGNS: frozenset[str] = frozenset(
    {
        "rct",
        "meta_analysis",
        "mendelian_randomisation",
        "genetic_association",
        "observational",
        "preclinical",
        "other",
    }
)


class Selection(BaseModel):
    """The model's choices for one sentence."""

    model_config = ConfigDict(extra="ignore")

    id: str
    exposure: int = 0
    exposure_kind: str = "none"
    change: str = "none"
    outcome: int = 0
    outcome_direction: str = "none"
    significant: str = "not_stated"
    design: str = "other"


def sentence_key(sentence: GroundedSentence) -> str:
    return f"{sentence.source}#{sentence.index}"


def reader_digest(skill_text: str) -> str:
    """What the answers depend on besides the sentence: the skill and this module's
    rendering and parsing."""
    return hashlib.sha256(f"{READER_VERSION}|{skill_text}".encode()).hexdigest()[:16]


def render(sentences: Sequence[GroundedSentence]) -> str:
    """The sentences as the reader sees them: each with its numbered mentions."""
    blocks: list[str] = []
    for i, s in enumerate(sentences, start=1):
        lines = [f"S{i}: {' '.join(s.text.split())}"]
        lines += [f'  [{m.number}] "{m.text}" — {m.label()}' for m in s.mentions]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def parse(raw: str, n: int) -> dict[int, Selection] | None:
    """{sentence position (0-based): selection} from a reply; None when it does not
    parse. Ids outside the batch are dropped."""
    try:
        data = json.loads(strip_json_fence(raw))
    except (json.JSONDecodeError, ValueError):
        logger.warning("statement reading: reply does not parse: %r", raw[:200])
        return None
    items = data.get("sentences") if isinstance(data, dict) else data
    if not isinstance(items, list):
        logger.warning("statement reading: reply has no sentence list: %r", raw[:200])
        return None
    out: dict[int, Selection] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            sel = Selection.model_validate({**item, "id": str(item.get("id") or "")})
        except ValidationError:
            continue
        sid = sel.id.strip().upper().removeprefix("S")
        if sid.isdigit() and 1 <= int(sid) <= n:
            out.setdefault(int(sid) - 1, sel)
    return out


async def read_sentences(
    sentences: Sequence[GroundedSentence], ctx: RunContext
) -> dict[str, Selection]:
    """{sentence key: selection}, one call per ``BATCH`` sentences. A batch whose reply
    does not parse is skipped; the failures are recorded as one degradation."""
    if not sentences:
        return {}
    skill_text = ctx.load_skill(SKILL)
    provider, model = ctx.router.select(DataClass.NON_SENSITIVE, "claim_extraction")
    out: dict[str, Selection] = {}
    failed = unread = 0
    for start in range(0, len(sentences), BATCH):
        batch = list(sentences[start : start + BATCH])
        try:
            completion = await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": render(batch)}],
                    system=skill_text,
                    classification=DataClass.NON_SENSITIVE,
                    task="claim_extraction",
                    max_tokens=_TOKENS_PER_CALL + _TOKENS_PER_SENTENCE * len(batch),
                    model_override=model,
                )
            )
            parsed = parse(completion.content, len(batch))
        except Exception as exc:
            logger.warning("statement reading of a batch failed: %s", exc)
            parsed = None
        if parsed is None:
            failed += 1
            unread += len(batch)
            continue
        for i, sel in parsed.items():
            out[sentence_key(batch[i])] = sel
    if failed:
        record_degradation(
            "extraction: statement reading",
            f"{failed} batch(es) gave no usable reply",
            f"{unread} sentence(s) have no statement",
        )
    logger.info("statement reading: %d of %d sentence(s) read", len(out), len(sentences))
    return out


# ── study design from PubMed's own terms ────────────────────────────────────────────

# NLM publication types and MeSH descriptors, as PubMed writes them, in priority order.
_DESIGN_TERMS: tuple[tuple[StudyDesign, frozenset[str]], ...] = (
    ("mendelian_randomisation", frozenset({"mendelian randomization analysis"})),
    ("meta_analysis", frozenset({"meta-analysis", "network meta-analysis"})),
    (
        "rct",
        frozenset({"randomized controlled trial", "equivalence trial", "pragmatic clinical trial"}),
    ),
    (
        "genetic_association",
        frozenset(
            {
                "genome-wide association study",
                "genetic association studies",
                "genetic predisposition to disease",
                "polymorphism, single nucleotide",
            }
        ),
    ),
    (
        "observational",
        frozenset(
            {
                "observational study",
                "cohort studies",
                "case-control studies",
                "cross-sectional studies",
                "prospective studies",
                "retrospective studies",
            }
        ),
    ),
)
_ANIMAL_MODEL = frozenset({"disease models, animal", "mice, knockout", "mice, transgenic"})


def design_from_metadata(
    publication_types: Iterable[str], mesh_headings: Iterable[Mapping[str, Any] | str]
) -> StudyDesign | None:
    """The design PubMed's publication types and MeSH headings name; None when they name
    none (the paper is not indexed, or its terms are of no design here). A study of
    animals without humans is preclinical."""
    terms = {str(t).strip().lower() for t in publication_types}
    mesh = {
        str(h.get("descriptor") if isinstance(h, Mapping) else h).strip().lower()
        for h in mesh_headings
    }
    names = terms | mesh
    for design, words in _DESIGN_TERMS:
        if names & words:
            return design
    if mesh & _ANIMAL_MODEL or ("animals" in mesh and "humans" not in mesh):
        return "preclinical"
    return None


# ── assembly ────────────────────────────────────────────────────────────────────────

# A variant's effect on the target from data: (change, source), or None.
VariantEffect = Callable[[Mention], "tuple[TargetChange, EffectSource] | None"]


def truncating(hgvs: str) -> bool:
    """A protein change that stops or frameshifts the protein: a loss by its
    consequence."""
    h = (hgvs or "").strip()
    return h.startswith("p.") and (h.rstrip(")").endswith(("*", "X", "Ter")) or "fs" in h)


def consequence_effect(mention: Mention) -> tuple[TargetChange, EffectSource] | None:
    """A truncating variant of the target lowers it, by its consequence."""
    return ("decrease", "consequence") if truncating(mention.hgvs) else None


def _mention(sentence: GroundedSentence, number: int) -> Mention | None:
    return next((m for m in sentence.mentions if m.number == number), None)


def _valid_exposure(kind: str, mention: Mention) -> bool:
    if kind == "variant":
        return mention.kind == "variant" and mention.is_target
    if kind == "drug":
        return mention.kind == "chemical" and bool(mention.drug)
    if kind in ("genetic_perturbation", "observed_level"):
        return mention.kind == "gene" and (mention.is_target or mention.ortholog)
    return False


def assemble(
    sentence: GroundedSentence,
    selection: Selection | None,
    *,
    design: StudyDesign | None = None,
    drug_effects: Mapping[str, str] | None = None,
    variant_effect: VariantEffect = consequence_effect,
    species: str = "",
) -> TargetStatement:
    """The sentence's statement from the model's selection (module docstring). A choice
    that names no mention, or a mention of the wrong kind, is ``none``."""
    base: dict[str, Any] = {
        "source": sentence.source,
        "sentence": sentence.index,
        "text": sentence.text,
        "species": next(iter(sentence.species()), species),
        "design": design or "other",
    }
    if selection is None:
        return TargetStatement(**base)
    if design is None and selection.design in _DESIGNS:
        base["design"] = selection.design
    kind: ExposureKind = "none"
    effect: TargetChange = "none"
    source: EffectSource = "none"
    exposure = _mention(sentence, selection.exposure)
    if (
        exposure is not None
        and selection.exposure_kind in _EXPOSURE_KINDS
        and _valid_exposure(selection.exposure_kind, exposure)
    ):
        kind = selection.exposure_kind  # type: ignore[assignment]
        base["exposure_id"] = exposure.rsid or exposure.hgvs or exposure.drug or exposure.id
        base["exposure_text"] = exposure.text
        if kind == "variant":
            base["allele"] = exposure.allele
            found = variant_effect(exposure)
            if found is not None:
                effect, source = found
        elif kind == "drug":
            drug = (drug_effects or {}).get(exposure.drug, "")
            if drug in ("decrease", "increase"):
                effect, source = drug, "drug"  # type: ignore[assignment]
        elif kind == "genetic_perturbation" and selection.change in ("decrease", "increase"):
            effect, source = selection.change, "text"  # type: ignore[assignment]
    outcome = _mention(sentence, selection.outcome)
    outcome_fields: dict[str, Any] = {}
    if outcome is not None and outcome in sentence.outcomes:
        significance: Significance = (
            selection.significant  # type: ignore[assignment]
            if selection.significant in _SIGNIFICANCE
            else "not_stated"
        )
        # A reported null result is no effect: "the association … was nonsignificant"
        # was read as higher risk, and turned a gene's genetics from support to neutral.
        direction: OutcomeDirection = (
            selection.outcome_direction  # type: ignore[assignment]
            if selection.outcome_direction in ("higher", "lower") and significance != "no"
            else "none"
        )
        outcome_fields = {
            "outcome_kind": "disease" if outcome.kind == "disease" else "biomarker",
            "outcome_id": outcome.id,
            "outcome_text": outcome.text,
            "outcome_direction": direction,
            "in_scope": outcome.in_scope,
            "significance": significance,
        }
    return TargetStatement(
        **base,
        exposure_kind=kind,
        effect_on_target=effect,
        effect_source=source,
        **outcome_fields,
    )
