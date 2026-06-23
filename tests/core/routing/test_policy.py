# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.routing.policy import _load


def _write_policy(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "routing.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def test_load_hybrid_policy(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        """
        policy: hybrid
        default_provider: bedrock
        rules:
          - when:
              classification: SENSITIVE
            use: ollama
          - when:
              classification: NON_SENSITIVE
            use: bedrock
        providers:
          ollama:
            model: llama3.1:8b-instruct-q4_K_M
            embed_model: nomic-embed-text:latest
          bedrock:
            model: anthropic.claude-3-5-sonnet-20241022-v2:0
            region: us-east-1
        embeddings:
          model: nomic-embed-text:latest
        """,
    )
    policy = _load(path)
    assert policy.policy == "hybrid"
    assert policy.default_provider == "bedrock"
    assert len(policy.rules) == 2
    assert policy.embed_model == "nomic-embed-text:latest"
    assert "ollama" in policy.providers
    assert "bedrock" in policy.providers


def test_load_all_local_policy(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        """
        policy: all_local
        default_provider: ollama
        providers:
          ollama:
            model: llama3.1:8b-instruct-q4_K_M
            embed_model: nomic-embed-text:latest
        """,
    )
    policy = _load(path)
    assert policy.policy == "all_local"


def test_invalid_policy_raises(tmp_path: Path) -> None:
    path = _write_policy(tmp_path, "policy: invalid_policy\ndefault_provider: x\n")
    with pytest.raises(ValueError, match="policy must be one of"):
        _load(path)


def test_provider_config_model_is_loaded(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        """
        policy: hybrid
        default_provider: ollama
        providers:
          ollama:
            model: llama3.1:8b-instruct-q4_K_M
        """,
    )
    policy = _load(path)
    assert policy.providers["ollama"].model == "llama3.1:8b-instruct-q4_K_M"


def test_lens_num_ctx_env_override_applies_to_all_lens_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LENS_NUM_CTX", "8192")
    path = _write_policy(
        tmp_path,
        """
        policy: all_local
        default_provider: ollama
        providers:
          ollama:
            model: llama3.1:8b-instruct-q4_K_M
            num_ctx: 32768
            task_num_ctx:
              screening: 16384
        """,
    )
    policy = _load(path)
    task_num_ctx = policy.providers["ollama"].task_num_ctx
    assert task_num_ctx["screening"] == 16384  # untouched by the lens-only override
    for task in (
        "genetics_lens",
        "biology_lens",
        "safety_lens",
        "clinical_lens",
        "commercial_lens",
        "regulatory_lens",
    ):
        assert task_num_ctx[task] == 8192


def test_lens_num_ctx_env_override_ignored_when_unset_or_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LENS_NUM_CTX", raising=False)
    path = _write_policy(
        tmp_path,
        """
        policy: all_local
        default_provider: ollama
        providers:
          ollama:
            model: llama3.1:8b-instruct-q4_K_M
        """,
    )
    policy = _load(path)
    assert "genetics_lens" not in policy.providers["ollama"].task_num_ctx

    monkeypatch.setenv("LENS_NUM_CTX", "not-a-number")
    policy = _load(path)
    assert "genetics_lens" not in policy.providers["ollama"].task_num_ctx


def test_lens_num_ctx_env_override_wins_over_task_num_ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LENS_NUM_CTX", "4096")
    path = _write_policy(
        tmp_path,
        """
        policy: all_local
        default_provider: ollama
        providers:
          ollama:
            model: llama3.1:8b-instruct-q4_K_M
            task_num_ctx:
              genetics_lens: 32768
              investigator: 16384
        """,
    )
    policy = _load(path)
    task_num_ctx = policy.providers["ollama"].task_num_ctx
    assert task_num_ctx["genetics_lens"] == 4096  # env overrides the explicit lens entry
    assert task_num_ctx["investigator"] == 16384  # non-lens task untouched by the override
