# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Build a Router with every provider the routing policy can reach.

Ollama is always registered. Cloud providers are registered only under a non-local
policy (hybrid/custom) and only when their credentials resolve; a policy that assigns
an agent to a provider without credentials fails here, at startup, instead of
mid-run after retrieval.
"""

from __future__ import annotations

import os

from .policy import ProviderConfig, RoutingPolicy, get_policy
from .providers.anthropic import AnthropicProvider
from .providers.azure import AzureProvider
from .providers.base import ModelProvider
from .providers.bedrock import BedrockProvider
from .providers.google import GoogleProvider
from .providers.ollama import OllamaProvider
from .providers.openai import OpenAIProvider
from .router import NoProviderError, Router

# What each cloud provider needs, for the startup error message.
_CREDENTIAL_HINTS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "bedrock": "BEDROCK_MODEL_ID and AWS_REGION (plus AWS credentials)",
    "azure": "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT and AZURE_OPENAI_API_KEY",
}


def _resolved(value: str | None) -> str:
    """The value, or "" when empty or an unexpanded ${VAR} placeholder."""
    if not value or "${" in value:
        return ""
    return value


def _cloud_providers(cfgs: dict[str, ProviderConfig]) -> dict[str, ModelProvider]:
    providers: dict[str, ModelProvider] = {}
    empty = ProviderConfig()

    anthropic = cfgs.get("anthropic", empty)
    if key := _resolved(anthropic.api_key):
        providers["anthropic"] = AnthropicProvider(api_key=key)

    openai = cfgs.get("openai", empty)
    if key := _resolved(openai.api_key):
        providers["openai"] = OpenAIProvider(api_key=key)

    google = cfgs.get("google", empty)
    if key := _resolved(google.api_key):
        providers["google"] = GoogleProvider(
            api_key=key, model=_resolved(google.model) or "gemini-2.5-pro"
        )

    bedrock = cfgs.get("bedrock", empty)
    region = (
        _resolved(bedrock.region)
        or os.environ.get("AWS_REGION", "")
        or os.environ.get("BEDROCK_REGION", "")
    )
    if (model := _resolved(bedrock.model)) and region:
        providers["bedrock"] = BedrockProvider(model=model, region=region)

    azure = cfgs.get("azure", empty)
    deployment, endpoint = _resolved(azure.deployment), _resolved(azure.endpoint)
    azure_key = _resolved(azure.api_key) or os.environ.get("AZURE_OPENAI_API_KEY", "")
    if deployment and endpoint and azure_key:
        providers["azure"] = AzureProvider(
            deployment=deployment, endpoint=endpoint, api_key=azure_key
        )

    return providers


def build_router(
    policy: RoutingPolicy | None = None, *, ollama_base_url: str | None = None
) -> Router:
    """Router for ``policy`` (default: config/routing.yaml).

    ``ollama_base_url`` overrides the configured URL (the host CLI reaches Ollama on
    localhost, the containers on the compose service name).
    """
    policy = policy or get_policy()
    ollama_cfg = policy.providers["ollama"]
    providers: dict[str, ModelProvider] = {
        "ollama": OllamaProvider(
            model=ollama_cfg.model,
            embed_model=ollama_cfg.embed_model or "nomic-embed-text:latest",
            base_url=ollama_base_url or ollama_cfg.base_url or "http://ollama:11434",
            num_ctx=ollama_cfg.num_ctx,
            task_num_ctx=ollama_cfg.task_num_ctx,
            timeout=ollama_cfg.timeout,
            think=ollama_cfg.think,
        )
    }
    if policy.policy != "all_local":
        providers.update(_cloud_providers(policy.providers))

    router = Router(policy, providers)
    missing = router.unregistered_assignments()
    if missing:
        lines = [
            f"  {key} → {name} (set {_CREDENTIAL_HINTS.get(name, 'its credentials')})"
            for key, name in sorted(missing.items())
        ]
        raise NoProviderError(
            f"routing policy {policy.policy!r} assigns agents to providers with no "
            "credentials:\n" + "\n".join(lines) + "\nSet them in .env, or edit "
            f"agent_models.{policy.policy} in config/routing.yaml."
        )
    return router
