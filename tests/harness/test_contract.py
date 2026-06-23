# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for AgentContract, validate_inbound, validate_outbound."""

from __future__ import annotations

import uuid

import pytest

from core.exceptions import ContractViolation
from harness.contract import AgentContract, validate_inbound, validate_outbound
from schemas.messages import AgentMessage


@pytest.fixture()
def contract() -> AgentContract:
    return AgentContract(
        name="literature",
        consumes={"target_gene", "disease", "population"},
        produces={"literature_evidence"},
        max_loops=3,
    )


def _make_msg(task_spec: dict | None = None, payload=None) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        from_agent="planner",
        to_agent="literature",
        intent="task",
        task_spec=task_spec,
        payload=payload,
        trace_id="trace-001",
    )


# ---------------------------------------------------------------------------
# validate_inbound
# ---------------------------------------------------------------------------


def test_validate_inbound_passes_declared_keys(contract: AgentContract) -> None:
    msg = _make_msg(task_spec={"target_gene": "BRCA1", "disease": "breast cancer"})
    validate_inbound(msg, contract)  # should not raise


def test_validate_inbound_passes_with_no_task_spec(contract: AgentContract) -> None:
    msg = _make_msg(task_spec=None)
    validate_inbound(msg, contract)  # None task_spec is always valid


def test_validate_inbound_raises_on_extra_key(contract: AgentContract) -> None:
    msg = _make_msg(task_spec={"target_gene": "BRCA1", "sneaky_extra": "boom"})
    with pytest.raises(ContractViolation, match="sneaky_extra"):
        validate_inbound(msg, contract)


def test_validate_inbound_raises_on_multiple_extra_keys(contract: AgentContract) -> None:
    msg = _make_msg(task_spec={"a": 1, "b": 2})
    with pytest.raises(ContractViolation):
        validate_inbound(msg, contract)


# ---------------------------------------------------------------------------
# validate_outbound
# ---------------------------------------------------------------------------


def test_validate_outbound_passes_declared_dict_key(contract: AgentContract) -> None:
    msg = _make_msg(payload={"literature_evidence": []})
    validate_outbound(msg, contract)


def test_validate_outbound_passes_evidence_list_payload(contract: AgentContract) -> None:
    msg = _make_msg(payload=[])  # list[Evidence] — always allowed
    validate_outbound(msg, contract)


def test_validate_outbound_passes_none_payload(contract: AgentContract) -> None:
    msg = _make_msg(payload=None)
    validate_outbound(msg, contract)


def test_validate_outbound_raises_on_extra_dict_key(contract: AgentContract) -> None:
    msg = _make_msg(payload={"literature_evidence": [], "bonus_key": "oops"})
    with pytest.raises(ContractViolation, match="bonus_key"):
        validate_outbound(msg, contract)


# ---------------------------------------------------------------------------
# AgentContract
# ---------------------------------------------------------------------------


def test_contract_default_max_loops() -> None:
    c = AgentContract(name="x", consumes=set(), produces=set())
    assert c.max_loops == 3


def test_contract_skills_default_empty() -> None:
    c = AgentContract(name="x", consumes=set(), produces=set())
    assert c.skills == []
