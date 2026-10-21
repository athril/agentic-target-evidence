# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Direction of effect from typed allele effects (direction_of_effect.from_allele_effects)
and the allele-effect question's validation (genetics_lens/allele_effects.py).

The failure these prevent: the claim extractor read a direction from surface words, so a
protective loss-of-function allele that "increased the likelihood of resolution" counted
for activating the gene.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from services.evidence.direction_of_effect import build_direction_reading, from_allele_effects
from services.evidence.disease_scope import build_disease_scope

_FIXTURE = Path("tests/fixtures/allele_effect_claims.yaml")
_SCOPE = build_disease_scope("disease a", names=["disease a"], endpoints=())


def _labelled() -> list[dict]:
    return yaml.safe_load(_FIXTURE.read_text())["claims"]


def _claim(i: int, text: str, direction: str = "unspecified"):
    effect = {"inhibit": "decrease", "activate": "increase"}.get(direction, "none")
    return SimpleNamespace(
        evidence_id=f"c{i}",
        source_evidence_id=f"p{i}",
        claim_text=text,
        direction=SimpleNamespace(value=direction),
        perturbation=effect,
        outcome="benefit" if effect != "none" else "none",
        off_target=False,
    )


@pytest.mark.parametrize("case", _labelled(), ids=lambda c: c["text"][:40])
def test_the_labelled_answers_give_the_labelled_therapeutic_direction(case: dict) -> None:
    claim = _claim(0, case["text"])

    obs = from_allele_effects([claim], {"c0": case["answer"]}, gene="GENE-A", scope=_SCOPE)

    assert [o.favours for o in obs] == ([case["favours"]] if case["favours"] else [])


def test_typed_answers_replace_the_surface_tags_in_the_reading() -> None:
    """A tag "activate" on a protective loss-of-function sentence no longer counts."""
    claims = [
        _claim(i, "Loss-of-function carriers of GENE-A had lower disease a risk.", "activate")
        for i in range(5)
    ]
    answers = {
        f"c{i}": {
            "variant_gene": "GENE-A",
            "gene_change": "loss",
            "risk": "lower",
            "outcome": "disease a",
        }
        for i in range(5)
    }

    tagged = build_direction_reading(
        "inhibit", disease="disease a", genetics_claims=claims, gene="GENE-A", scope=_SCOPE
    )
    typed = build_direction_reading(
        "inhibit",
        disease="disease a",
        genetics_claims=claims,
        gene="GENE-A",
        scope=_SCOPE,
        allele_effects=answers,
    )

    assert tagged.verdict == "contradicts"
    assert typed.verdict == "supports"


def test_an_outcome_outside_the_disease_scope_gives_no_observation() -> None:
    claim = _claim(0, "GENE-A loss carriers had lower disease z risk.")
    answer = {
        "variant_gene": "GENE-A",
        "gene_change": "loss",
        "risk": "lower",
        "outcome": "disease z",
    }

    assert from_allele_effects([claim], {"c0": answer}, gene="GENE-A", scope=_SCOPE) == []


async def test_the_question_asks_for_risk_only_and_drops_unseen_ids() -> None:
    from agents.interpretation.genetics_lens.allele_effects import (
        AlleleEffect,
        ask_allele_effects,
    )
    from core.routing.providers.base import CompletionResult
    from harness.context import RunContext
    from schemas.evidence import DataClass, EvidenceType
    from tests.services.evidence.test_lens_selection import _claim as core_claim

    assert "gene_change" not in AlleleEffect.model_fields
    claim = core_claim(
        EvidenceType.ARTICLE,
        "Truncating GENE-A variants lowered disease a risk.",
        topics=["genetics"],
    )
    reply = {
        "claims": [
            {
                "claim_id": str(claim.evidence_id),
                "variant_gene": "GENE-A",
                "outcome": "disease a",
                "risk": "lower",
            },
            {"claim_id": "not-shown", "risk": "higher"},
        ]
    }
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content=json.dumps(reply),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )
    )
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    ctx = RunContext(run_id=claim.run_id, trace_id="t", router=router)

    answers = await ask_allele_effects([claim], ctx=ctx, classification=DataClass.NON_SENSITIVE)

    assert answers == {
        str(claim.evidence_id): {"variant_gene": "GENE-A", "outcome": "disease a", "risk": "lower"}
    }


def test_only_claims_with_a_known_change_are_asked_and_the_merge_adds_it() -> None:
    from agents.interpretation.genetics_lens.allele_effects import (
        merge_changes,
        with_known_change,
    )
    from services.evidence.variant_registry import KnownChange

    known, unknown = _claim(0, "x"), _claim(1, "y")
    changes = {"c0": KnownChange(change="loss", source="registry: c9", basis="L46 allele")}

    assert with_known_change([known, unknown], changes) == [known]
    merged = merge_changes(
        {"c0": {"variant_gene": "GENE-A", "risk": "lower"}, "c1": {"risk": "higher"}}, changes
    )
    assert merged == {
        "c0": {
            "variant_gene": "GENE-A",
            "risk": "lower",
            "gene_change": "loss",
            "change_basis": "L46 allele",
            "change_source": "registry: c9",
        }
    }
