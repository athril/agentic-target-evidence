# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Typed allele effects for genetics claims: which way risk moved for the carriers.

The claim extractor tagged each literature claim with a therapeutic direction read from
surface words ("increased" → activate), so a protective loss-of-function allele that
"increased the likelihood of resolution" read as support for activating the gene. The
direction now has two factors found apart. The gene's change is typed in code, from the
variant's nomenclature or the claim's own typed effect (services/evidence/
variant_registry.claim_changes): asked to type it, the model answered "unknown" on "the B2
allele … lower <target> concentration" and typed "<allele> … protective" as a loss from
its own knowledge. This question asks only what the sentence states about risk — the
gene the variant is in, the outcome and the carriers' risk (lower, higher, none,
unknown) — and the direction reading derives the therapeutic direction in code
(services/evidence/direction_of_effect.from_allele_effects).

Only claims that describe a genetic change, and whose change is known, are asked, a batch
per call; answers are kept per claim text by the caller, independent of the run's
direction.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_questions import LensQuestion, ask, question_digest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass
from services.evidence.variant_registry import KnownChange

logger = logging.getLogger(__name__)

_BATCH = 15
# One answer entry is under 100 tokens; the bound leaves room for long quoted words.
_TOKENS_PER_CLAIM = 160
# Claims asked per run (three batches), those naming the indication first.
ALLELE_CLAIMS_MAX = 3 * _BATCH

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
    tokens_per_claim=_TOKENS_PER_CLAIM,
)


def allele_digest() -> str:
    return question_digest([ALLELE_QUESTION])


def describes_genetic_change(claim: CoreClaim) -> bool:
    return bool(GENETIC_CHANGE.search(claim.claim_text or ""))


def with_known_change(
    claims: Iterable[CoreClaim], changes: Mapping[str, KnownChange]
) -> list[CoreClaim]:
    """The claims worth asking: only a claim whose gene change is known can set a direction."""
    return [c for c in claims if str(c.evidence_id) in changes]


def merge_changes(
    answers: Mapping[str, Mapping[str, Any]], changes: Mapping[str, KnownChange]
) -> dict[str, dict[str, Any]]:
    """The model's risk answers with the code-typed gene change of each claim
    (``gene_change``, ``change_basis``, ``change_source``), as the direction reading reads
    them (direction_of_effect.from_allele_effects)."""
    return {
        cid: {
            **answer,
            "gene_change": changes[cid].change,
            "change_basis": changes[cid].basis,
            "change_source": changes[cid].source,
        }
        for cid, answer in answers.items()
        if cid in changes
    }


async def ask_allele_effects(
    claims: Iterable[CoreClaim], *, ctx: RunContext, classification: DataClass
) -> dict[str, dict[str, Any]]:
    """Typed allele effects for ``claims``: {claim evidence id: answer without the id}.

    Answers citing a claim the batch did not show are dropped. A batch the model cannot
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
        except Exception as exc:  # degrade: the reading falls back to the claims' extracted effects
            logger.warning("allele-effect question failed for a batch: %s", exc)
            continue
        shown = set(result.shown_claim_ids)
        for item in (result.answer or {}).get("claims") or []:
            cid = str(item.get("claim_id") or "")
            claim = by_id.get(cid)
            if claim is None or cid not in shown:
                continue
            out[cid] = {k: v for k, v in item.items() if k != "claim_id"}
    logger.info("allele-effect question: %d of %d claim(s) answered", len(out), len(pool))
    return out
