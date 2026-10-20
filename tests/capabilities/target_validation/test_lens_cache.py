# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens verdict cache: lookup validation and an input-covering cache key.

A cached lens verdict that no longer validates must be a cache miss, not a crash
(regression: TRPC6/inhibit runs aborting in genetics_lens because the llm_cache held
verdicts written under LensVerdict schema_version '1.1').

The key must cover what the lens reads (regression: the regulatory lens's cached
"no evidence" verdict would have outlived the fix that let FDA labels through
screening, because the key was only gene/disease/direction/lens).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from agents.interpretation._lens_base import lens_input_digest
from capabilities.target_validation import workflow
from schemas.evidence import (
    CoreClaim,
    DataClass,
    Direction,
    EvidenceType,
    Provenance,
    lens_fingerprint,
)
from schemas.verdicts import LensVerdict


def _verdict_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="trace-abc",
        lens="genetics",
        target_gene="TRPC6",
        disease="Focal Segmental Glomerulosclerosis",
        direction="inhibit",
        overall_verdict="support",
        confidence=0.9,
    ).model_dump(mode="json")
    payload.update(overrides)
    return payload


async def test_lens_cache_get_returns_verdict_on_valid_row() -> None:
    cached = _verdict_payload()
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=cached)):
        verdict = await workflow._lens_cache_get("cache-key", "genetics", "model-fp")
    assert verdict is not None
    assert verdict.overall_verdict == "support"


async def test_lens_cache_get_treats_stale_schema_as_miss() -> None:
    cached = _verdict_payload(schema_version="9.9")
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=cached)):
        verdict = await workflow._lens_cache_get("cache-key", "genetics", "model-fp")
    assert verdict is None


async def test_lens_cache_get_returns_none_on_miss() -> None:
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=None)):
        verdict = await workflow._lens_cache_get("cache-key", "genetics", "model-fp")
    assert verdict is None


def _claim(
    evidence_type: EvidenceType, text: str, source_evidence_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """A claim as it sits in task_spec, with a fresh per-run UUID like real extraction."""
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=source_evidence_id,
        run_id=uuid.uuid4(),
        gene="TRPC6",
        disease="FSGS",
        evidence_type=evidence_type,
        claim_text=text,
        direction=Direction.INHIBIT,
        confidence=0.7,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
    ).model_dump(mode="json")


def _spec(claims: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
    return {
        "target_gene": "TRPC6",
        "disease": "FSGS",
        "extracted_claims": claims,
        "source_quality": {},
        "fda_label_text": "",
        **context,
    }


_LABEL_CLAIM = "Sparsentan is FDA-approved to reduce proteinuria in FSGS."


def test_lens_cache_key_uses_current_schema_version_and_inputs() -> None:
    spec = _spec([])
    key = workflow._lens_cache_key("TRPC6", "FSGS", "inhibit", "genetics", spec)
    current = LensVerdict.model_fields["schema_version"].default
    inputs = lens_input_digest("genetics", spec, workflow.load_skill("genetics_lens"))
    assert key == lens_fingerprint(
        "TRPC6", "FSGS", "inhibit", "genetics", schema_version=current, inputs=inputs
    )


def test_input_digest_ignores_per_run_claim_uuids() -> None:
    """Re-extraction mints new claim UUIDs; the same evidence must still hit the cache."""
    a = _spec([_claim(EvidenceType.REGULATORY, _LABEL_CLAIM)])
    b = _spec([_claim(EvidenceType.REGULATORY, _LABEL_CLAIM)])
    assert lens_input_digest("regulatory", a, "skill") == lens_input_digest(
        "regulatory", b, "skill"
    )


def test_input_digest_ignores_claim_order() -> None:
    c1 = _claim(EvidenceType.REGULATORY, _LABEL_CLAIM)
    c2 = _claim(EvidenceType.REGULATORY, "No boxed warning on the sparsentan label.")
    assert lens_input_digest("regulatory", _spec([c1, c2]), "s") == lens_input_digest(
        "regulatory", _spec([c2, c1]), "s"
    )


def test_input_digest_changes_with_extra_context() -> None:
    """The regulatory regression: fda_label_text going from "" to label text must miss."""
    empty = _spec([])
    with_label = _spec([], fda_label_text="FDA-approved drug labels: Drug: SPARSENTAN | ...")
    assert lens_input_digest("regulatory", empty, "s") != lens_input_digest(
        "regulatory", with_label, "s"
    )


def test_input_digest_changes_with_routed_claims() -> None:
    assert lens_input_digest("regulatory", _spec([]), "s") != lens_input_digest(
        "regulatory", _spec([_claim(EvidenceType.REGULATORY, _LABEL_CLAIM)]), "s"
    )


def test_input_digest_ignores_claims_routed_to_other_lenses() -> None:
    """A new genetics claim must not invalidate the regulatory lens's verdict."""
    base = _spec([_claim(EvidenceType.REGULATORY, _LABEL_CLAIM)])
    more = _spec(
        [
            _claim(EvidenceType.REGULATORY, _LABEL_CLAIM),
            _claim(EvidenceType.GENETICS, "TRPC6 gain-of-function variants cause FSGS."),
        ]
    )
    assert lens_input_digest("regulatory", base, "s") == lens_input_digest("regulatory", more, "s")


def test_input_digest_changes_with_source_quality_of_routed_claims() -> None:
    src = uuid.uuid4()
    claims = [_claim(EvidenceType.REGULATORY, _LABEL_CLAIM, source_evidence_id=src)]
    unscored = _spec(claims)
    scored = _spec(claims, source_quality={str(src): {"sjr_score": 0.9}})
    assert lens_input_digest("regulatory", unscored, "s") != lens_input_digest(
        "regulatory", scored, "s"
    )


def test_input_digest_changes_with_skill_prompt() -> None:
    spec = _spec([])
    assert lens_input_digest("regulatory", spec, "v1") != lens_input_digest(
        "regulatory", spec, "v2"
    )


async def test_lens_cache_get_treats_canned_evidence_gap_as_miss() -> None:
    """The empty-evidence guard's verdict must not be replayed once evidence arrives.

    Regression: the regulatory lens's canned insufficient_evidence verdict (cached while
    screening dropped every openFDA row) would otherwise outlive the screening fix.
    """
    cached = _verdict_payload(overall_verdict="insufficient_evidence", confidence=0.0, axes=[])
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=cached)):
        verdict = await workflow._lens_cache_get("cache-key", "regulatory", "model-fp")
    assert verdict is None


async def test_lens_cache_get_keeps_reasoned_insufficient_verdict() -> None:
    axis = {"axis": "approval_precedent", "verdict": None, "confidence": 0.3}
    cached = _verdict_payload(overall_verdict="insufficient_evidence", confidence=0.3, axes=[axis])
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=cached)):
        verdict = await workflow._lens_cache_get("cache-key", "regulatory", "model-fp")
    assert verdict is not None
