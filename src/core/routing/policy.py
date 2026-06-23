# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import contextlib
import os
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ProviderConfig:
    model: str = ""  # Azure uses deployment instead; model may be absent
    embed_model: str | None = None
    base_url: str | None = None
    region: str | None = None
    endpoint: str | None = None
    deployment: str | None = None
    num_ctx: int = 32768  # Ollama context window; ignored by other providers
    task_num_ctx: dict[str, int] = field(default_factory=dict)  # per-task override of num_ctx
    api_key: str | None = None  # Anthropic / OpenAI direct API key
    timeout: float | None = None  # Ollama HTTP timeout in seconds; None = no timeout

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderConfig:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: _expand_env(v) for k, v in d.items() if k in known})


@dataclass
class RoutingRule:
    """A single if/then routing rule from the YAML ``rules`` list."""

    classification: str | None = None  # SENSITIVE | NON_SENSITIVE | None means any
    task: str | None = None  # task name pattern or None means any
    use: str = ""  # name of the provider to use


@dataclass
class RoutingPolicy:
    policy: str  # hybrid | all_local | custom
    default_provider: str
    rules: list[RoutingRule] = field(default_factory=list)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    embed_model: str = "nomic-embed-text:latest"
    # Per-policy agent→model map. Keyed by policy name; active block = agent_models[policy].
    # Values are "provider/model" strings, e.g. "anthropic/claude-sonnet-4-6".
    agent_models: dict[str, dict[str, str]] = field(default_factory=dict)


_ALLOWED_POLICIES = {"hybrid", "all_local", "custom"}
_cached: RoutingPolicy | None = None
_policy_path: Path = Path("config/routing.yaml")

# The six interpretation lens tasks (see agents/interpretation/_lens_base.py
# run_lens, task=f"{lens}_lens"). Their num_ctx is set per-task under the ollama
# provider's task_num_ctx in routing.yaml; the LENS_NUM_CTX env var, if set,
# overrides all six uniformly without editing that file.
_LENS_TASKS = (
    "genetics_lens",
    "biology_lens",
    "safety_lens",
    "clinical_lens",
    "commercial_lens",
    "regulatory_lens",
)


def _expand_env(value: Any) -> Any:
    """Expand ${VAR} placeholders in string values using os.environ."""
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _lens_num_ctx_override() -> int | None:
    """Optional LENS_NUM_CTX env override applied to every lens task's num_ctx.

    Wins over the lens entries in the ollama provider's task_num_ctx
    (config/routing.yaml); when unset or unparseable, those YAML values stand.
    """
    raw = os.environ.get("LENS_NUM_CTX")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load(path: Path) -> RoutingPolicy:
    with path.open() as fh:
        raw = yaml.safe_load(fh)

    if raw["policy"] not in _ALLOWED_POLICIES:
        raise ValueError(f"routing.yaml: policy must be one of {_ALLOWED_POLICIES}")

    rules = [
        RoutingRule(
            classification=r.get("when", {}).get("classification"),
            task=r.get("when", {}).get("task"),
            use=r["use"],
        )
        for r in raw.get("rules", [])
    ]

    providers = {
        name: ProviderConfig.from_dict(cfg) for name, cfg in raw.get("providers", {}).items()
    }

    # LENS_NUM_CTX env, if set, overrides the lens entries in task_num_ctx uniformly.
    lens_num_ctx = _lens_num_ctx_override()
    if lens_num_ctx is not None:
        for provider in providers.values():
            for task in _LENS_TASKS:
                provider.task_num_ctx[task] = lens_num_ctx

    return RoutingPolicy(
        policy=raw["policy"],
        default_provider=raw.get("default_provider", "bedrock"),
        rules=rules,
        providers=providers,
        embed_model=raw.get("embeddings", {}).get("model", "nomic-embed-text:latest"),
        agent_models=raw.get("agent_models", {}),
    )


def get_policy(path: Path | None = None) -> RoutingPolicy:
    global _cached, _policy_path
    if path is not None:
        _policy_path = path
    if _cached is None:
        _cached = _load(_policy_path)
    return _cached


def reload_policy() -> None:
    global _cached
    _cached = _load(_policy_path)


def _setup_sighup() -> None:
    with contextlib.suppress(OSError, ValueError):  # Windows / non-main thread
        signal.signal(signal.SIGHUP, lambda *_: reload_policy())


_setup_sighup()
