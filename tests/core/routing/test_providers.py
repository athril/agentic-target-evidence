# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ModelProvider implementations."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic as anthropic_sdk
import openai as openai_sdk
import pytest
from anthropic.types import TextBlock, ThinkingBlock

from core.routing.providers._claude import accepts_sampling_params
from core.routing.providers.anthropic import AnthropicProvider
from core.routing.providers.azure import AzureProvider
from core.routing.providers.base import CompletionRequest, CompletionResult, ModelProvider
from core.routing.providers.bedrock import BedrockProvider
from core.routing.providers.google import GoogleProvider
from core.routing.providers.ollama import OllamaProvider
from core.routing.providers.openai import OpenAIProvider
from schemas.evidence import DataClass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _non_sensitive_req(**kwargs) -> CompletionRequest:
    return CompletionRequest(
        messages=[{"role": "user", "content": "hello"}],
        classification=DataClass.NON_SENSITIVE,
        task="test",
        **kwargs,
    )


def _sensitive_req(**kwargs) -> CompletionRequest:
    return CompletionRequest(
        messages=[{"role": "user", "content": "hello"}],
        classification=DataClass.SENSITIVE,
        task="test",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# ModelProvider Protocol
# ---------------------------------------------------------------------------


def test_ollama_is_a_model_provider() -> None:
    provider = OllamaProvider(model="qwen2.5:7b")
    assert isinstance(provider, ModelProvider)


def test_bedrock_is_a_model_provider() -> None:
    provider = BedrockProvider(
        model="anthropic.claude-3-5-sonnet-20241022-v2:0", region="us-east-1"
    )
    assert isinstance(provider, ModelProvider)


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------


def test_ollama_supports_all_data_classes() -> None:
    provider = OllamaProvider(model="qwen2.5:7b")
    assert provider.supports(DataClass.SENSITIVE) is True
    assert provider.supports(DataClass.NON_SENSITIVE) is True


def test_ollama_name() -> None:
    assert OllamaProvider(model="qwen2.5:7b").name == "ollama"


@pytest.mark.asyncio
async def test_ollama_complete_returns_result() -> None:
    mock_response = MagicMock()
    mock_response.message.content = "test reply"
    mock_response.prompt_eval_count = 10
    mock_response.eval_count = 5

    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)

        provider = OllamaProvider(model="qwen2.5:7b")
        provider._client = instance

        result = await provider.complete(_non_sensitive_req())

    assert isinstance(result, CompletionResult)
    assert result.content == "test reply"
    assert result.model_used == "qwen2.5:7b"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "seed"), [(None, 42), ("7", 7), ("x", 42)])
async def test_ollama_sends_a_fixed_seed(monkeypatch, env, seed) -> None:
    """A fixed seed removes the sampler as a source of run-to-run variation."""
    if env is None:
        monkeypatch.delenv("OLLAMA_SEED", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_SEED", env)
    mock_response = MagicMock()
    mock_response.message.content = "ok"
    mock_response.prompt_eval_count = 1
    mock_response.eval_count = 1
    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)
        provider = OllamaProvider(model="qwen2.5:7b")
        provider._client = instance
        await provider.complete(_non_sensitive_req())
    assert instance.chat.await_args.kwargs["options"]["seed"] == seed


@pytest.mark.asyncio
@pytest.mark.parametrize("think", [None, False, True])
async def test_ollama_forwards_the_think_switch(think) -> None:
    """A thinking model spends its trace from num_predict, so the switch must reach Ollama."""
    mock_response = MagicMock()
    mock_response.message.content = "ok"
    mock_response.prompt_eval_count = 1
    mock_response.eval_count = 1
    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)
        instance.generate = AsyncMock()
        provider = OllamaProvider(model="qwen3.5:9b", think=think)
        provider._client = instance
        await provider.complete(_non_sensitive_req())
        await provider.warmup()
    assert instance.chat.await_args.kwargs["think"] is think
    assert instance.generate.await_args.kwargs["think"] is think


@pytest.mark.asyncio
async def test_ollama_complete_includes_system_message() -> None:
    mock_response = MagicMock()
    mock_response.message.content = "ok"
    mock_response.prompt_eval_count = 5
    mock_response.eval_count = 2

    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)

        provider = OllamaProvider(model="qwen2.5:7b")
        provider._client = instance

        req = _non_sensitive_req(system="You are a helpful assistant.")
        await provider.complete(req)

    call_args = instance.chat.call_args
    messages_sent = call_args.kwargs["messages"]
    assert messages_sent[0] == {"role": "system", "content": "You are a helpful assistant."}


