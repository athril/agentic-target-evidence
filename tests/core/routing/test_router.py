# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.routing.policy import ProviderConfig, RoutingPolicy, RoutingRule
from core.routing.router import NoProviderError, Router
from schemas.evidence import DataClass


def _make_provider(name: str, supports_sensitive: bool = True) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.supports.side_effect = lambda cls: (
        True if supports_sensitive else cls == DataClass.NON_SENSITIVE
    )
    p.complete = AsyncMock()
    p.embed = AsyncMock()
    return p


def _hybrid_policy() -> RoutingPolicy:
    return RoutingPolicy(
        policy="hybrid",
        default_provider="bedrock",
        rules=[
            RoutingRule(classification="SENSITIVE", use="ollama"),
            RoutingRule(classification="NON_SENSITIVE", use="bedrock"),
        ],
        providers={
            "ollama": ProviderConfig(model="llama3"),
            "bedrock": ProviderConfig(model="claude-3"),
        },
    )


def _all_local_policy() -> RoutingPolicy:
    return RoutingPolicy(
        policy="all_local",
        default_provider="ollama",
        providers={"ollama": ProviderConfig(model="llama3")},
    )


def test_hybrid_routes_sensitive_to_ollama() -> None:
    ollama = _make_provider("ollama")
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(_hybrid_policy(), {"ollama": ollama, "bedrock": bedrock})

    provider, _ = router.select(DataClass.SENSITIVE, "hypothesis")
    assert provider.name == "ollama"


def test_hybrid_routes_non_sensitive_to_bedrock() -> None:
    ollama = _make_provider("ollama")
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(_hybrid_policy(), {"ollama": ollama, "bedrock": bedrock})

    provider, _ = router.select(DataClass.NON_SENSITIVE, "hypothesis")
    assert provider.name == "bedrock"


def test_all_local_routes_everything_to_ollama() -> None:
    ollama = _make_provider("ollama")
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(_all_local_policy(), {"ollama": ollama, "bedrock": bedrock})

    provider_s, _ = router.select(DataClass.SENSITIVE, "any")
    provider_n, _ = router.select(DataClass.NON_SENSITIVE, "any")
    assert provider_s.name == "ollama"
    assert provider_n.name == "ollama"


def test_all_local_raises_when_ollama_missing() -> None:
    router = Router(_all_local_policy(), {})  # no providers registered
    with pytest.raises(NoProviderError):
        router.select(DataClass.NON_SENSITIVE, "any")


def test_no_matching_rule_raises_no_provider_error() -> None:
    policy = RoutingPolicy(
        policy="custom",
        default_provider="nonexistent",
        rules=[],  # no rules
        providers={},
    )
    router = Router(policy, {})
    with pytest.raises(NoProviderError):
        router.select(DataClass.NON_SENSITIVE, "anything")


def test_falls_back_to_default_provider_when_no_rule_matches() -> None:
    policy = RoutingPolicy(
        policy="custom",
        default_provider="bedrock",
        rules=[RoutingRule(classification="SENSITIVE", use="ollama")],
        providers={"bedrock": ProviderConfig(model="claude-3")},
    )
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(policy, {"bedrock": bedrock})

    provider, _ = router.select(DataClass.NON_SENSITIVE, "anything")
    assert provider.name == "bedrock"


def test_agent_models_routes_by_agent_name() -> None:
    policy = RoutingPolicy(
        policy="hybrid",
        default_provider="bedrock",
        rules=[RoutingRule(classification="NON_SENSITIVE", use="bedrock")],
        providers={
            "ollama": ProviderConfig(model="qwen2.5:7b"),
            "bedrock": ProviderConfig(model="claude-3"),
        },
        agent_models={
            "hybrid": {
                "screening": "ollama/qwen2.5:7b-instruct-q4_K_M",
                "hypothesis": "bedrock/claude-3",
            }
        },
    )
    ollama = _make_provider("ollama")
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(policy, {"ollama": ollama, "bedrock": bedrock})

    provider, model = router.select(DataClass.NON_SENSITIVE, "", agent="screening")
    assert provider.name == "ollama"
    assert model == "qwen2.5:7b-instruct-q4_K_M"

    provider, model = router.select(DataClass.NON_SENSITIVE, "", agent="hypothesis")
    assert provider.name == "bedrock"
    assert model == "claude-3"


def test_sensitive_overrides_agent_models() -> None:
    policy = RoutingPolicy(
        policy="hybrid",
        default_provider="bedrock",
        rules=[RoutingRule(classification="SENSITIVE", use="ollama")],
        providers={
            "ollama": ProviderConfig(model="qwen2.5:7b"),
            "bedrock": ProviderConfig(model="claude-3"),
        },
        agent_models={"hybrid": {"hypothesis": "bedrock/claude-3"}},
    )
    ollama = _make_provider("ollama")
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(policy, {"ollama": ollama, "bedrock": bedrock})

    provider, _ = router.select(DataClass.SENSITIVE, "", agent="hypothesis")
    assert provider.name == "ollama"


def test_all_local_ignores_agent_models() -> None:
    policy = RoutingPolicy(
        policy="all_local",
        default_provider="ollama",
        providers={"ollama": ProviderConfig(model="qwen2.5:7b")},
        agent_models={"all_local": {"hypothesis": "bedrock/claude-3"}},
    )
    ollama = _make_provider("ollama")
    router = Router(policy, {"ollama": ollama})

    provider, _ = router.select(DataClass.NON_SENSITIVE, "", agent="hypothesis")
    assert provider.name == "ollama"


def test_agent_not_in_map_falls_through_to_rules() -> None:
    policy = RoutingPolicy(
        policy="hybrid",
        default_provider="bedrock",
        rules=[RoutingRule(classification="NON_SENSITIVE", use="bedrock")],
        providers={
            "ollama": ProviderConfig(model="qwen2.5:7b"),
            "bedrock": ProviderConfig(model="claude-3"),
        },
        agent_models={"hybrid": {"screening": "ollama/qwen2.5:7b"}},
    )
    ollama = _make_provider("ollama")
    bedrock = _make_provider("bedrock", supports_sensitive=False)
    router = Router(policy, {"ollama": ollama, "bedrock": bedrock})

    # "reviewer" is not in agent_models → falls through to NON_SENSITIVE rule → bedrock
    provider, _ = router.select(DataClass.NON_SENSITIVE, "", agent="reviewer")
    assert provider.name == "bedrock"
