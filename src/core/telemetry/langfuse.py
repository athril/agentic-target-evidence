# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from .setup import get_tracer


@asynccontextmanager
async def span(
    name: str,
    trace_id: str,
    *,
    input_data: str | None = None,  # Explicitly accept string/object inputs
    attributes: dict[str, Any] | None = None,
) -> AsyncGenerator[Span, None]:
    """Async context manager that wraps an operation in a Langfuse-compatible OTel span."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as current_span:
        current_span.set_attribute("langfuse.trace_id", trace_id)

        if input_data:
            current_span.set_attribute("input", input_data)

        if attributes:
            for k, v in attributes.items():
                current_span.set_attribute(k, str(v))

        t0 = time.monotonic()
        try:
            yield current_span
        except Exception as exc:
            current_span.record_exception(exc)
            current_span.set_status(trace.StatusCode.ERROR, str(exc))
            raise
        finally:
            latency_ms = (time.monotonic() - t0) * 1000
            current_span.set_attribute("latency_ms", round(latency_ms, 2))


def record_token_cost(
    current_span: Span,
    *,
    input_tokens: int,
    output_tokens: int,
    model: str,
    output_data: str,  # <-- Added output content parameter
) -> None:
    """Attach token-cost and response content attributes for Langfuse mapping."""
    current_span.set_attribute("llm.usage.prompt_tokens", input_tokens)
    current_span.set_attribute("llm.usage.completion_tokens", output_tokens)
    current_span.set_attribute("llm.usage.total_tokens", input_tokens + output_tokens)
    current_span.set_attribute("llm.model", model)

    # Langfuse requires completion content to populate trace outputs natively
    current_span.set_attribute("gen_ai.completion", output_data)
