# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Structured records become claims deterministically; the LLM reads free text only
(services/evidence/claim_extraction.py).

The failure these prevent: the extraction LLM paraphrased structured records. It rounded a
fold change, then the grounding check dropped the claim. It merged a knockout record into
"knockout mice are viable", which the check also rejected. And it returned nothing for whole
batches of them.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from services.evidence.claim_extraction import extract_claims, structured_claims

_RUN = uuid.uuid4()


def _ev(et: EvidenceType, extra: dict, source: str = "src:1") -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="GENEX",
        disease="disease one",
        evidence_type=et,
        scope="abstract",
        source=source,
        source_link="https://example.org",
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
        extra=extra,
    )


def _texts(ev: Evidence) -> list[str]:
    return [c.claim_text for c in structured_claims(ev, _RUN, "t")]


async def test_structured_records_never_reach_the_extraction_llm():
    article = _ev(EvidenceType.ARTICLE, {"title": "T", "abstract": "GENEX was studied."}, "PMID:1")
    expression = _ev(
        EvidenceType.EXPRESSION, {"text": "GTEx top tissues (median TPM): Liver=12.0."}, "gtex:1"
    )
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content=json.dumps([{"evidence_id": str(article.evidence_id), "claims": []}]),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )
    )
    router = MagicMock()
    router.select.return_value = (provider, "m")
    ctx = RunContext(run_id=_RUN, trace_id="t", router=router)
    ctx.load_skill = MagicMock(return_value="skill")

    claims = await extract_claims([article, expression], "GENEX", "disease one", ctx)

    prompt = provider.complete.await_args.args[0].messages[0]["content"]
    assert str(article.evidence_id) in prompt
    assert str(expression.evidence_id) not in prompt
    assert [c.claim_text for c in claims] == ["GTEx top tissues (median TPM): Liver=12.0."]


def test_a_long_record_becomes_one_claim_per_item_and_quoted_semicolons_stay_whole():
    text = (
        "Expression Atlas: no data for disease one; results elsewhere: DOWN -2.7-fold "
        "(p=1.7e-33) in 'tumour' vs 'normal' [E-1]; UP +6.3-fold (p=2.2e-15) in "
        "'senescence; drug-a' vs 'control; vehicle' [E-2]; DOWN -3.3-fold (p=3.4e-20) "
        "in 'knockdown' vs 'control' [E-3]"
    )

    claims = _texts(_ev(EvidenceType.EXPRESSION, {"text": text}, "expression_atlas:1"))

    assert len(claims) == 4
    assert all(c.startswith("Expression Atlas (GENEX): ") for c in claims)
    assert "'senescence; drug-a' vs 'control; vehicle' [E-2]" in claims[2]


def test_a_record_without_rendered_text_reads_as_fields_not_json():
    claims = _texts(
        _ev(
            EvidenceType.EXPRESSION,
            {"hpa_tissue_specificity": "Group enriched", "screening_verdict": {"v": 1}},
            "hpa:rna_tissue_specificity",
        )
    )

    assert claims == [
        "hpa:rna_tissue_specificity for GENEX: hpa tissue specificity: Group enriched"
    ]


def test_a_knockout_record_states_its_own_facts():
    claims = _texts(
        _ev(
            EvidenceType.FUNCTIONAL_GENOMICS,
            {
                "text": "IMPC knockout-mouse phenotype for GENEX: viability=viable; 26 significant "
                "phenotype call(s); top terms: increased circulating triglyceride level, "
                "abnormal heart morphology, decreased body weight, enlarged spleen."
            },
            "impc:GENEX",
        )
    )

    assert claims[0] == "IMPC knockout-mouse phenotype for GENEX: viability=viable"


def test_the_clinvar_claim_counts_variant_consequences():
    claims = _texts(
        _ev(
            EvidenceType.CONSTRAINT,
            {
                "pathogenic": [
                    {"major_consequence": "stop_gained", "gold_stars": 1},
                    {"major_consequence": "frameshift_variant", "gold_stars": 1},
                ],
                "likely_pathogenic": [{"major_consequence": "stop_gained"}],
            },
            "gnomad_clinvar:1",
        )
    )

    assert claims == [
        "3 Pathogenic/Likely-Pathogenic ClinVar variant(s) in GENEX "
        "(2 P, 1 LP; 2 stop_gained, 1 frameshift_variant)"
    ]
