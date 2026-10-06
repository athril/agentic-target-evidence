# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""build_router(): provider registration and startup validation per routing policy."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from agents.synthesis.report.provenance import REASONING_STAGES
from core.routing.factory import build_router
from core.routing.policy import RoutingPolicy, _load
from core.routing.router import NoProviderError
from schemas.evidence import DataClass

_CLOUD_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "BEDROCK_MODEL_ID",
    "AWS_REGION",
    "BEDROCK_REGION",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_KEY",
)
# Stages that go through the router (the investigator drives its own Ollama loop).
_ROUTED_STAGES = tuple(s for s in REASONING_STAGES if s != "investigator")


@pytest.fixture(autouse=True)
def _no_cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _CLOUD_ENV:
        monkeypatch.delenv(var, raising=False)


def _shipped_policy(policy: str) -> RoutingPolicy:
    """config/routing.yaml as shipped, with only the policy line switched."""
    loaded = _load(Path("config/routing.yaml"))
    return dataclasses.replace(loaded, policy=policy)


def test_shipped_default_policy_is_all_local() -> None:
    assert _load(Path("config/routing.yaml")).policy == "all_local"


def test_all_local_registers_only_ollama_even_with_cloud_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    router = build_router(_shipped_policy("all_local"))
    assert router.get_provider("anthropic") is None
    for stage in _ROUTED_STAGES:
        assert router.select(DataClass.NON_SENSITIVE, stage)[0].name == "ollama"


def test_hybrid_lists_every_routed_stage() -> None:
    # A stage missing from the block falls through to the fallback rules (bedrock),
    # which crashes mid-run when only an Anthropic key is configured.
    block = _shipped_policy("hybrid").agent_models["hybrid"]
    assert set(_ROUTED_STAGES) <= set(block)


def test_hybrid_with_anthropic_key_routes_each_stage_as_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    policy = _shipped_policy("hybrid")
    router = build_router(policy)
    block = policy.agent_models["hybrid"]
    for stage in _ROUTED_STAGES:
        provider, model = router.select(DataClass.NON_SENSITIVE, stage)
        assert f"{provider.name}/{model}" == block[stage], stage


def test_hybrid_keeps_sensitive_data_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    router = build_router(_shipped_policy("hybrid"))
    assert router.select(DataClass.SENSITIVE, "critic")[0].name == "ollama"


def test_hybrid_without_anthropic_key_fails_at_startup() -> None:
    with pytest.raises(NoProviderError, match="ANTHROPIC_API_KEY") as exc:
        build_router(_shipped_policy("hybrid"))
    assert "critic → anthropic" in str(exc.value)


def test_unexpanded_placeholder_is_not_a_credential() -> None:
    # ${ANTHROPIC_API_KEY} stays literal when the env var is unset.
    policy = _shipped_policy("hybrid")
    assert policy.providers["anthropic"].api_key == "${ANTHROPIC_API_KEY}"
    with pytest.raises(NoProviderError):
        build_router(policy)


def test_bedrock_registers_from_aws_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    router = build_router(_shipped_policy("hybrid"))
    bedrock = router.get_provider("bedrock")
    assert bedrock is not None
    assert bedrock._region == "us-east-1"  # type: ignore[attr-defined]


def test_ollama_base_url_override() -> None:
    router = build_router(_shipped_policy("all_local"), ollama_base_url="http://localhost:11434")
    ollama = router.get_provider("ollama")
    assert ollama is not None
    assert str(ollama._client._client.base_url).startswith("http://localhost:11434")  # type: ignore[attr-defined]