@pytest.mark.asyncio
async def test_ollama_embed_uses_nomic_model() -> None:
    mock_response = MagicMock()
    mock_response.embeddings = [[0.1, 0.2, 0.3]]

    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.embed = AsyncMock(return_value=mock_response)

        provider = OllamaProvider(model="qwen2.5:7b", embed_model="nomic-embed-text:latest")
        provider._client = instance

        result = await provider.embed(["hello world"])

    instance.embed.assert_called_once_with(model="nomic-embed-text:latest", input=["hello world"])
    assert result == [[0.1, 0.2, 0.3]]


@pytest.mark.asyncio
async def test_ollama_embed_always_uses_embed_model_not_reasoning_model() -> None:
    """embed() must use nomic-embed-text regardless of the configured reasoning model."""
    mock_response = MagicMock()
    mock_response.embeddings = [[0.0]]

    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.embed = AsyncMock(return_value=mock_response)

        provider = OllamaProvider(model="some-huge-reasoning-model:70b")
        provider._embed_model = "nomic-embed-text:latest"
        provider._client = instance

        await provider.embed(["text"])

    call_model = instance.embed.call_args.kwargs["model"]
    assert call_model == "nomic-embed-text:latest"
    assert "some-huge-reasoning-model" not in call_model


# ---------------------------------------------------------------------------
# BedrockProvider
# ---------------------------------------------------------------------------


def test_bedrock_supports_only_non_sensitive() -> None:
    provider = BedrockProvider(model="anthropic.claude", region="us-east-1")
    assert provider.supports(DataClass.NON_SENSITIVE) is True
    assert provider.supports(DataClass.SENSITIVE) is False


def test_bedrock_name() -> None:
    assert BedrockProvider(model="anthropic.claude", region="us-east-1").name == "bedrock"


@pytest.mark.asyncio
async def test_bedrock_complete_raises_on_sensitive_input() -> None:
    provider = BedrockProvider(model="anthropic.claude", region="us-east-1")
    with pytest.raises(ValueError, match="SENSITIVE"):
        await provider.complete(_sensitive_req())


@pytest.mark.asyncio
async def test_bedrock_complete_non_sensitive_returns_result() -> None:
    fake_body = json.dumps(
        {
            "content": [{"text": "bedrock reply"}],
            "usage": {"input_tokens": 20, "output_tokens": 8},
        }
    ).encode()

    mock_response_obj = MagicMock()
    mock_response_obj.__aenter__ = AsyncMock(return_value=mock_response_obj)
    mock_response_obj.__aexit__ = AsyncMock(return_value=False)
    mock_response_obj.invoke_model = AsyncMock(
        return_value={"body": AsyncMock(read=AsyncMock(return_value=fake_body))}
    )

    mock_session = MagicMock()
    mock_session.client.return_value = mock_response_obj

    with patch("core.routing.providers.bedrock.aioboto3.Session", return_value=mock_session):
        provider = BedrockProvider(model="anthropic.claude", region="us-east-1")
        result = await provider.complete(_non_sensitive_req())

    assert result.content == "bedrock reply"
    assert result.input_tokens == 20
    assert result.output_tokens == 8
    assert result.model_used == "anthropic.claude"


@pytest.mark.asyncio
async def test_bedrock_embed_raises_not_implemented() -> None:
    provider = BedrockProvider(model="anthropic.claude", region="us-east-1")
    with pytest.raises(NotImplementedError, match="OllamaProvider"):
        await provider.embed(["text"])


# ---------------------------------------------------------------------------
# AzureProvider (bonus — implemented alongside the Bedrock provider)
# ---------------------------------------------------------------------------


def test_azure_supports_only_non_sensitive() -> None:
    with patch("core.routing.providers.azure.AsyncAzureOpenAI"):
        provider = AzureProvider(
            deployment="gpt-4o", endpoint="https://example.azure.com", api_key="k"
        )
    assert provider.supports(DataClass.NON_SENSITIVE) is True
    assert provider.supports(DataClass.SENSITIVE) is False


@pytest.mark.asyncio
async def test_azure_complete_raises_on_sensitive_input() -> None:
    with patch("core.routing.providers.azure.AsyncAzureOpenAI"):
        provider = AzureProvider(
            deployment="gpt-4o", endpoint="https://example.azure.com", api_key="k"
        )
    with pytest.raises(ValueError, match="SENSITIVE"):
        await provider.complete(_sensitive_req())


