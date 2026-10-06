# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""_row_to_evidence must round-trip claim_text/source_evidence_id from a cache-hit row.

Regression test for the gap where claim-level Evidence rows (e.g. omics agent's
per-contrast Expression Atlas/GTEx claims) lost their only descriptive text when
reused across runs via the evidence cache, because the EvidenceRow ORM model had
no claim_text/source_evidence_id columns to read back from.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from capabilities.target_validation import workflow
from capabilities.target_validation.workflow import _row_to_evidence
from core.persistence.models import EvidenceRow


def _make_row(**overrides: object) -> MagicMock:
    row = MagicMock(spec=EvidenceRow)
    defaults: dict[str, object] = {
        "evidence_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "schema_version": "1.0",
        "gene": "TRPC6",
        "gene_id": "ENSG00000137672",
        "disease": "Focal Segmental Glomerulosclerosis",
        "disease_id": "EFO_0004236",
        "direction": "inhibit",
        "availability_date": None,
        "population": None,
        "evidence_type": "expression",
        "scope": "abstract",
        "source": "expression_atlas:E-MTAB-9194",
        "source_link": "https://www.ebi.ac.uk/gxa/experiments/E-MTAB-9194",
        "claim_text": "",
        "source_evidence_id": None,
        "query_used": None,
        "artifact_uri": None,
        "extra": {},
        "classification": "NON_SENSITIVE",
        "prov_agent_name": "omics",
        "prov_tool_name": "expression_atlas.get_differential_expression",
        "prov_timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "prov_model_used": None,
        "prov_trace_id": "trace-abc",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(row, key, value)
    return row


def test_row_to_evidence_carries_claim_text() -> None:
    blob_id = uuid.uuid4()
    row = _make_row(
        claim_text="TRPC6 DOWN -5.9-fold (p=0) in 'definitive endoderm cell; 72 hour' "
        "vs 'embryonic stem cell; 0 hour' [E-MTAB-9194] (Expression Atlas).",
        source_evidence_id=blob_id,
    )

    evidence = _row_to_evidence(row)

    assert evidence.claim_text == row.claim_text
    assert evidence.source_evidence_id == blob_id


def test_row_to_evidence_defaults_claim_text_to_empty_string() -> None:
    row = _make_row(claim_text=None)

    evidence = _row_to_evidence(row)

    assert evidence.claim_text == ""
    assert evidence.source_evidence_id is None


async def _lookup_with_rows(rows: list[Any]) -> list[Any]:
    @asynccontextmanager
    async def _session() -> Any:
        yield MagicMock()

    repo = MagicMock()
    repo.find_by_target = AsyncMock(return_value=rows)
    with (
        patch.object(workflow, "get_session", _session),
        patch.object(workflow, "EvidenceRepository", return_value=repo),
    ):
        return await workflow._evidence_cache_lookup(
            "TRPC6", "Focal Segmental Glomerulosclerosis", "inhibit", "expression"
        )


async def test_evidence_cache_lookup_skips_stale_schema_rows() -> None:
    """One row written under an older Evidence schema must not discard the others."""
    fresh = _make_row()
    stale = _make_row(schema_version="1.4")

    evidence = await _lookup_with_rows([stale, fresh])

    assert [e.evidence_id for e in evidence] == [fresh.evidence_id]


async def test_evidence_cache_lookup_all_stale_is_a_miss() -> None:
    evidence = await _lookup_with_rows([_make_row(schema_version="1.4")])

    assert evidence == []
