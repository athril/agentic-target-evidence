# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinVar connector: esummary records reduced to the gene's own P/LP calls, each with the
condition it is for. Offline: E-utilities replies are built in the tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.clinvar.tools as clinvar


def _summary(
    title: str,
    spdi: str,
    sig: str,
    review: str,
    traits: list[tuple[str, str]],
    scvs: int,
    consequences: list[str],
) -> dict[str, Any]:
    return {
        "accession": f"VCV{abs(hash(title)) % 10**9:09d}", "title": title,
        "variation_set": [{"canonical_spdi": spdi}],
        "molecular_consequence_list": consequences,
        "germline_classification": {
            "description": sig, "review_status": review,
            "trait_set": [
                {"trait_name": n, "trait_xrefs": [{"db_source": "MONDO", "db_id": m}] if m else []}
                for n, m in traits
            ],
        },
        "supporting_submissions": {"scv": [f"SCV{i}" for i in range(scvs)]},
    }  # fmt: skip


_SUMMARIES = [
    _summary("NM_1.4(TST1):c.1120G>T (p.Asp374Tyr)", "NC_000001.11:55057453:G:T",
             "Pathogenic/Likely pathogenic", "criteria provided, multiple submitters, no conflicts",
             [("Familial hypercholesterolemia", "MONDO:0005439"), ("not provided", "")], 4,
             ["missense variant"]),
    _summary("NM_1.4(TST1):c.1120G>C (p.Asp374His)", "NC_000001.11:55057453:G:C",
             "Pathogenic", "criteria provided, single submitter", [("Hypercholesterolemia", "")], 1,
             ["missense variant"]),
    _summary("NM_1.4(TST1):c.42_43insTG (p.Leu15fs)", "NC_000001.11:55039879::TG",
             "Pathogenic", "criteria provided, single submitter", [], 1, ["frameshift variant"]),
    _summary("NM_1.4(TST1):c.399+1G>A", "NC_000001.11:55044034:G:A", "Likely pathogenic",
             "criteria provided, single submitter", [], 1,
             ["splice donor variant", "intron variant"]),
    _summary("NM_1.4(TST1):c.10C>T (p.Gln4Ter)", "NC_000001.11:55039800:C:T",
             "Pathogenic; risk factor", "no assertion criteria provided", [], 1, ["nonsense"]),
    _summary("GRCh38/hg38 1p32.3-p31.1(chr1:50000000-60000000)x1", "", "Pathogenic",
             "criteria provided, single submitter", [("See cases", "")], 1, []),
    _summary("NM_1.4(TST1):c.20A>G (p.Lys7Arg)", "NC_000001.11:55039810:A:G",
             "Conflicting classifications of pathogenicity",
             "criteria provided, conflicting classifications", [], 3, ["missense variant"]),
    _summary("NM_9.1(OTHER):c.5A>G (p.Lys2Arg)", "NC_000001.11:55039900:A:G", "Pathogenic",
             "criteria provided, single submitter", [], 1, ["missense variant"]),
]  # fmt: skip


def test_calls_keep_the_condition_review_and_allele_of_the_genes_own_alleles():
    p, lp = clinvar.parse_summaries(_SUMMARIES, "TST1")
    calls = {c.hgvsc: c for c in (*p, *lp)}

    # copy-number, conflicting and other-gene records are left out
    assert set(calls) == {"c.1120G>T", "c.1120G>C", "c.42_43insTG", "c.399+1G>A", "c.10C>T"}
    y = calls["c.1120G>T"]
    assert (y.variant_id, y.hgvsp, y.transcript) == ("1-55057454-G-T", "p.Asp374Tyr", "NM_1.4")
    assert (y.gold_stars, y.submissions) == (2, 4)
    assert [(c.name, c.mondo_id) for c in y.conditions] == [
        ("Familial hypercholesterolemia", "MONDO:0005439")
    ]
    assert y in lp  # "Pathogenic/Likely pathogenic" sits with likely pathogenic, as in gnomAD
    assert calls["c.1120G>C"].variant_id == "1-55057454-G-C"
    assert calls["c.42_43insTG"].variant_id == ""  # an insertion's VCF form needs the anchor
    assert calls["c.399+1G>A"].major_consequence == "splice_donor_variant"
    assert calls["c.10C>T"].major_consequence == "stop_gained"
    assert calls["c.10C>T"].clinical_significance == "Pathogenic"


@pytest.mark.asyncio
async def test_the_fetch_summarises_every_id_in_batches_and_reports_the_match_count():
    ids = [str(i) for i in range(len(_SUMMARIES))]
    replies = [
        {"esearchresult": {"count": "8", "idlist": ids}},
        {"result": {"uids": ids, **dict(zip(ids, _SUMMARIES, strict=True))}},
    ]
    get = AsyncMock(side_effect=replies)
    with patch.object(clinvar, "_rate_limited_get", get):
        bundle = await clinvar.get_pathogenic_variants("TST1")

    assert get.await_args_list[0].args[1]["db"] == "clinvar"
    assert (bundle.records_found, bundle.capped) == (8, False)
    assert len(bundle.pathogenic) + len(bundle.likely_pathogenic) == 5
    assert "1 name the condition" not in bundle.text and "(2 name the condition" in bundle.text
