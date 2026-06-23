# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for RunRepository — mocked SQLAlchemy session."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from core.persistence.models import Run
from core.persistence.repos.runs import RunRepository


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session


def _make_run(run_id: uuid.UUID) -> Run:
    run = Run(
        id=run_id,
        status="pending",
        target_gene="BRCA1",
        disease="breast cancer",
        user_request="validate BRCA1",
        step_budget_total=200,
        step_budget_consumed=0,
    )
    return run


async def test_create_adds_run_and_flushes() -> None:
    session = _mock_session()
    repo = RunRepository(session)
    run_id = uuid.uuid4()

    run = await repo.create(
        run_id=run_id,
        target_gene="BRCA1",
        disease="breast cancer",
        user_request="validate BRCA1",
    )

    session.add.assert_called_once()
    session.flush.assert_awaited_once()
    assert run.id == run_id
    assert run.status == "pending"
    assert run.step_budget_consumed == 0


async def test_create_passes_optional_fields() -> None:
    session = _mock_session()
    repo = RunRepository(session)

    run = await repo.create(
        run_id=uuid.uuid4(),
        target_gene="TP53",
        disease="lung cancer",
        user_request="validate TP53",
        population="adult",
        tissue="lung",
        step_budget_total=100,
    )

    assert run.population == "adult"
    assert run.tissue == "lung"
    assert run.step_budget_total == 100


async def test_get_returns_run_when_found() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    expected = _make_run(run_id)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = expected
    session.execute.return_value = result_mock

    repo = RunRepository(session)
    run = await repo.get(run_id)

    assert run is expected


async def test_get_returns_none_when_not_found() -> None:
    session = _mock_session()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute.return_value = result_mock

    repo = RunRepository(session)
    assert await repo.get(uuid.uuid4()) is None


async def test_update_status_executes_update() -> None:
    session = _mock_session()
    repo = RunRepository(session)
    run_id = uuid.uuid4()

    await repo.update_status(run_id, "running")

    session.execute.assert_awaited_once()


async def test_increment_step_budget_increments_consumed() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    run = _make_run(run_id)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run
    session.execute.return_value = result_mock

    repo = RunRepository(session)
    await repo.increment_step_budget(run_id, 3)

    assert run.step_budget_consumed == 3


async def test_increment_step_budget_noop_when_run_missing() -> None:
    session = _mock_session()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute.return_value = result_mock

    repo = RunRepository(session)
    # Should not raise even if run doesn't exist
    await repo.increment_step_budget(uuid.uuid4(), 1)


# ── Rerun cache fields ────────────────────────────────────────────────────────


async def test_create_with_model_fingerprint() -> None:
    session = _mock_session()
    repo = RunRepository(session)
    run_id = uuid.uuid4()

    run = await repo.create(
        run_id=run_id,
        target_gene="BRCA1",
        disease="breast cancer",
        user_request="validate BRCA1",
        model_fingerprint="llama3.1:8b",
        force_refresh=True,
    )

    assert run.model_fingerprint == "llama3.1:8b"
    assert run.force_refresh is True
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


async def test_create_defaults_model_fingerprint_to_none() -> None:
    session = _mock_session()
    repo = RunRepository(session)

    run = await repo.create(
        run_id=uuid.uuid4(),
        target_gene="TP53",
        disease="lung cancer",
        user_request="validate TP53",
    )

    assert run.model_fingerprint is None
    assert run.force_refresh is False


async def test_find_prior_run_returns_done_run() -> None:
    session = _mock_session()
    run_id = uuid.uuid4()
    expected = _make_run(run_id)
    expected.status = "done"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = expected
    session.execute.return_value = result_mock

    repo = RunRepository(session)
    run = await repo.find_prior_run("BRCA1", "breast cancer", "inhibit", None, None)

    session.execute.assert_awaited_once()
    assert run is expected


async def test_find_prior_run_ignores_non_done_runs() -> None:
    session = _mock_session()
    result_mock = MagicMock()
    # DB query already filters by status="done"; None means no done run found
    result_mock.scalar_one_or_none.return_value = None
    session.execute.return_value = result_mock

    repo = RunRepository(session)
    run = await repo.find_prior_run("BRCA1", "breast cancer", "inhibit", None, None)

    assert run is None
