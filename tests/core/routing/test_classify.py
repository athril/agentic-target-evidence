# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid

from core.routing.classify import classify
from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage


def test_single_non_sensitive_evidence(sample_evidence: Evidence) -> None:
    assert classify(sample_evidence) == DataClass.NON_SENSITIVE


def test_single_sensitive_evidence(sensitive_evidence: Evidence) -> None:
    assert classify(sensitive_evidence) == DataClass.SENSITIVE


def test_list_with_any_sensitive_returns_sensitive(
    sample_evidence: Evidence, sensitive_evidence: Evidence
) -> None:
    assert classify([sample_evidence, sensitive_evidence]) == DataClass.SENSITIVE


def test_empty_list_returns_non_sensitive() -> None:
    assert classify([]) == DataClass.NON_SENSITIVE


def test_message_from_internal_data_is_sensitive(run_id: uuid.UUID, trace_id: str) -> None:
    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="internal_data",
        to_agent="genetics",
        intent="result",
        payload=None,
        trace_id=trace_id,
    )
    assert classify(msg) == DataClass.SENSITIVE


def test_message_from_pubmed_with_no_sensitive_payload_is_non_sensitive(
    run_id: uuid.UUID, trace_id: str, sample_evidence: Evidence
) -> None:
    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="pubmed",
        to_agent="literature",
        intent="result",
        payload=[sample_evidence],
        trace_id=trace_id,
    )
    assert classify(msg) == DataClass.NON_SENSITIVE
