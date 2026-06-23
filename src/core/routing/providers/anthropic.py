# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time

import anthropic as anthropic_sdk

from core.telemetry.setup import get_tracer
from schemas.evidence import DataClass

from .base import CompletionRequest, CompletionResult

_SENSITIVE_ERROR = (
    "AnthropicProvider does not handle SENSITIVE data. "
    "Check your routing policy — SENSITIVE requests must route to ollama."
)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._model = model
        self._client = anthropic_sdk.AsyncAnthropic(api_key=api_key)

    def supports(self, classification: DataClass) -> bool:
        return classification == DataClass.NON_SENSITIVE

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        if req.classification == DataClass.SENSITIVE:
            raise ValueError(_SENSITIVE_ERROR)

        model = req.model_override or self._model

        tracer = get_tracer()
        with tracer.start_as_current_span(f"anthropic.{req.task or 'complete'}") as gen_span:
            try:
                from langfuse import LangfuseOtelSpanAttributes

                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_TYPE, "generation")
                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_MODEL, model)
                _input_json = json.dumps(req.messages, ensure_ascii=False)
                gen_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_INPUT,
                    _input_json[:20_000],
                )
            except Exception:
                pass

            gen_span.set_attribute("gen_ai.system", "anthropic")
            gen_span.set_attribute("gen_ai.request.model", model)

            t0 = time.monotonic()
            response = await self._client.messages.create(
                model=model,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                system=req.system if req.system is not None else anthropic_sdk.omit,
                messages=req.messages,  # type: ignore[arg-type]
            )
            latency_ms = (time.monotonic() - t0) * 1000

        first_block = response.content[0] if response.content else None
        content = first_block.text if isinstance(first_block, anthropic_sdk.types.TextBlock) else ""
        usage = response.usage
        result = CompletionResult(
            content=content,
            model_used=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
        )

        try:
            from langfuse import LangfuseOtelSpanAttributes

            gen_span.set_attribute(
                LangfuseOtelSpanAttributes.OBSERVATION_OUTPUT, result.content[:20_000]
            )
            gen_span.set_attribute(
                LangfuseOtelSpanAttributes.OBSERVATION_USAGE_DETAILS,
                json.dumps({"input": result.input_tokens, "output": result.output_tokens}),
            )
            gen_span.set_attribute(
                f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.latency_ms",
                str(round(latency_ms, 2)),
            )
            gen_span.set_attribute(
                f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.task",
                req.task or "",
            )
        except Exception:
            pass

        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "AnthropicProvider does not support embeddings. "
            "Embeddings must use OllamaProvider with nomic-embed-text."
        )
