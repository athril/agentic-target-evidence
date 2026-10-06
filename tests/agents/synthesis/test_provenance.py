# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from agents.synthesis.report.provenance import (
    REASONING_STAGES,
    Provenance,
    collect_provenance,
    run_info_md,
    short_version,
    version_line,
)
from core.routing.policy import ProviderConfig, RoutingPolicy
from core.routing.router import Router


def _provider(name: str, model: str) -> MagicMock:
    p = MagicMock()
    p.name = name
    p._model = model
    p.supports.return_value = True
    return p


def test_short_version_is_installed_version() -> None:
    import importlib.metadata

    assert short_version() == importlib.metadata.version("agentic-target-evidence")


def test_collect_provenance_all_local_single_model() -> None:
    policy = RoutingPolicy(
        policy="all_local",
        default_provider="ollama",
        providers={"ollama": ProviderConfig(model="qwen2.5:7b")},
    )
    router = Router(policy, {"ollama": _provider("ollama", "qwen2.5:7b")})

    prov = collect_provenance(router)

    assert prov.policy == "all_local"
    assert set(REASONING_STAGES) <= set(prov.stage_models)
    assert prov.model_for("safety_lens") == "ollama/qwen2.5:7b"


def test_collect_provenance_skips_stages_without_provider() -> None:
    policy = RoutingPolicy(policy="custom", default_provider="bedrock", providers={})
    prov = collect_provenance(Router(policy, {}))
    assert prov.model_for("screening") == ""


def test_collect_provenance_investigator_bypasses_router() -> None:
    policy = RoutingPolicy(policy="custom", default_provider="bedrock", providers={})
    assert collect_provenance(Router(policy, {})).model_for("investigator").startswith("ollama/")


def test_run_info_lists_steps_in_pipeline_order_with_labels() -> None:
    prov = Provenance(
        policy="hybrid",
        stage_models={"critic": "anthropic/opus", "screening": "ollama/q"},
    )
    md = run_info_md(datetime(2026, 10, 6, 15, 20, tzinfo=UTC), prov)
    assert md.startswith("## Run Information")
    assert "- **Generated:** 2026-10-06 15:20 UTC" in md
    assert f"- **Version:** agentic-target-evidence {short_version()}" in md
    assert md.index("| Evidence screening | `ollama/q` |") < md.index(
        "| Critic | `anthropic/opus` |"
    )


def test_run_info_stage_filter() -> None:
    prov = Provenance(policy="hybrid", stage_models={"screening": "o/q", "critic": "a/o"})
    md = run_info_md(datetime(2026, 10, 6, tzinfo=UTC), prov, stages=("screening",))
    assert "o/q" in md and "a/o" not in md


def test_version_line_is_short() -> None:
    assert version_line() == f"**Version:** {short_version()}"
