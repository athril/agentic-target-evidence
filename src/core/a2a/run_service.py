# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generic A2A service runner.

Each agent container (agents-knowledge, agents-reasoning, report) runs this
module with a --service flag that selects which agents to register.  Incoming
A2A messages are routed by their ``to_agent`` field.

Usage (Docker CMD):
    python -m core.a2a.run_service --service knowledge --port 8001
    python -m core.a2a.run_service --service reasoning --port 8002
    python -m core.a2a.run_service --service report    --port 8003
"""

from __future__ import annotations

import argparse
import importlib
import os
import ssl
from collections.abc import Callable, Coroutine
from typing import Any

import uvicorn

from core.a2a.server import create_app, register_handler
from core.routing.policy import get_policy
from core.routing.providers.base import ModelProvider
from core.routing.providers.bedrock import BedrockProvider
from core.routing.providers.ollama import OllamaProvider
from core.routing.router import Router
from core.telemetry.setup import init_telemetry
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage

# Map service name → list of (module_path, class_name) pairs.
_SERVICES: dict[str, list[tuple[str, str]]] = {
    "knowledge": [
        ("agents.retrieval.literature.agent", "LiteratureAgent"),
        ("agents.retrieval.patent.agent", "PatentAgent"),
        ("agents.retrieval.clinical_trial.agent", "ClinicalTrialAgent"),
        ("agents.retrieval.opentargets.agent", "OpenTargetsAgent"),
        ("agents.retrieval.genetics.agent", "GeneticsAgent"),
        ("agents.retrieval.omics.agent", "OmicsAgent"),
        ("agents.screening.screening.agent", "ScreeningAgent"),
        ("agents.screening.knowledge_extraction.agent", "KnowledgeExtractionAgent"),
    ],
    "reasoning": [
        ("agents.synthesis.experiment.agent", "ExperimentAgent"),
        ("agents.challenge.critic.agent", "CriticAgent"),
        ("agents.challenge.reviewer.agent", "ReviewerAgent"),
    ],
    "report": [
        ("agents.synthesis.report.agent", "ReportAgent"),
    ],
}


def _build_router() -> Router:
    policy = get_policy()
    ollama_cfg = policy.providers["ollama"]
    providers: dict[str, ModelProvider] = {
        "ollama": OllamaProvider(
            model=ollama_cfg.model,
            embed_model=ollama_cfg.embed_model or "nomic-embed-text:latest",
            base_url=ollama_cfg.base_url or "http://ollama:11434",
            num_ctx=ollama_cfg.num_ctx,
            task_num_ctx=ollama_cfg.task_num_ctx,
            timeout=ollama_cfg.timeout,
        )
    }
    if os.environ.get("BEDROCK_REGION") or os.environ.get("AZURE_OPENAI_ENDPOINT"):
        bedrock_cfg = policy.providers["bedrock"]
        providers["bedrock"] = BedrockProvider(
            model=bedrock_cfg.model,
            region=bedrock_cfg.region or os.environ.get("BEDROCK_REGION", ""),
        )
    return Router(policy, providers)


def _load_agents(service: str) -> dict[str, BaseAgent]:
    """Return {contract_name: agent_instance} for the requested service."""
    agents: dict[str, BaseAgent] = {}
    for mod_path, cls_name in _SERVICES[service]:
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        instance = cls()
        agents[instance.contract.name] = instance
    return agents


def _make_dispatch(
    agents: dict[str, BaseAgent], router: Router
) -> Callable[[AgentMessage], Coroutine[Any, Any, AgentMessage]]:
    async def _handler(msg: AgentMessage) -> AgentMessage:
        agent = agents.get(msg.to_agent)
        if agent is None:
            return msg.error_reply(
                f"No agent registered for to_agent={msg.to_agent!r} in this service"
            )
        ctx = RunContext(run_id=msg.run_id, trace_id=msg.trace_id, router=router)
        return await agent.run(msg, ctx)

    return _handler


def _ssl_kwargs() -> dict[str, Any]:
    cert = os.environ.get("AGENT_CERT_PATH")
    key = os.environ.get("AGENT_KEY_PATH")
    ca = os.environ.get("CA_CERT_PATH")
    if not (cert and key and ca):
        return {}
    return {
        "ssl_certfile": cert,
        "ssl_keyfile": key,
        "ssl_ca_certs": ca,
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A agent service runner")
    parser.add_argument("--service", required=True, choices=list(_SERVICES.keys()))
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    init_telemetry()

    router = _build_router()
    agents = _load_agents(args.service)
    register_handler(_make_dispatch(agents, router))

    app = create_app()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        workers=1,
        **_ssl_kwargs(),
    )


if __name__ == "__main__":
    main()
