# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from opentelemetry import trace

_initialized = False


def init_telemetry() -> None:
    """Initialize Langfuse as the OpenTelemetry TracerProvider.

    Reads credentials and host from environment variables:
      LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL (or LANGFUSE_HOST)

    Exports spans that are:
      - Created by the Langfuse SDK tracer (get_client().start_as_current_observation)
      - Annotated with any gen_ai.* semantic-convention attribute
      - From a known LLM instrumentor (LangChain, OpenAI SDK, etc.)
      - From our own "gene-target-validation" instrumentation scope (agent harness spans)

    Safe to call multiple times; only the first call takes effect.
    """
    global _initialized
    if _initialized:
        return

    from langfuse import Langfuse
    from langfuse.span_filter import is_default_export_span

    Langfuse(
        should_export_span=lambda span: (
            is_default_export_span(span)
            or (
                span.instrumentation_scope is not None
                and span.instrumentation_scope.name == "gene-target-validation"
            )
        ),
    )
    _initialized = True


def get_tracer(name: str = "gene-target-validation") -> trace.Tracer:
    """Return a tracer. init_telemetry() must have been called first."""
    return trace.get_tracer(name)
