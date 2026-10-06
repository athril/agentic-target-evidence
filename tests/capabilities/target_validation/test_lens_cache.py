# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A cached lens verdict that no longer validates must be a cache miss, not a crash.

Regression test for TRPC6/inhibit runs aborting in genetics_lens because the
llm_cache held verdicts written under LensVerdict schema_version '1.1' while the
current schema only accepts '1.0'.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from capabilities.target_validation import workflow
from schemas.evidence import lens_fingerprint
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
        verdict = await workflow._lens_cache_get(
            "TRPC6", "Focal Segmental Glomerulosclerosis", "inhibit", "genetics", "model-fp"
        )
    assert verdict is not None
    assert verdict.overall_verdict == "support"


async def test_lens_cache_get_treats_stale_schema_as_miss() -> None:
    cached = _verdict_payload(schema_version="1.1")
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=cached)):
        verdict = await workflow._lens_cache_get(
            "TRPC6", "Focal Segmental Glomerulosclerosis", "inhibit", "genetics", "model-fp"
        )
    assert verdict is None


async def test_lens_cache_get_returns_none_on_miss() -> None:
    with patch.object(workflow, "_llm_cache_get", AsyncMock(return_value=None)):
        verdict = await workflow._lens_cache_get(
            "TRPC6", "Focal Segmental Glomerulosclerosis", "inhibit", "genetics", "model-fp"
        )
    assert verdict is None


def test_lens_cache_key_uses_current_schema_version() -> None:
    key = workflow._lens_cache_key("TRPC6", "FSGS", "inhibit", "genetics")
    current = LensVerdict.model_fields["schema_version"].default
    assert key == lens_fingerprint("TRPC6", "FSGS", "inhibit", "genetics", schema_version=current)