@pytest.mark.asyncio
async def test_azure_complete_non_sensitive_returns_result() -> None:
    mock_choice = MagicMock()
    mock_choice.message.content = "azure reply"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 15
    mock_usage.completion_tokens = 6
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_completion.usage = mock_usage

    with patch("core.routing.providers.azure.AsyncAzureOpenAI") as MockAzure:
        instance = MockAzure.return_value
        instance.chat = MagicMock()
        instance.chat.completions = MagicMock()
        instance.chat.completions.create = AsyncMock(return_value=mock_completion)

        provider = AzureProvider(
            deployment="gpt-4o", endpoint="https://example.azure.com", api_key="k"
        )
        result = await provider.complete(_non_sensitive_req())

    assert result.content == "azure reply"
    assert result.input_tokens == 15
    assert result.output_tokens == 6


@pytest.mark.asyncio
async def test_azure_embed_raises_not_implemented() -> None:
    with patch("core.routing.providers.azure.AsyncAzureOpenAI"):
        provider = AzureProvider(
            deployment="gpt-4o", endpoint="https://example.azure.com", api_key="k"
        )
    with pytest.raises(NotImplementedError, match="OllamaProvider"):
        await provider.embed(["text"])


# ---------------------------------------------------------------------------
# GoogleProvider
# ---------------------------------------------------------------------------


def test_google_is_a_model_provider() -> None:
    with patch("core.routing.providers.google.genai.Client"):
        provider = GoogleProvider(api_key="k")
    assert isinstance(provider, ModelProvider)


def test_google_supports_only_non_sensitive() -> None:
    with patch("core.routing.providers.google.genai.Client"):
        provider = GoogleProvider(api_key="k")
    assert provider.supports(DataClass.NON_SENSITIVE) is True
    assert provider.supports(DataClass.SENSITIVE) is False


def test_google_name() -> None:
    with patch("core.routing.providers.google.genai.Client"):
        assert GoogleProvider(api_key="k").name == "google"


@pytest.mark.asyncio
async def test_google_complete_raises_on_sensitive_input() -> None:
    with patch("core.routing.providers.google.genai.Client"):
        provider = GoogleProvider(api_key="k")
    with pytest.raises(ValueError, match="SENSITIVE"):
        await provider.complete(_sensitive_req())


