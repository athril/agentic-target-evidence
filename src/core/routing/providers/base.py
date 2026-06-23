# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from schemas.evidence import DataClass


class CompletionRequest(BaseModel):
    messages: list[dict[str, Any]]
    system: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    classification: DataClass
    task: str
    model_override: str | None = None  # set by harness; provider uses this over its default


class CompletionResult(BaseModel):
    content: str
    model_used: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def supports(self, classification: DataClass) -> bool: ...

    async def complete(self, req: CompletionRequest) -> CompletionResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
