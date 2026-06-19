# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import logging
import time

from langfuse import LangfuseOtelSpanAttributes
from ollama import AsyncClient

from core.telemetry.setup import get_tracer
from schemas.evidence import DataClass

from .base import CompletionRequest, CompletionResult

logger = logging.getLogger(__name__)

# Single semaphore shared across all OllamaProvider instances — enforces the
# one-GPU-slot constraint under the all_local policy (see architecture §3).
_gpu_slot: asyncio.Semaphore = asyncio.Semaphore(1)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        embed_model: str = "nomic-embed-text:latest",
        base_url: str = "http://ollama:11434",
        num_ctx: int = 16384,
        timeout: float | None = None,
    ) -> None:
        self._model = model
        self._embed_model = embed_model
        self._client = AsyncClient(host=base_url, timeout=timeout)
        self._num_ctx = num_ctx

    def supports(self, classification: DataClass) -> bool:
        # Handles all classifications — it is the fallback for sensitive data
        return True

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.extend(req.messages)

        t_queued = time.monotonic()
        async with _gpu_slot:
            queue_wait_ms = (time.monotonic() - t_queued) * 1000
            # Span is opened only after acquiring the slot so that parallel lens
            # coroutines waiting on _gpu_slot don't nest their spans under the
            # holder's active OTel context.
            tracer = get_tracer()
            with tracer.start_as_current_span(f"ollama.{req.task or 'complete'}") as gen_span:
                gen_span.set_attribute("gen_ai.system", "ollama")
                gen_span.set_attribute("gen_ai.request.model", self._model)
                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_TYPE, "generation")
                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_MODEL, self._model)
                _input_json = json.dumps(messages, ensure_ascii=False)
                _display_limit = 20_000
                _stored_input = (
                    _input_json[:_display_limit] + "\n… [TRUNCATED for Langfuse display]"
                    if len(_input_json) > _display_limit
                    else _input_json
                )
                gen_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_INPUT, _stored_input)

                logger.info(
                    "[ollama] %-20s → %s (messages=%d)", req.task, self._model, len(messages)
                )
                t0 = time.monotonic()
                response = await self._client.chat(
                    model=self._model,
                    messages=messages,
                    options={
                        "temperature": req.temperature,
                        "num_predict": req.max_tokens,
                        "num_ctx": self._num_ctx,
                    },
                )
                latency_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "[ollama] %-20s ← %s  %.0f ms  in=%d out=%d tokens",
                    req.task,
                    self._model,
                    latency_ms,
                    response.prompt_eval_count or 0,
                    response.eval_count or 0,
                )

                msg = response.message
                result = CompletionResult(
                    content=msg.content or "",
                    model_used=self._model,
                    input_tokens=response.prompt_eval_count or 0,
                    output_tokens=response.eval_count or 0,
                    latency_ms=latency_ms,
                )

                gen_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_OUTPUT,
                    result.content[:20_000],
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
                    f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.queue_wait_ms",
                    str(round(queue_wait_ms, 2)),
                )
                gen_span.set_attribute(
                    f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.task",
                    req.task or "",
                )

        return result

    async def warmup(self) -> None:
        """Load the model into GPU/RAM.

        Sends a trivial 1-token generation so the first real inference call
        doesn't pay the cold-start penalty inside a timed section.
        """
        logger.info("[ollama] warming up %s ...", self._model)
        t0 = time.monotonic()
        await self._client.generate(
            model=self._model,
            prompt="hi",
            options={"num_predict": 1, "num_ctx": 512},
        )
        logger.info("[ollama] warmup done in %.0f ms", (time.monotonic() - t0) * 1000)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Embeddings always use nomic-embed-text regardless of the reasoning model.
        # They do NOT go through _gpu_slot — embedding is much lighter than generation.
        response = await self._client.embed(model=self._embed_model, input=texts)
        return response.embeddings
