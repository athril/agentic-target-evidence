# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from schemas.messages import AgentMessage


def test_agent_message_round_trips_json(sample_message: AgentMessage) -> None:
    restored = AgentMessage.model_validate_json(sample_message.model_dump_json())
    assert restored.message_id == sample_message.message_id
    assert restored.run_id == sample_message.run_id
    assert restored.intent == sample_message.intent


def test_error_reply_swaps_from_to(sample_message: AgentMessage) -> None:
    reply = sample_message.error_reply("something went wrong")
    assert reply.from_agent == sample_message.to_agent
    assert reply.to_agent == sample_message.from_agent
    assert reply.intent == "error"
    assert reply.run_id == sample_message.run_id
    assert reply.trace_id == sample_message.trace_id


def test_error_reply_carries_detail(sample_message: AgentMessage) -> None:
    detail = "loop limit exceeded"
    reply = sample_message.error_reply(detail)
    assert reply.task_spec is not None
    assert reply.task_spec["detail"] == detail


def test_error_reply_has_new_message_id(sample_message: AgentMessage) -> None:
    reply = sample_message.error_reply("x")
    assert reply.message_id != sample_message.message_id


def test_schema_version_is_1_0(sample_message: AgentMessage) -> None:
    assert sample_message.schema_version == "1.0"


def test_sent_at_is_set_automatically(run_id, trace_id) -> None:
    import uuid
    from datetime import datetime

    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent="literature",
        intent="task",
        trace_id=trace_id,
    )
    assert isinstance(msg.sent_at, datetime)
    assert msg.sent_at.tzinfo is not None


def test_error_reply_has_fresh_sent_at(sample_message: AgentMessage) -> None:
    reply = sample_message.error_reply("boom")
    assert reply.sent_at >= sample_message.sent_at


# ── payload union discrimination ──────────────────────────────────────────────


def test_payload_accepts_evidence_list(sample_message: AgentMessage, sample_evidence) -> None:
    assert isinstance(sample_message.payload, list)
    assert all(isinstance(e, type(sample_evidence)) for e in sample_message.payload)


def test_payload_accepts_dict(run_id, trace_id) -> None:
    import uuid

    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="experiment",
        to_agent="planner",
        intent="result",
        payload={"score": 87, "target": "BRCA1"},
        trace_id=trace_id,
    )
    assert isinstance(msg.payload, dict)
    assert msg.payload["score"] == 87


def test_payload_accepts_none(run_id, trace_id) -> None:
    import uuid

    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent="literature",
        intent="task",
        payload=None,
        trace_id=trace_id,
    )
    assert msg.payload is None


def test_evidence_dicts_in_payload_are_coerced_to_evidence(
    run_id, trace_id, sample_evidence
) -> None:
    import uuid

    from schemas.evidence import Evidence

    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="literature",
        to_agent="screening",
        intent="result",
        payload=[sample_evidence.model_dump()],
        trace_id=trace_id,
    )
    assert isinstance(msg.payload, list)
    assert isinstance(msg.payload[0], Evidence)
