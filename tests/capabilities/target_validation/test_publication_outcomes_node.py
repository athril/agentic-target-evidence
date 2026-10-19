# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Publication outcomes in the programme reading (workflow._publication_outcomes)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import agents.interpretation.clinical_lens.publication_outcomes as po
import capabilities.target_validation.workflow as wf
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from services.evidence.programme_outcomes import ProgrammeReading, load_programme_config


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


async def test_publication_outcomes_are_asked_once_per_claim_text_and_merged(monkeypatch):
    paper = uuid.uuid4()
    text = "Patients randomised to DRUG-A had more relapses than placebo."
    first, repeat = _claim(text, paper), _claim(text, paper)
    rows = [
        {"drug_name": "DRUG-A", "action_type": "INHIBITOR"},
        {"drug_name": "DRUG-X", "action_type": "AGONIST"},  # opposite direction: not asked
    ]
    monkeypatch.setattr(wf, "_ot_extra", lambda _s: {"known_drugs": rows})
    index = {
        "papers": {
            str(paper): {"scope": "in_scope", "design": {"clinical": {"family": "rct", "level": 2}}}
        }
    }
    monkeypatch.setattr(wf, "_literature_index", AsyncMock(return_value=index))
    store: dict[str, object] = {}

    async def get(key, _model):
        return store.get(key)

    async def put(key, _model, _kind, payload):
        store[key] = payload

    monkeypatch.setattr(wf, "_llm_cache_get", get)
    monkeypatch.setattr(wf, "_llm_cache_set", put)

    async def answer(claims, *, drugs, **_kw):
        assert drugs == ["DRUG-A"]
        return {
            str(c.evidence_id): {
                "drug": "DRUG-A",
                "design": "randomised_controlled",
                "result": "worsened",
            }
            for c in claims
        }

    ask = AsyncMock(side_effect=answer)
    monkeypatch.setattr(po, "ask_publication_outcomes", ask)
    ctx = MagicMock()
    ctx.router.select.return_value = (MagicMock(), "model-1")
    cfg = load_programme_config()
    empty = ProgrammeReading(requested="decrease")
    state = {"disease": "disease one", "model_fingerprint": "fp", "extracted_claims": [first]}

    reading = await wf._publication_outcomes(state, empty, cfg, ctx)
    again = await wf._publication_outcomes({**state, "extracted_claims": [repeat]}, empty, cfg, ctx)

    assert ask.await_count == 1  # the repeat came from the cache
    for r in (reading, again):
        [p] = r.programmes
        assert (p.drug, p.outcome, p.worsened) == ("DRUG-A", "failed_efficacy", True)
    assert again.programmes[0].publications[0].claim_id == str(repeat.evidence_id)
