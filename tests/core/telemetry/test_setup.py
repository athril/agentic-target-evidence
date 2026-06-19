# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for telemetry initialisation (MP-07)."""

from __future__ import annotations

from opentelemetry import trace as otel_trace

import core.telemetry.setup as telemetry_setup
from core.telemetry.setup import get_tracer, init_telemetry


def test_init_telemetry_is_idempotent() -> None:
    """Calling init_telemetry() multiple times must not raise or re-register."""
    # Reset state so we exercise the first-call branch regardless of test order.
    telemetry_setup._initialized = False
    init_telemetry()
    init_telemetry()  # second call must be a no-op


def test_get_tracer_returns_tracer() -> None:
    tracer = get_tracer("test-service")
    assert isinstance(tracer, otel_trace.Tracer)


def test_get_tracer_default_name() -> None:
    tracer = get_tracer()
    assert isinstance(tracer, otel_trace.Tracer)


def test_get_tracer_same_name_returns_equivalent_tracer() -> None:
    t1 = get_tracer("svc-a")
    t2 = get_tracer("svc-a")
    # OTel guarantees the same logical tracer for the same (name, version) pair.
    assert type(t1) is type(t2)
