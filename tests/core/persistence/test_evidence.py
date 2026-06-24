# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for EvidenceRepository — mocked SQLAlchemy session."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from core.persistence.models import EvidenceRow
from core.persistence.repos.evidence import EvidenceRepository, _to_row
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _make_evidence(
    run_id: uuid.UUID,
    *,
    classification: DataClass = DataClass.NON_SENSITIVE,
    claim_text: str = "",
    source_evidence_id: uuid.UUID | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        target_gene="BRCA1",
        disease="breast cancer",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source="PMID:11111",
        source_link="https://pubmed.ncbi.nlm.nih.gov/11111/",
        classification=classification,
        claim_text=claim_text,
        source_evidence_id=source_evidence_id,
        provenance=Provenance(
            agent_name="test_agent",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            trace_id="trace-abc",
        ),
    )


def test_to_row_carries_claim_text_and_source_evidence_id() -> None:
    run_id = uuid.uuid4()
    blob_id = uuid.uuid4()
    evidence = _make_evidence(
        run_id,
        claim_text="TRPC6 DOWN -5.9-fold in definitive endoderm vs. ESC (Expression Atlas).",
        source_evidence_id=blob_id,
    )

    row = _to_row(evidence)

    assert row["claim_text"] == evidence.claim_text
    assert row["source_evidence_id"] == blob_id


def test_to_row_defaults_claim_text_to_empty_string() -> None:
    row = _to_row(_make_evidence(uuid.uuid4()))

    assert row["claim_text"] == ""
    assert row["source_evidence_id"] is None


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session


async def test_upsert_executes_insert_statement() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    evidence = _make_evidence(run_id)

    repo = EvidenceRepository(session)
    await repo.upsert(evidence)

    session.execute.assert_awaited_once()


async def test_bulk_upsert_skips_on_empty_list() -> None:
    session = _mock_session()
    repo = EvidenceRepository(session)

    await repo.bulk_upsert([])

    session.execute.assert_not_called()


async def test_bulk_upsert_executes_once_for_multiple() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    evidences = [_make_evidence(run_id) for _ in range(5)]

    repo = EvidenceRepository(session)
    await repo.bulk_upsert(evidences)

    session.execute.assert_awaited_once()


async def test_get_by_run_returns_rows() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    fake_row = MagicMock(spec=EvidenceRow)
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [fake_row]
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute.return_value = result_mock

    repo = EvidenceRepository(session)
    rows = await repo.get_by_run(run_id)

    assert rows == [fake_row]


async def test_update_embedding_sets_vector() -> None:
    session = _mock_session()
    evidence_id = uuid.uuid4()
    fake_row = MagicMock(spec=EvidenceRow)
    session.get.return_value = fake_row

    repo = EvidenceRepository(session)
    embedding = [0.1] * 768
    await repo.update_embedding(evidence_id, embedding)

    assert fake_row.embedding == embedding


async def test_update_embedding_noop_when_row_missing() -> None:
    session = _mock_session()
    session.get.return_value = None

    repo = EvidenceRepository(session)
    # Should not raise
    await repo.update_embedding(uuid.uuid4(), [0.0] * 768)


async def test_similarity_search_executes_ordered_query() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    fake_row = MagicMock(spec=EvidenceRow)
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [fake_row]
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute.return_value = result_mock

    repo = EvidenceRepository(session)
    results = await repo.similarity_search([0.1] * 768, run_id=run_id, k=5)

    session.execute.assert_awaited_once()
    assert results == [fake_row]


# ── find_by_target ────────────────────────────────────────────────────────────


async def test_find_by_target_returns_rows_for_gene_disease() -> None:
    session = _mock_session()
    fake_row = MagicMock(spec=EvidenceRow)
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [fake_row]
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute.return_value = result_mock

    repo = EvidenceRepository(session)
    rows = await repo.find_by_target("BRCA1", "breast cancer", "inhibit")

    session.execute.assert_awaited_once()
    assert rows == [fake_row]


async def test_find_by_target_with_evidence_type_filters() -> None:
    session = _mock_session()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute.return_value = result_mock

    repo = EvidenceRepository(session)
    rows = await repo.find_by_target("BRCA1", "breast cancer", "inhibit", evidence_type="article")

    session.execute.assert_awaited_once()
    assert rows == []


async def test_find_by_target_returns_empty_on_miss() -> None:
    session = _mock_session()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute.return_value = result_mock

    repo = EvidenceRepository(session)
    rows = await repo.find_by_target("UNKNOWN_GENE", "unknown disease", "unspecified")

    assert rows == []
