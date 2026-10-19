# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The perturbation reading in the biology lens node (workflow._perturbation_reading)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import agents.interpretation.biology_lens.perturbations as pt
import capabilities.target_validation.workflow as wf
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance


def _claim(text: str, paper: uuid.UUID) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=paper,
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="disease one",
        evidence_type=EvidenceType.ARTICLE,
        claim_text=text,
        provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
        classification=DataClass.NON_SENSITIVE,
    )


async def test_perturbations_are_asked_once_per_claim_text_and_read_per_paper(monkeypatch):
    papers = [uuid.uuid4(), uuid.uuid4()]
    claims = [
        _claim(f"GENE-A knockout mice had worse phenotype two ({i}).", p)
        for i, p in enumerate(papers)
    ]
    index = {"papers": {str(p): {"scope": "related"} for p in papers}}
    monkeypatch.setattr(wf, "_literature_index", AsyncMock(return_value=index))
    store: dict[str, object] = {}

    async def get(key, _model):
        return store.get(key)

    async def put(key, _model, _kind, payload):
        store[key] = payload

    monkeypatch.setattr(wf, "_llm_cache_get", get)
    monkeypatch.setattr(wf, "_llm_cache_set", put)

    async def answer(batch, **_kw):
        return {
            str(c.evidence_id): {
                "perturbed_gene": "GENE-A",
                "change": "lower",
                "change_basis": "GENE-A knockout",
                "outcome": "worse",
            }
            for c in batch
        }

    ask = AsyncMock(side_effect=answer)
    monkeypatch.setattr(pt, "ask_perturbations", ask)
    ctx = MagicMock()
    ctx.router.select.return_value = (MagicMock(), "model-1")
    state = {
        "target_gene": "GENE-A",
        "direction": "inhibit",
        "model_fingerprint": "fp",
        "extracted_claims": claims,
    }

    first = await wf._perturbation_reading(state, ctx)
    again = await wf._perturbation_reading(state, ctx)

    assert ask.await_count == 1  # the second reading came from the cache
    assert first["verdict"] == again["verdict"] == "contradicts"
    assert len(first["observations"]) == 2


async def test_no_perturbation_claim_no_reading(monkeypatch):
    monkeypatch.setattr(wf, "_literature_index", AsyncMock(return_value={"papers": {}}))

    assert await wf._perturbation_reading({"target_gene": "GENE-A"}, MagicMock()) == {}
