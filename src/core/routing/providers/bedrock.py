# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time
from typing import Any

import aioboto3

from schemas.evidence import DataClass

from .base import CompletionRequest, CompletionResult

# Bedrock is never allowed to handle SENSITIVE data (architecture §3 + CLAUDE.md rule 4)
_BEDROCK_SENSITIVE_ERROR = (
    "BedrockProvider does not support SENSITIVE data. "
    "Check your routing policy — SENSITIVE requests must route to ollama."
)


class BedrockProvider:
    name = "bedrock"

    def __init__(self, *, model: str, region: str) -> None:
        self._model = model
        self._region = region

    def supports(self, classification: DataClass) -> bool:
        return classification == DataClass.NON_SENSITIVE

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        if req.classification == DataClass.SENSITIVE:
            raise ValueError(_BEDROCK_SENSITIVE_ERROR)

        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": req.messages,
        }
        if req.system:
            body["system"] = req.system

        t0 = time.monotonic()
        session = aioboto3.Session()
        async with session.client("bedrock-runtime", region_name=self._region) as client:
            response = await client.invoke_model(
                modelId=self._model,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body),
            )
            raw = await response["body"].read()
        latency_ms = (time.monotonic() - t0) * 1000

        data = json.loads(raw)
        content = data["content"][0]["text"]
        usage = data.get("usage", {})
        return CompletionResult(
            content=content,
            model_used=self._model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=latency_ms,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Embeddings are always local (CLAUDE.md rule 5) — this should never be called.
        raise NotImplementedError(
            "BedrockProvider does not support embeddings. "
            "Embeddings must use OllamaProvider with nomic-embed-text."
        )
