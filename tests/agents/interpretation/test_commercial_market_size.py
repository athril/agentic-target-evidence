# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Commercial lens: a market is not sized from another disorder's prevalence."""

from __future__ import annotations

import uuid

from agents.interpretation.commercial_lens.agent import _market_size_unsized
from schemas.verdicts import AxisVerdict, LensVerdict

_RUN = uuid.uuid4()


def test_unsized_market_is_uncertain_not_unfavourable():
    """PCSK9 × MI: 'market size: No (ultra-rare)' from another disorder's prevalence."""
    verdict = LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens="commercial",
        target_gene="PCSK9",
        disease="myocardial infarction",
        overall_verdict="oppose",
        axes=[
            AxisVerdict(axis="ip_landscape", verdict=False),
            AxisVerdict(axis="market_size", verdict=False),
        ],
    )
    out = _market_size_unsized(verdict, [])
    assert [a.verdict for a in out.axes] == [False, None]
    assert [f.rule_id for f in out.validation_flags] == ["market_size_unsized"]
    favourable = verdict.model_copy(
        update={"axes": [AxisVerdict(axis="market_size", verdict=True)]}
    )
    assert _market_size_unsized(favourable, []) is favourable


def test_ultra_rare_without_an_indication_record_is_flagged():
    from services.evidence.commercial_interpret import check_commercial_guards

    text = "The addressable population for myocardial infarction is ultra-rare."
    assert [
        v.rule_id for v in check_commercial_guards(text, indication_prevalence_found=False)
    ] == ["commercial.rarity_unsupported"]
    sized = "The population is ultra-rare (1-9 / 1,000,000, Orphanet)."
    assert check_commercial_guards(sized, indication_prevalence_found=True) == []
