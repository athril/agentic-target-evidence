# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time
from typing import Any, cast

from google import genai
from google.genai import types as genai_types

from core.telemetry.setup import get_tracer
from schemas.evidence import DataClass

from .base import CompletionRequest, CompletionResult

_SENSITIVE_ERROR = (
    "GoogleProvider does not handle SENSITIVE data. "
    "Check your routing policy — SENSITIVE requests must route to ollama."
)


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map OpenAI/Anthropic-style {role, content} messages to Gemini contents.

    Gemini has no "assistant" role — prior model turns are role "model".
    """
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else m["role"]
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    return contents


class GoogleProvider:
    name = "google"

    def __init__(self, *, api_key: str, model: str = "gemini-2.5-pro") -> None:
        self._model = model
        self._client = genai.Client(api_key=api_key)

    def supports(self, classification: DataClass) -> bool:
        return classification == DataClass.NON_SENSITIVE

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        if req.classification == DataClass.SENSITIVE:
            raise ValueError(_SENSITIVE_ERROR)

        model = req.model_override or self._model
        contents = _to_gemini_contents(req.messages)
        config = genai_types.GenerateContentConfig(
            system_instruction=req.system,
            max_output_tokens=req.max_tokens,
            temperature=req.temperature,
        )

        tracer = get_tracer()
        with tracer.start_as_current_span(f"google.{req.task or 'complete'}") as gen_span:
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

            gen_span.set_attribute("gen_ai.system", "google")
            gen_span.set_attribute("gen_ai.request.model", model)

            t0 = time.monotonic()
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=cast("genai_types.ContentListUnionDict", contents),
                config=config,
            )
            latency_ms = (time.monotonic() - t0) * 1000

        usage = response.usage_metadata
        result = CompletionResult(
            content=response.text or "",
            model_used=model,
            input_tokens=usage.prompt_token_count if usage else 0,
            output_tokens=usage.candidates_token_count if usage else 0,
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
            "GoogleProvider does not support embeddings. "
            "Embeddings must use OllamaProvider with nomic-embed-text."
        )
