# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core.telemetry.langfuse import record_token_cost, span

# OTel only allows set_tracer_provider once per process.  Set it up once at
# session scope and share the exporter; each test clears it before running.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
otel_trace.set_tracer_provider(_PROVIDER)


@pytest.fixture(autouse=True)
def _otel_in_memory() -> InMemorySpanExporter:
    _EXPORTER.clear()
    return _EXPORTER


async def test_span_sets_trace_id_attribute(_otel_in_memory: InMemorySpanExporter) -> None:
    async with span("test-op", trace_id="abc123"):
        pass

    finished = _otel_in_memory.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].attributes["langfuse.trace_id"] == "abc123"


async def test_span_records_latency_ms(_otel_in_memory: InMemorySpanExporter) -> None:
    async with span("timed-op", trace_id="t1"):
        pass

    finished = _otel_in_memory.get_finished_spans()
    assert "latency_ms" in finished[0].attributes
    assert finished[0].attributes["latency_ms"] >= 0.0


async def test_span_records_exception_on_error(_otel_in_memory: InMemorySpanExporter) -> None:
    with pytest.raises(ValueError):
        async with span("failing-op", trace_id="t2"):
            raise ValueError("boom")

    finished = _otel_in_memory.get_finished_spans()
    assert finished[0].status.status_code == otel_trace.StatusCode.ERROR
    assert len(finished[0].events) >= 1  # at least one exception event


async def test_span_records_latency_even_on_error(_otel_in_memory: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError):
        async with span("fail-timed", trace_id="t3"):
            raise RuntimeError("fail")

    finished = _otel_in_memory.get_finished_spans()
    assert "latency_ms" in finished[0].attributes


async def test_span_sets_extra_attributes(_otel_in_memory: InMemorySpanExporter) -> None:
    async with span("attrs-op", trace_id="t4", attributes={"run_id": "r1", "agent": "literature"}):
        pass

    attrs = _otel_in_memory.get_finished_spans()[0].attributes
    assert attrs["run_id"] == "r1"
    assert attrs["agent"] == "literature"


def test_record_token_cost(_otel_in_memory: InMemorySpanExporter) -> None:
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("llm-call") as s:
        record_token_cost(
            s, input_tokens=100, output_tokens=50, model="qwen2.5:7b", output_data="ok"
        )

    attrs = _otel_in_memory.get_finished_spans()[0].attributes
    assert attrs["llm.usage.prompt_tokens"] == 100
    assert attrs["llm.usage.completion_tokens"] == 50
    assert attrs["llm.usage.total_tokens"] == 150
    assert attrs["llm.model"] == "qwen2.5:7b"
