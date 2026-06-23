# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time

import openai as openai_sdk

from core.telemetry.setup import get_tracer
from schemas.evidence import DataClass

from .base import CompletionRequest, CompletionResult

_SENSITIVE_ERROR = (
    "OpenAIProvider does not handle SENSITIVE data. "
    "Check your routing policy — SENSITIVE requests must route to ollama."
)


class OpenAIProvider:
    name = "openai"

    def __init__(self, *, api_key: str, model: str = "gpt-4.1") -> None:
        self._model = model
        self._client = openai_sdk.AsyncOpenAI(api_key=api_key)

    def supports(self, classification: DataClass) -> bool:
        return classification == DataClass.NON_SENSITIVE

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        if req.classification == DataClass.SENSITIVE:
            raise ValueError(_SENSITIVE_ERROR)

        model = req.model_override or self._model

        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.extend(req.messages)

        tracer = get_tracer()
        with tracer.start_as_current_span(f"openai.{req.task or 'complete'}") as gen_span:
            try:
                from langfuse import LangfuseOtelSpanAttributes

                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_TYPE, "generation")
                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_MODEL, model)
                _input_json = json.dumps(messages, ensure_ascii=False)
                gen_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_INPUT,
                    _input_json[:20_000],
                )
            except Exception:
                pass

            gen_span.set_attribute("gen_ai.system", "openai")
            gen_span.set_attribute("gen_ai.request.model", model)

            t0 = time.monotonic()
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            latency_ms = (time.monotonic() - t0) * 1000

        choice = response.choices[0]
        usage = response.usage
        result = CompletionResult(
            content=choice.message.content or "",
            model_used=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
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
            "OpenAIProvider does not support embeddings. "
            "Embeddings must use OllamaProvider with nomic-embed-text."
        )
