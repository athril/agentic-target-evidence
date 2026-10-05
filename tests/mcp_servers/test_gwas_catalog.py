# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GWAS Catalog MCP tools (REST API v2)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.gwas_catalog import tools
from mcp_servers.gwas_catalog.tools import GWASBundle, GWASHit, get_gwas_associations

_BASE = "https://www.ebi.ac.uk/gwas/rest/api/v2"
_ASSOC_URL = f"{_BASE}/associations"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "_BACKOFF_S", 0.0)


def _assoc(
    assoc_id: int,
    trait: str = "lung cancer",
    efo_id: str = "EFO_0001071",
    mantissa: int = 1,
    exponent: int = -10,
    rs_id: str = "rs123456",
    **extra: Any,
) -> dict[str, Any]:
    p = mantissa * 10.0**exponent
    return {
        "association_id": assoc_id,
        "p_value": p if p > 1e-300 else 0.0,
        "pvalue_mantissa": mantissa,
        "pvalue_exponent": exponent,
        "risk_frequency": "0.35",
        "beta": "-",
        "efo_traits": [{"efo_id": efo_id, "efo_trait": trait}] if efo_id else [],
        "reported_trait": [trait],
        "accession_id": f"GCST{assoc_id}",
        "pubmed_id": "12345678",
        "snp_allele": [{"rs_id": rs_id, "effect_allele": "A"}],
        "mapped_genes": ["PRMT5"],
        **extra,
    }


def _page(assocs: list[dict[str, Any]], number: int = 0, total_pages: int = 1) -> dict[str, Any]:
    return {
        "_embedded": {"associations": assocs},
        "page": {
            "size": 100,
            "totalElements": len(assocs),
            "totalPages": total_pages,
            "number": number,
        },
    }


def _mock_pages(*pages: dict[str, Any]) -> respx.Route:
    for i, body in enumerate(pages):
        respx.get(_ASSOC_URL, params={"page": str(i)}).mock(
            return_value=httpx.Response(200, json=body)
        )
    return respx.get(_ASSOC_URL)


def _mock_enrichment(status: int = 404) -> None:
    respx.get(url__regex=rf"{_BASE}/(studies|publications)/.*").mock(
        return_value=httpx.Response(status, json={})
    )


@respx.mock
async def test_get_gwas_associations_returns_bundle() -> None:
    route = respx.get(
        _ASSOC_URL,
        params={"mapped_gene": "PRMT5", "sort": "p_value", "direction": "asc", "page": "0"},
    ).mock(return_value=httpx.Response(200, json=_page([_assoc(9001)])))
    respx.get(f"{_BASE}/studies/GCST9001").mock(
        return_value=httpx.Response(200, json={"initial_sample_size": "10,000 European"})
    )
    respx.get(f"{_BASE}/publications/12345678").mock(
        return_value=httpx.Response(
            200,
            json={
                "publication_date": "2020-01-15",
                "journal": "Nat Genet",
                "title": "GWAS of lung cancer risk.",
            },
        )
    )

    bundle = await get_gwas_associations("PRMT5")

    assert route.called
    assert isinstance(bundle, GWASBundle)
    assert bundle.gene_symbol == "PRMT5"
    assert len(bundle.hits) == 1
    hit = bundle.hits[0]
    assert isinstance(hit, GWASHit)
    assert hit.rs_id == "rs123456"
    assert hit.association_id == "9001"
    assert hit.pvalue == pytest.approx(1e-10)
    assert hit.trait == "lung cancer"
    assert hit.efo_id == "EFO_0001071"
    assert hit.study_accession == "GCST9001"
    assert hit.pmid == "12345678"
    assert hit.initial_sample_size == "10,000 European"
    assert (hit.journal, hit.pub_date) == ("Nat Genet", "2020-01-15")
    assert "PRMT5" in bundle.source_link
    assert "1 genome-wide significant" in bundle.text


@respx.mock
async def test_get_gwas_associations_no_hits() -> None:
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=_page([])))
    _mock_enrichment()

    bundle = await get_gwas_associations("PRMT5")

    assert bundle.hits == []
    assert "No genome-wide significant" in bundle.text


@respx.mock
async def test_persistent_http_error_raises_instead_of_reporting_empty() -> None:
    """The v1 connector read every 429 as 'no associations'. v2 must surface failures."""
    route = respx.get(_ASSOC_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_gwas_associations("PRMT5")
    assert route.call_count == tools._MAX_ATTEMPTS


@respx.mock
async def test_rate_limit_is_retried() -> None:
    respx.get(_ASSOC_URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=_page([_assoc(1)]))]
    )
    _mock_enrichment()

    bundle = await get_gwas_associations("PRMT5")
    assert len(bundle.hits) == 1


@respx.mock
async def test_client_error_is_not_retried() -> None:
    route = respx.get(_ASSOC_URL).mock(return_value=httpx.Response(400))
    with pytest.raises(MCPToolError, match="HTTP 400"):
        await get_gwas_associations("PRMT5")
    assert route.call_count == 1


@respx.mock
async def test_filters_by_p_threshold() -> None:
    page = _page([_assoc(1, exponent=-9), _assoc(2, exponent=-5)])
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations("BRCA1", p_threshold=5e-8)
    assert [h.association_id for h in bundle.hits] == ["1"]


