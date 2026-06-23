# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from schemas.evidence import DataClass

from .policy import RoutingPolicy
from .providers.base import ModelProvider


class NoProviderError(Exception):
    """Raised when no registered provider matches the policy rules for a request."""


def _default_model(provider: ModelProvider) -> str:
    return getattr(provider, "_model", "") or getattr(provider, "_deployment", "")


class Router:
    def __init__(
        self,
        policy: RoutingPolicy,
        providers: dict[str, ModelProvider],
    ) -> None:
        self._policy = policy
        self._providers = providers

    def select(
        self,
        classification: DataClass,
        task: str,
        agent: str = "",
    ) -> tuple[ModelProvider, str]:
        """Return (provider, model_name) for this call.

        Resolution order:
          1. all_local policy  → always ollama
          2. SENSITIVE data    → always ollama (unconditional privacy override)
          3. agent_models[policy][agent] → specified provider + model
          4. existing rules (classification / task matching)
          5. default_provider fallback
        """
        if self._policy.policy == "all_local":
            p = self._require("ollama")
            return p, _default_model(p)

        if classification == DataClass.SENSITIVE:
            p = self._require("ollama")
            return p, _default_model(p)

        active_block = self._policy.agent_models.get(self._policy.policy, {})
        if agent and agent in active_block:
            provider_name, model = active_block[agent].split("/", 1)
            agent_provider = self._providers.get(provider_name)
            if agent_provider and agent_provider.supports(classification):
                return agent_provider, model

        for rule in self._policy.rules:
            if rule.classification and rule.classification != classification.value:
                continue
            if rule.task and rule.task != task:
                continue
            provider = self._providers.get(rule.use)
            if provider and provider.supports(classification):
                return provider, _default_model(provider)

        default = self._providers.get(self._policy.default_provider)
        if default and default.supports(classification):
            return default, _default_model(default)

        raise NoProviderError(
            f"No provider can handle classification={classification.value!r}, task={task!r}, "
            f"agent={agent!r} under policy {self._policy.policy!r}. "
            "Check config/routing.yaml and registered providers."
        )

    def _require(self, name: str) -> ModelProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise NoProviderError(
                f"Provider {name!r} is required by the routing policy but is not registered."
            )
        return provider
