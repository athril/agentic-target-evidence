# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from openai import AsyncAzureOpenAI

from schemas.evidence import DataClass

from .base import CompletionRequest, CompletionResult


class AzureProvider:
    name = "azure"

    def __init__(self, *, deployment: str, endpoint: str, api_key: str) -> None:
        self._deployment = deployment
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-02-01",
        )

    def supports(self, classification: DataClass) -> bool:
        return classification == DataClass.NON_SENSITIVE

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        if req.classification == DataClass.SENSITIVE:
            raise ValueError(
                "AzureProvider does not support SENSITIVE data. Route to ollama instead."
            )

        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.extend(req.messages)

        t0 = time.monotonic()
        response = await self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        choice = response.choices[0]
        usage = response.usage
        return CompletionResult(
            content=choice.message.content or "",
            model_used=self._deployment,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "AzureProvider does not support embeddings. "
            "Embeddings must use OllamaProvider with nomic-embed-text."
        )