@respx.mock
async def test_underflowed_p_values_sort_by_mantissa_and_exponent() -> None:
    """p_value is 0.0 below ~1e-308; ordering must use mantissa/exponent."""
    page = _page([_assoc(1, mantissa=6, exponent=-29), _assoc(2, mantissa=3, exponent=-1364)])
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations("PCSK9")
    assert [h.association_id for h in bundle.hits] == ["2", "1"]
    assert bundle.hits[0].pvalue == 0.0
    assert "3e-1364" in bundle.text


@respx.mock
async def test_deduplicates_by_association_id() -> None:
    respx.get(_ASSOC_URL).mock(
        return_value=httpx.Response(200, json=_page([_assoc(7777), _assoc(7777)]))
    )
    _mock_enrichment()

    bundle = await get_gwas_associations("GENE1")
    assert len(bundle.hits) == 1


def test_parse_beta_and_odds_ratio() -> None:
    hit = tools._parse_hit(_assoc(1, beta="0.2466 unit increase", or_per_copy_num=1.3))
    assert (hit.beta_num, hit.beta_unit, hit.beta_direction) == (0.2466, "unit", "increase")
    assert hit.or_per_copy == pytest.approx(1.3)
    assert tools._parse_beta("-") == (None, None, None)
    assert tools._parse_beta("0.05 SD decrease") == (0.05, "SD", "decrease")


@respx.mock
async def test_enrichment_failure_is_not_fatal() -> None:
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=_page([_assoc(1)])))
    _mock_enrichment(status=500)

    bundle = await get_gwas_associations("PRMT5")
    assert len(bundle.hits) == 1
    assert bundle.hits[0].initial_sample_size == ""


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


@respx.mock
async def test_unscoped_query_stops_once_enough_hits() -> None:
    first = _page([_assoc(i) for i in range(5)], number=0, total_pages=10)
    route = _mock_pages(first)
    _mock_enrichment()

    bundle = await get_gwas_associations("PCSK9", max_hits=5)
    assert len(bundle.hits) == 5
    assert route.call_count == 0  # only the page-0 route was used
    assoc_calls = [c for c in respx.calls if c.request.url.path.endswith("/associations")]
    assert len(assoc_calls) == 1


@respx.mock
async def test_scoped_query_pages_until_significance_ends() -> None:
    p0 = _page([_assoc(1, trait="height", efo_id="EFO_0004339")], number=0, total_pages=4)
    p1 = _page([_assoc(2, trait="pancreatic cancer", efo_id="EFO_0003860")], number=1)
    p2 = _page([_assoc(3, exponent=-5)], number=2)  # non-significant → stop after this batch
    _mock_pages(p0, p1, p2, _page([], number=3))
    _mock_enrichment()

    bundle = await get_gwas_associations("PRMT5", efo_ids={"EFO_0003860"})
    assert [h.association_id for h in bundle.hits] == ["2"]
    assert bundle.dropped_off_target == 1


# ---------------------------------------------------------------------------
# EFO disease-scope filtering
# ---------------------------------------------------------------------------


@respx.mock
async def test_efo_scope_keeps_matching_hits() -> None:
    page = _page(
        [
            _assoc(1, trait="pancreatic cancer", efo_id="EFO_0003860"),
            _assoc(2, trait="height", efo_id="EFO_0004339"),
        ]
    )
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations("PRMT5", efo_ids={"EFO_0003860", "EFO_0002618"})

    assert len(bundle.hits) == 1
    assert bundle.hits[0].efo_id == "EFO_0003860"
    assert bundle.dropped_off_target == 1
    assert "pancreatic cancer" in bundle.kept_traits
    assert "height" in bundle.all_traits
    assert "height" not in bundle.kept_traits


@respx.mock
async def test_efo_scope_drops_all_off_target() -> None:
    page = _page([_assoc(3, trait="height", efo_id="EFO_0004339")])
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations("PRMT5", efo_ids={"EFO_0003860"})

    assert bundle.hits == []
    assert bundle.dropped_off_target == 1
    assert "height" in bundle.all_traits
    assert bundle.kept_traits == []
    assert "off-indication" in bundle.text


@respx.mock
async def test_efo_scope_none_preserves_old_behavior() -> None:
    page = _page([_assoc(4, trait="height", efo_id="EFO_0004339")])
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations("GENE_X")

    assert len(bundle.hits) == 1
    assert bundle.dropped_off_target == 0


@respx.mock
async def test_trait_term_fallback_matches_substring() -> None:
    page = _page([_assoc(5, trait="pancreatic adenocarcinoma", efo_id="MONDO_0005105")])
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations(
        "PRMT5", efo_ids={"EFO_0003860"}, trait_terms=["pancreatic"]
    )

    assert len(bundle.hits) == 1
    assert bundle.dropped_off_target == 0


@respx.mock
async def test_max_hits_cap_applied_after_filter() -> None:
    page = _page(
        [
            _assoc(100, trait="pancreatic cancer", efo_id="EFO_0003860", exponent=-10),
            _assoc(101, trait="pancreatic cancer", efo_id="EFO_0003860", exponent=-9),
        ]
    )
    respx.get(_ASSOC_URL).mock(return_value=httpx.Response(200, json=page))
    _mock_enrichment()

    bundle = await get_gwas_associations("PRMT5", efo_ids={"EFO_0003860"}, max_hits=1)

    assert len(bundle.hits) == 1
    assert bundle.hits[0].pvalue == pytest.approx(1e-10)
