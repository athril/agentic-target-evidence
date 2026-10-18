# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Typed allele effects for genetics claims: which way the gene changed, which way risk moved.

The claim extractor tagged each literature claim with a therapeutic direction read from
surface words ("increased" → activate), so a protective loss-of-function allele that
"increased the likelihood of resolution" read as support for activating the gene. This
question asks only what a sentence states — the gene's change (loss, gain, unknown, with
the words that say so) and the carriers' risk (lower, higher, none, unknown) — and the
direction reading derives the therapeutic direction in code
(services/evidence/direction_of_effect.from_allele_effects).

Only claims that describe a genetic change are asked, a batch per call; answers are kept
per claim text by the caller, independent of the run's direction.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_questions import LensQuestion, ask, question_digest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass

logger = logging.getLogger(__name__)

_BATCH = 15

# A claim worth asking: it describes a genetic change in people.
GENETIC_CHANGE = re.compile(
    r"\b(?:variants?|alleles?|carriers?|mutations?|polymorphisms?|rs\d{3,}|genotypes?|"
    r"loss[- ]of[- ]function|gain[- ]of[- ]function|truncat\w*|deficien\w*|"
    r"haploinsufficien\w*|mendelian randomi[sz]ation|genetically (?:proxied|predicted)|"
    r"splice|nonsense|frameshift|homozyg\w*|heterozyg\w*|burden)\b",
    re.IGNORECASE,
)


class AlleleEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    variant_gene: str = ""
    gene_change: Literal["loss", "gain", "unknown"] = "unknown"
    change_basis: str = ""
    outcome: str = ""
    risk: Literal["lower", "higher", "no_effect", "unknown"] = "unknown"


class AlleleEffects(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[AlleleEffect] = []


ALLELE_QUESTION = LensQuestion(
    id="allele_effect",
    skill="genetics_q_allele_effect",
    answer_model=AlleleEffects,
    max_claims=_BATCH,
)


def allele_digest() -> str:
    return question_digest([ALLELE_QUESTION])


def describes_genetic_change(claim: CoreClaim) -> bool:
    return bool(GENETIC_CHANGE.search(claim.claim_text or ""))


def _quoted(basis: str, text: str) -> bool:
    def norm(s: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())

    b = norm(basis)
    return bool(b) and b in norm(text)


async def ask_allele_effects(
    claims: Iterable[CoreClaim], *, ctx: RunContext, classification: DataClass
) -> dict[str, dict[str, Any]]:
    """Typed allele effects for ``claims``: {claim evidence id: answer without the id}.

    Answers citing a claim the batch did not show, or whose ``change_basis`` is not in the
    claim's text, lose their gene change (it becomes unknown). A batch the model cannot
    answer is skipped; its claims stay unanswered.
    """
    pool = list(claims)
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pool), _BATCH):
        batch = pool[i : i + _BATCH]
        by_id = {str(c.evidence_id): c for c in batch}
        try:
            result = await ask(
                ALLELE_QUESTION,
                facts="",
                claims=batch,
                quality_map={},
                disease_classes=frozenset(),
                lens="genetics",
                ctx=ctx,
                classification=classification,
            )
        except Exception as exc:  # degrade: the reading falls back to the claim tags
            logger.warning("allele-effect question failed for a batch: %s", exc)
            continue
        shown = set(result.shown_claim_ids)
        for item in (result.answer or {}).get("claims") or []:
            cid = str(item.get("claim_id") or "")
            claim = by_id.get(cid)
            if claim is None or cid not in shown:
                continue
            answer = {k: v for k, v in item.items() if k != "claim_id"}
            if answer.get("gene_change") in ("loss", "gain") and not _quoted(
                str(answer.get("change_basis") or ""), claim.claim_text
            ):
                answer["gene_change"] = "unknown"
            out[cid] = answer
    logger.info("allele-effect question: %d of %d claim(s) answered", len(out), len(pool))
    return out
