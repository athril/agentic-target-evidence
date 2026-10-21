# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Extracted claims checked against their source, and the record of claims left out.

The extractor can reverse what a paper did to the target: a knockout of another gene the
target only modifies became "<target> knockout …". A regex guard dropped claims whose
"loss of <gene>" wording was missing from the source, but 13 of the 14 claims it dropped
in panel runs were faithful ("an absence of <protein> (<gene> …)", "deletion of the
<gene> … enhancer"). The source is now asked one narrow question per claim instead:
does the text state the same change of the target (supported), the change made to
something else or the opposite way (contradicted), or nothing either way (not stated)?

On the claims it actually saw in two resumed runs, the check was wrong more often than
right: on drug-class claims ("<target> inhibitors improve outcomes when added to
statins") the model judged the population or the comparator, and its "contradicting"
quote restated the claim. Its verdicts are therefore recorded, not applied:

* a contradicted or not-stated claim is kept unchanged and listed as a
  ``faithfulness_flag`` with the quote the model gave (when found word for word in the
  source);
* a reply that fails to parse leaves the claims unchanged, is logged with its first 200
  characters, and records a degradation.

Every claim left out of a run, by this check or by the grounding checks of
claim_extraction, is kept as a ``DroppedClaim`` the report lists.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from core.degradations import record_degradation
from core.evidence_text import normalize_typography
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import CoreClaim, Evidence

logger = logging.getLogger(__name__)

DropReason = Literal["faithfulness_flag", "number_not_in_source", "example_identifier"]
Verdict = Literal["supported", "contradicted", "not_stated"]
_VERDICTS: frozenset[str] = frozenset({"supported", "contradicted", "not_stated"})

# Source text shown per call, in characters: an abstract or trial summary fits whole; a
# full text is cut to keep the call inside the claim_extraction context window.
_MAX_SOURCE_CHARS = 12_000
# Reply budget per claim (verdict plus a quoted sentence) and per call.
_TOKENS_PER_CLAIM = 120
_TOKENS_PER_CALL = 100


class DroppedClaim(BaseModel):
    """A claim left out of the run, or kept but flagged by the faithfulness check, and
    why."""

    source: str  # the source record, e.g. "PMID:123"
    claim_text: str
    reason: DropReason
    quote: str = ""


def _norm(text: str) -> str:
    return " ".join(normalize_typography(text).lower().split())


def quoted_in(quote: str, source_text: str) -> bool:
    """``quote`` appears word for word in ``source_text`` (whitespace and case aside)."""
    q = _norm(quote)
    return bool(q) and q in _norm(source_text)


def _parse(raw: str, n: int) -> dict[int, tuple[str, str]] | None:
    """{claim index: (verdict, quote)} from a reply; None when it does not parse."""
    try:
        data = json.loads(strip_json_fence(raw))
    except (json.JSONDecodeError, ValueError):
        logger.warning("faithfulness check: reply does not parse: %r", raw[:200])
        return None
    answers = data.get("answers") if isinstance(data, dict) else data
    if not isinstance(answers, list):
        logger.warning("faithfulness check: reply has no answer list: %r", raw[:200])
        return None
    out: dict[int, tuple[str, str]] = {}
    for a in answers:
        if not isinstance(a, dict):
            continue
        i, verdict = a.get("i"), str(a.get("verdict") or "").strip().lower()
        if isinstance(i, int) and 0 <= i < n and verdict in _VERDICTS:
            out[i] = (verdict, " ".join(str(a.get("quote") or "").split()))
    return out


def _user_content(target_gene: str, source_text: str, claims: Sequence[CoreClaim]) -> str:
    listed = "\n".join(f"{i}. {c.claim_text}" for i, c in enumerate(claims))
    return (
        f"Target gene: {target_gene}\n\n"
        f"Text:\n{source_text[:_MAX_SOURCE_CHARS]}\n\n"
        f"Claims:\n{listed}"
    )


async def check_claims(
    claims_by_source: Sequence[tuple[Evidence, str, Sequence[CoreClaim]]],
    target_gene: str,
    ctx: RunContext,
) -> dict[UUID, tuple[str, str]]:
    """{claim id: (verdict, quote)} for each claim, asked one call per source document
    (each item: the source, the text the extractor saw, the claims to check). Claims of a
    reply that fails to parse get no verdict; the failures are recorded as one
    degradation."""
    if not claims_by_source:
        return {}
    skill_text = ctx.load_skill("claim_faithfulness")
    verdicts: dict[UUID, tuple[str, str]] = {}
    failed = unchecked = 0
    for source, source_text, claims in claims_by_source:
        if not claims:
            continue
        classification = classify([source])
        provider, model = ctx.router.select(classification, "claim_extraction")
        try:
            completion = await provider.complete(
                CompletionRequest(
                    messages=[
                        {
                            "role": "user",
                            "content": _user_content(target_gene, source_text, claims),
                        }
                    ],
                    system=skill_text,
                    classification=classification,
                    task="claim_extraction",
                    max_tokens=_TOKENS_PER_CALL + _TOKENS_PER_CLAIM * len(claims),
                    model_override=model,
                )
            )
            parsed = _parse(completion.content, len(claims))
        except Exception as exc:
            logger.warning("faithfulness check of %s failed: %s", source.source, exc)
            parsed = None
        if parsed is None:
            failed += 1
            unchecked += len(claims)
            continue
        for i, claim in enumerate(claims):
            if i in parsed:
                verdicts[claim.evidence_id] = parsed[i]
    if failed:
        record_degradation(
            "extraction: faithfulness check",
            f"{failed} source(s) gave no usable reply",
            f"{unchecked} claim(s) not checked against the source",
        )
    return verdicts


def apply_verdicts(
    claims: Sequence[CoreClaim],
    verdicts: dict[UUID, tuple[str, str]],
    source_text: str,
    *,
    source: str = "",
) -> tuple[list[CoreClaim], list[DroppedClaim]]:
    """The claims, unchanged, and a ``faithfulness_flag`` for each one the check called
    contradicted or not stated (module docstring). Claims without a verdict, and
    supported ones, are not flagged."""
    flags = [
        DroppedClaim(
            source=source,
            claim_text=claim.claim_text,
            reason="faithfulness_flag",
            quote=quote if quoted_in(quote, source_text) else "",
        )
        for claim in claims
        for verdict, quote in [verdicts.get(claim.evidence_id, ("supported", ""))]
        if verdict in ("contradicted", "not_stated")
    ]
    return list(claims), flags


def grounding_reason(reason: str) -> DropReason:
    """The ``DroppedClaim`` reason of a claim_extraction ``ungrounded_reason`` message."""
    return (
        "example_identifier"
        if reason.startswith("example identifier")
        else ("number_not_in_source")
    )