@pytest.mark.asyncio
async def test_google_complete_non_sensitive_returns_result() -> None:
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 12
    mock_usage.candidates_token_count = 4
    mock_response = MagicMock()
    mock_response.text = "gemini reply"
    mock_response.usage_metadata = mock_usage

    with patch("core.routing.providers.google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.aio.models.generate_content = AsyncMock(return_value=mock_response)

        provider = GoogleProvider(api_key="k", model="gemini-2.5-pro")
        result = await provider.complete(_non_sensitive_req())

    assert result.content == "gemini reply"
    assert result.model_used == "gemini-2.5-pro"
    assert result.input_tokens == 12
    assert result.output_tokens == 4


@pytest.mark.asyncio
async def test_google_complete_maps_assistant_role_to_model() -> None:
    mock_usage = MagicMock(prompt_token_count=1, candidates_token_count=1)
    mock_response = MagicMock(text="ok", usage_metadata=mock_usage)

    with patch("core.routing.providers.google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.aio.models.generate_content = AsyncMock(return_value=mock_response)

        provider = GoogleProvider(api_key="k")
        req = CompletionRequest(
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            classification=DataClass.NON_SENSITIVE,
            task="test",
        )
        await provider.complete(req)

    call_kwargs = instance.aio.models.generate_content.call_args.kwargs
    roles = [c["role"] for c in call_kwargs["contents"]]
    assert roles == ["user", "model"]


@pytest.mark.asyncio
async def test_google_embed_raises_not_implemented() -> None:
    with patch("core.routing.providers.google.genai.Client"):
        provider = GoogleProvider(api_key="k")
    with pytest.raises(NotImplementedError, match="OllamaProvider"):
        await provider.embed(["text"])


# ---------------------------------------------------------------------------
# Claude request/response helpers (Anthropic + Bedrock)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "accepts"),
    [
        ("claude-sonnet-4-6", True),
        ("claude-opus-4-6", True),
        ("claude-haiku-4-5", True),
        ("claude-sonnet-4-5-20250929", True),
        ("claude-opus-4-20250514", True),
        ("claude-3-5-sonnet-20241022", True),
        ("anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", True),
        ("claude-opus-4-7", False),
        ("claude-opus-4-8", False),
        ("claude-sonnet-5", False),
        ("claude-sonnet-5-5", False),
        ("claude-opus-5-5", False),
        ("claude-fable-5-1", False),
        ("anthropic.claude-opus-5-5", False),
    ],
)
def test_accepts_sampling_params(model: str, accepts: bool) -> None:
    assert accepts_sampling_params(model) is accepts


def _anthropic_response(blocks: list) -> MagicMock:
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = "end_turn"
    resp.usage.input_tokens = 3
    resp.usage.output_tokens = 2
    return resp


@pytest.mark.parametrize(
    ("model", "sends_temperature"),
    [("claude-sonnet-4-6", True), ("claude-opus-4-8", False)],
)
@pytest.mark.asyncio
async def test_anthropic_temperature_only_for_models_that_accept_it(
    model: str, sends_temperature: bool
) -> None:
    provider = AnthropicProvider(api_key="test")
    create = AsyncMock(return_value=_anthropic_response([TextBlock(type="text", text="ok")]))
    provider._client.messages.create = create  # type: ignore[method-assign]

    await provider.complete(_non_sensitive_req(model_override=model))

    kwargs = create.call_args.kwargs
    assert kwargs["model"] == model
    assert (kwargs["temperature"] is not anthropic_sdk.omit) is sends_temperature


@pytest.mark.asyncio
async def test_anthropic_reads_text_after_a_thinking_block() -> None:
    provider = AnthropicProvider(api_key="test")
    blocks = [
        ThinkingBlock(type="thinking", thinking="", signature="sig"),
        TextBlock(type="text", text='{"verdict": "go"}'),
    ]
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_anthropic_response(blocks)
    )

    result = await provider.complete(_non_sensitive_req())

    assert result.content == '{"verdict": "go"}'


@pytest.mark.asyncio
async def test_bedrock_honours_model_override_and_omits_unsupported_temperature() -> None:
    fake_body = json.dumps(
        {
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "bedrock reply"},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).encode()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.invoke_model = AsyncMock(
        return_value={"body": AsyncMock(read=AsyncMock(return_value=fake_body))}
    )
    session = MagicMock()
    session.client.return_value = client

    with patch("core.routing.providers.bedrock.aioboto3.Session", return_value=session):
        provider = BedrockProvider(model="anthropic.claude-sonnet-4-6", region="us-east-1")
        result = await provider.complete(
            _non_sensitive_req(model_override="anthropic.claude-opus-4-8")
        )

    kwargs = client.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "anthropic.claude-opus-4-8"
    assert "temperature" not in json.loads(kwargs["body"])
    assert result.content == "bedrock reply"
    assert result.model_used == "anthropic.claude-opus-4-8"


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "sends_temperature"),
    [("gpt-4.1", True), ("gpt-5", False), ("o4-mini", False)],
)
@pytest.mark.asyncio
async def test_openai_request_params_per_model(model: str, sends_temperature: bool) -> None:
    provider = OpenAIProvider(api_key="test")
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "ok"
    resp.usage.prompt_tokens = 1
    resp.usage.completion_tokens = 1
    create = AsyncMock(return_value=resp)
    provider._client.chat.completions.create = create  # type: ignore[method-assign]

    result = await provider.complete(_non_sensitive_req(model_override=model))

    kwargs = create.call_args.kwargs
    assert "max_tokens" not in kwargs
    assert kwargs["max_completion_tokens"] == 4096
    assert (kwargs["temperature"] is not openai_sdk.omit) is sends_temperature
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_ollama_reports_the_prompt_size_to_the_token_estimator() -> None:
    from core.tokens import get_estimator

    mock_response = MagicMock()
    mock_response.message.content = "ok"
    mock_response.prompt_eval_count = 1000
    mock_response.eval_count = 1
    with patch("core.routing.providers.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)
        provider = OllamaProvider(model="ratio-model")
        provider._client = instance
        for _ in range(3):  # a task's ratio is trusted from three samples
            await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": "y" * 3500}],
                    classification=DataClass.NON_SENSITIVE,
                    task="test",
                )
            )
    assert get_estimator().ratio("ratio-model", "test") == 3.5
