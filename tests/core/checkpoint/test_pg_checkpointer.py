# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Round-trip tests for the checkpoint msgpack allowlist.

Guards against PipelineState gaining a new schemas.* model/enum without the
allowlist in pg_checkpointer.py being updated to match — if it's missed,
LangGraph silently warns now and will start refusing to deserialize the
field once LANGGRAPH_STRICT_MSGPACK becomes the default.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from core.checkpoint.pg_checkpointer import _ALLOWED_MSGPACK_MODULES
from schemas.evidence import (
    CoreClaim,
    DataClass,
    Direction,
    Evidence,
    EvidenceType,
    LensTopic,
    Provenance,
)
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict


def _provenance() -> Provenance:
    return Provenance(agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="trace")


def _evidence() -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="BRCA1",
        disease="cancer",
        direction=Direction.INHIBIT,
        evidence_type=EvidenceType.ARTICLE,
        topics=[LensTopic.BIOLOGY],
        provenance=_provenance(),
        classification=DataClass.NON_SENSITIVE,
        scope="abstract",
        source="PMID:1",
        source_link="https://example.test/1",
    )


def _core_claim() -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="BRCA1",
        disease="cancer",
        direction=Direction.ACTIVATE,
        evidence_type=EvidenceType.GENETICS,
        provenance=_provenance(),
        classification=DataClass.SENSITIVE,
    )


def _lens_verdict() -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="trace",
        lens="biology",
        target_gene="BRCA1",
        disease="cancer",
        direction=Direction.INHIBIT,
        axes=[AxisVerdict(axis="causality")],
    )


def _agent_message(evidence: Evidence) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        from_agent="literature",
        to_agent="screening",
        intent="result",
        payload=[evidence],
        trace_id="trace",
    )


class TestCheckpointMsgpackAllowlist:
    def test_pipeline_state_types_round_trip_without_warning(self, caplog):
        serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
        evidence = _evidence()
        objects = [evidence, _core_claim(), _lens_verdict(), _agent_message(evidence)]

        with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
            for obj in objects:
                typ, packed = serde.dumps_typed(obj)
                restored = serde.loads_typed((typ, packed))
                assert restored == obj

        assert caplog.records == []

    def test_unallowlisted_type_still_warns(self, caplog):
        """Sanity check: the default (no explicit allowlist) serializer warns —
        confirms the allowlist above is what's actually suppressing the warning,
        not some property of the test fixtures."""
        serde = JsonPlusSerializer()
        obj = _evidence()

        with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
            typ, packed = serde.dumps_typed(obj)
            serde.loads_typed((typ, packed))

        assert any("Deserializing unregistered type" in r.message for r in caplog.records)
