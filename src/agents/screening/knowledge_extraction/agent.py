# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Knowledge extraction agent.

For each "keep" Evidence:
  1. Fetch the abstract via PubMed MCP for PMID sources; fall back to
     claim_text for non-PMID sources.  Skip items with no embeddable text.
  2. Chunk into ≤512-token segments (≈2 000 chars).
  3. Embed each chunk via OllamaProvider (nomic-embed-text).
  4. Upsert the updated Evidence row (with embedding) via EvidenceRepository.
  5. If a reason to re-screen is detected in the abstract, flag the item as
     "uncertain" so the graph can dispatch it back to ScreeningAgent.
  6. If full text is available in PubMed Central, also upgrade scope to
     "full_text" and store the PMC URL as artifact_uri.

Returns the payload with embeddings stored and scope/artifact_uri upgraded
for items where PMC full text is available.
"""

from __future__ import annotations

import uuid
from typing import Any

from agents.screening.knowledge_extraction.contract import CONTRACT
from core.persistence.db import get_session
from core.persistence.repos.evidence import EvidenceRepository
from core.routing.providers.ollama import OllamaProvider
from harness.base_agent import BaseAgent
from harness.context import RunContext
from mcp_servers.pubmed.tools import fetch_abstract, fetch_full_text
from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage

_CHUNK_CHARS = 2_000  # ≈512 tokens at ~4 chars/token
_RE_SCREEN_PHRASES = [  # heuristic triggers for re-screening
    "retracted",
    "erratum",
    "correction notice",
    "wrong patient population",
]


def _chunk(text: str) -> list[str]:
    return [text[i : i + _CHUNK_CHARS] for i in range(0, len(text), _CHUNK_CHARS)]


def _needs_rescreen(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _RE_SCREEN_PHRASES)


def _extract_pmid(evidence: Evidence) -> str | None:
    if evidence.source.startswith("PMID:"):
        return evidence.source[5:]
    return None


async def _get_embed_text(ev: Evidence) -> str:
    """Return the best available text for embedding this evidence item.

    Prefers the PubMed abstract (title + abstract body) for PMID sources.
    Falls back to claim_text for non-PMID sources or when the abstract fetch
    fails or returns empty content.
    """
    pmid = _extract_pmid(ev)
    if pmid:
        try:
            record = await fetch_abstract(pmid)
            text = " ".join(filter(None, [record.title, record.abstract]))
            if text.strip():
                return text.strip()
        except Exception:
            pass
    return ev.claim_text


class KnowledgeExtractionAgent(BaseAgent):
    contract = CONTRACT

    def __init__(self, embed_provider: OllamaProvider | None = None) -> None:
        self._embed = embed_provider

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        if not isinstance(msg.payload, list):
            return _passthrough(msg)

        evidences: list[Evidence] = [e for e in msg.payload if isinstance(e, Evidence)]
        keep_items = [
            e for e in evidences if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]

        # Resolve embed provider: prefer injected, fall back to router
        embed_provider = self._embed or _resolve_embed_provider(ctx)

        updated: dict[uuid.UUID, Evidence] = {}
        async with get_session() as session:
            repo = EvidenceRepository(session)
            for ev in keep_items:
                embed_text = await _get_embed_text(ev)
                if not embed_text:
                    continue  # no embeddable text available

                # Independently check for PMC full text to upgrade scope/URI
                ft = None
                pmid = _extract_pmid(ev)
                if pmid:
                    ft = await fetch_full_text(pmid)

                rescreen = _needs_rescreen(embed_text)
                chunks = _chunk(embed_text)
                embeddings = await embed_provider.embed(chunks)
                # Store the first chunk's embedding as the row-level vector
                first_embedding = embeddings[0] if embeddings else None

                new_verdict = (
                    {"verdict": "uncertain", "rationale": "Abstract triggered re-screen"}
                    if rescreen
                    else ev.extra.get("screening_verdict", {})
                )
                new_extra: dict[str, Any] = {
                    **ev.extra,
                    "screening_verdict": new_verdict,
                    "chunk_count": len(chunks),
                }
                update_fields: dict[str, Any] = {"extra": new_extra}
                if ft and ft.available and ft.full_text_url:
                    update_fields["scope"] = "full_text"
                    update_fields["artifact_uri"] = ft.full_text_url
                    new_extra["full_text_url"] = ft.full_text_url

                upgraded = ev.model_copy(update=update_fields)
                updated[ev.evidence_id] = upgraded

                # Upsert the upgraded Evidence and store the embedding
                await repo.upsert(upgraded)
                if first_embedding is not None:
                    await repo.update_embedding(upgraded.evidence_id, first_embedding)

        result_payload = [
            updated.get(e.evidence_id, e) if isinstance(e, Evidence) else e for e in evidences
        ]
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload=result_payload,
            trace_id=msg.trace_id,
        )


def _passthrough(msg: AgentMessage) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=msg.run_id,
        from_agent=msg.to_agent,
        to_agent=msg.from_agent,
        intent="result",
        payload=msg.payload,
        trace_id=msg.trace_id,
    )


def _resolve_embed_provider(ctx: RunContext) -> OllamaProvider:
    # Embeddings are always local — select the ollama provider unconditionally
    provider, _model = ctx.router.select(DataClass.NON_SENSITIVE, "embed")
    if isinstance(provider, OllamaProvider):
        return provider
    # Fallback: construct a default Ollama embedder; this path should not be
    # reached under a valid routing policy but guards against misconfiguration.
    return OllamaProvider(model="nomic-embed-text:latest")
