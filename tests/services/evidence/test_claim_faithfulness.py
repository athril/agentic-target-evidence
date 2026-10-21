# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Extracted claims checked against their source (services/evidence/claim_faithfulness.py).

A regex guard dropped claims whose "loss of <gene>" wording was missing from the source;
13 of the 14 claims it dropped in panel runs were faithful. The source is now asked one
narrow question per claim, and a claim is dropped only for a contradiction quoted word for
word from the source.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, Evidence, EvidenceType, Provenance
from services.evidence import claim_faithfulness
from services.evidence.claim_extraction import extract_claims
from services.evidence.claim_faithfulness import (
    DroppedClaim,
    apply_verdicts,
    check_claims,
    quoted_in,
)

_RUN = uuid.uuid4()
_SOURCE = (
    "Knockout of the GENE-A-counteracting phosphatase OTHER1 disrupted axonal transport. "
    "GENE-A levels were unchanged."
)


def _prov() -> Provenance:
    return Provenance(
        agent_name="t", tool_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
    )


def _evidence(abstract: str = _SOURCE) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="GENE-A",
        disease="DISEASE-A",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source="PMID:1",
        source_link="https://pubmed.ncbi.nlm.nih.gov/1/",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
        extra={"title": "A paper", "abstract": abstract},
    )


def _claim(text: str = "GENE-A knockout disrupted axonal transport.") -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="GENE-A",
        disease="DISEASE-A",
        evidence_type=EvidenceType.ARTICLE,
        claim_text=text,
        perturbation="decrease",
        effect_basis="GENE-A knockout",
        outcome="harm",
        direction=Direction.ACTIVATE,
        effect_source="claim",
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
    )


def _ctx(*replies: str) -> tuple[RunContext, MagicMock]:
    provider = MagicMock()
    provider.complete = AsyncMock(
        side_effect=[
            CompletionResult(
                content=r, model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
            )
            for r in replies
        ]
    )
    router = MagicMock()
    router.select.return_value = (provider, "m")
    ctx = RunContext(run_id=_RUN, trace_id="t", router=router)
    ctx.load_skill = MagicMock(return_value="skill")  # type: ignore[method-assign]
    return ctx, provider


def _reply(*answers: tuple[int, str, str]) -> str:
    return json.dumps({"answers": [{"i": i, "verdict": v, "quote": q} for i, v, q in answers]})


def test_a_quote_counts_only_word_for_word():
    assert quoted_in("knockout of the gene-a-counteracting  phosphatase", _SOURCE)
    assert not quoted_in("GENE-A knockout", _SOURCE)
    assert not quoted_in("", _SOURCE)


def test_a_contradiction_is_flagged_and_the_claim_kept_unchanged():
    claim = _claim()
    verdicts = {
        claim.evidence_id: (
            "contradicted",
            "Knockout of the GENE-A-counteracting phosphatase OTHER1",
        )
    }

    kept, dropped = apply_verdicts([claim], verdicts, _SOURCE, source="PMID:1")

    assert kept == [claim]
    assert dropped == [
        DroppedClaim(
            source="PMID:1",
            claim_text=claim.claim_text,
            reason="faithfulness_flag",
            quote="Knockout of the GENE-A-counteracting phosphatase OTHER1",
        )
    ]


@pytest.mark.parametrize(
    ("verdict", "quote"),
    [
        ("contradicted", "the gene was not knocked out"),  # not in the source
        ("contradicted", ""),
        ("not_stated", ""),
    ],
)
def test_an_unquoted_contradiction_or_silence_is_flagged_without_a_quote(verdict, quote):
    claim = _claim()

    kept, dropped = apply_verdicts([claim], {claim.evidence_id: (verdict, quote)}, _SOURCE)

    assert kept == [claim]  # effect, direction and outcome untouched
    assert [(d.reason, d.quote) for d in dropped] == [("faithfulness_flag", "")]


def test_supported_or_unchecked_claims_are_unchanged():
    supported, unchecked = _claim(), _claim()

    kept, dropped = apply_verdicts(
        [supported, unchecked], {supported.evidence_id: ("supported", "x")}, _SOURCE
    )

    assert kept == [supported, unchecked]
    assert dropped == []


async def test_one_call_per_source_maps_answers_to_claims():
    a, b = _claim("one"), _claim("two")
    ctx, provider = _ctx(_reply((0, "supported", "q0"), (1, "not_stated", "")))

    verdicts = await check_claims([(_evidence(), _SOURCE, [a, b])], "GENE-A", ctx)

    assert verdicts == {a.evidence_id: ("supported", "q0"), b.evidence_id: ("not_stated", "")}
    assert provider.complete.await_count == 1
    request = provider.complete.await_args.args[0]
    assert request.task == "claim_extraction"
    assert "0. one" in request.messages[0]["content"]


async def test_a_reply_that_fails_to_parse_leaves_claims_unchecked_and_degrades(
    monkeypatch, caplog
):
    recorded: list[str] = []
    monkeypatch.setattr(
        claim_faithfulness, "record_degradation", lambda component, *_: recorded.append(component)
    )
    claim = _claim()
    ctx, _ = _ctx("not json")

    verdicts = await check_claims([(_evidence(), _SOURCE, [claim])], "GENE-A", ctx)

    assert verdicts == {}
    assert recorded == ["extraction: faithfulness check"]
    assert "'not json'" in caplog.text  # the reply's start is logged
    kept, dropped = apply_verdicts([claim], verdicts, _SOURCE)
    assert kept == [claim] and dropped == []


def _extraction(ev: Evidence, *claims: dict) -> str:
    return json.dumps([{"evidence_id": str(ev.evidence_id), "claims": list(claims)}])


async def test_extraction_checks_only_claims_typed_as_acting_on_the_target():
    ev = _evidence()
    acting = {
        "claim_text": "GENE-A knockout disrupted axonal transport.",
        "acts_on_target": "lowers",
        "target_level": "none",
        "span": "GENE-A knockout",
        "outcome": "harm",
    }
    level_only = {
        "claim_text": "GENE-A levels were unchanged in the knockout.",
        "acts_on_target": "none",
        "target_level": "down",
        "span": "GENE-A levels",
    }
    plain = {"claim_text": "Axonal transport was disrupted.", "acts_on_target": "none"}
    ctx, provider = _ctx(
        _extraction(ev, acting, level_only, plain),
        _reply((0, "contradicted", "Knockout of the GENE-A-counteracting phosphatase OTHER1")),
    )
    dropped: list[DroppedClaim] = []

    claims = await extract_claims([ev], "GENE-A", "DISEASE-A", ctx, dropped=dropped)

    check = provider.complete.await_args_list[1].args[0].messages[0]["content"]
    assert "0. GENE-A knockout disrupted" in check
    assert "levels were unchanged in the knockout" not in check.split("Claims:")[1]
    assert [c.claim_text for c in claims] == [
        acting["claim_text"],
        level_only["claim_text"],
        plain["claim_text"],
    ]
    assert [(d.reason, d.source) for d in dropped] == [("faithfulness_flag", "PMID:1")]


async def test_grounding_drops_reach_the_dropped_list():
    ev = _evidence()
    ctx, provider = _ctx(
        _extraction(ev, {"claim_text": "GENE-A knockout raised risk 37-fold."}),
    )
    dropped: list[DroppedClaim] = []

    claims = await extract_claims([ev], "GENE-A", "DISEASE-A", ctx, dropped=dropped)

    assert claims == []
    assert [d.reason for d in dropped] == ["number_not_in_source"]
    assert provider.complete.await_count == 1  # nothing left to check
