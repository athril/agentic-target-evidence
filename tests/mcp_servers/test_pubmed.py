# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for PubMed MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from mcp_servers.pubmed import tools as pubmed_tools
from mcp_servers.pubmed.tools import (
    PubMedAbstract,
    PubMedFullText,
    PubMedRecord,
    _parse_jats_body,
    fetch_abstract,
    fetch_full_text,
    fetch_pmc_record,
    search_pubmed,
)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    pubmed_tools._request_times.clear()


_ESEARCH_HIT = {
    "esearchresult": {
        "idlist": ["12345678"],
        "webenv": "...",
        "querykey": "1",
    }
}

_ESUMMARY_HIT = {
    "result": {
        "12345678": {
            "title": "BRCA1 and Breast Cancer",
            "authors": [{"name": "Smith J"}, {"name": "Jones A"}],
            "source": "Nature",
            "fulljournalname": "Nature",
            "issn": "0028-0836",
            "essn": "1476-4687",
            "pubdate": "2022 Jan",
        }
    }
}


_EFETCH_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation><PMID>12345678</PMID>
      <Article><Abstract><AbstractText>Test abstract text.</AbstractText></Abstract></Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


@respx.mock
async def test_search_pubmed_returns_records() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_ESEARCH_HIT)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
        return_value=httpx.Response(200, json=_ESUMMARY_HIT)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=_EFETCH_XML)
    )

    records = await search_pubmed("BRCA1 AND breast cancer", max_results=10)
    assert len(records) == 1
    assert isinstance(records[0], PubMedRecord)
    assert records[0].pmid == "12345678"
    assert records[0].pub_year == 2022
    assert records[0].abstract == "Test abstract text."
    assert records[0].full_journal == "Nature"
    assert records[0].issn == "0028-0836"
    assert records[0].essn == "1476-4687"


@respx.mock
async def test_search_pubmed_returns_empty_on_no_hits() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    records = await search_pubmed("ZZZNOHITQUERY")
    assert records == []


@respx.mock
async def test_search_pubmed_raises_on_api_error() -> None:
    from core.exceptions import MCPToolError

    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(MCPToolError):
        await search_pubmed("any query")


@respx.mock
async def test_fetch_abstract_returns_abstract() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
        return_value=httpx.Response(200, json=_ESUMMARY_HIT)
    )

    abstract = await fetch_abstract("12345678")
    assert isinstance(abstract, PubMedAbstract)
    assert abstract.pmid == "12345678"
    assert abstract.pub_year == 2022


# elink ``linkname=pubmed_pmc`` responses, recorded from NCBI (2026-10). The PMC
# id is in ``linksetdbs[].links``; ``ids`` only echoes the queried PMID.
_ELINK_IN_PMC = {
    "header": {"type": "elink", "version": "0.3"},
    "linksets": [
        {
            "dbfrom": "pubmed",
            "ids": ["16060722"],
            "linksetdbs": [{"dbto": "pmc", "linkname": "pubmed_pmc", "links": ["1182327"]}],
        }
    ],
}
_ELINK_NOT_IN_PMC = {
    "header": {"type": "elink", "version": "0.3"},
    "linksets": [{"dbfrom": "pubmed", "ids": ["15879175"]}],
}


@respx.mock
async def test_fetch_full_text_available_in_pmc() -> None:
    elink = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
        return_value=httpx.Response(200, json=_ELINK_IN_PMC)
    )
    result = await fetch_pmc_record("16060722")
    assert isinstance(result, PubMedFullText)
    assert result.available is True
    assert result.pmc_id == "1182327"
    assert result.full_text_url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC1182327/"
    assert elink.calls.last.request.url.params["linkname"] == "pubmed_pmc"


@respx.mock
async def test_fetch_full_text_empty_links_does_not_raise() -> None:
    """A pubmed_pmc linksetdb whose link list is empty must not IndexError.

    Regression: pmc_id extraction previously indexed the id list unconditionally,
    crashing knowledge extraction with 'IndexError: list index out of range'.
    """
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
        return_value=httpx.Response(
            200,
            json={
                "linksets": [
                    {
                        "ids": ["12345678"],
                        "linksetdbs": [{"dbto": "pmc", "linkname": "pubmed_pmc", "links": []}],
                    }
                ]
            },
        )
    )
    result = await fetch_pmc_record("12345678")
    assert result.available is False
    assert result.pmc_id is None


@respx.mock
async def test_fetch_full_text_never_uses_echoed_pmid() -> None:
    """An article not in PMC echoes only its PMID in ``ids`` — never treat that as a PMC id."""
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
        return_value=httpx.Response(200, json=_ELINK_NOT_IN_PMC)
    )
    result = await fetch_pmc_record("15879175")
    assert result.available is False
    assert result.pmc_id is None
    assert result.full_text_url is None


@respx.mock
async def test_fetch_full_text_not_in_pmc() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
        return_value=httpx.Response(200, json={"linksets": []})
    )
    result = await fetch_pmc_record("99999999")
    assert result is not None
    assert result.available is False


@respx.mock
async def test_search_pubmed_retries_on_429(monkeypatch) -> None:
    """A single 429 response is retried and succeeds on the next attempt."""
    monkeypatch.setattr(pubmed_tools, "_RETRY_BASE_DELAY", 0.0)

    call_count = 0

    def _esearch_side_effect(_request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_ESEARCH_HIT)

    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        side_effect=_esearch_side_effect
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
        return_value=httpx.Response(200, json=_ESUMMARY_HIT)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=_EFETCH_XML)
    )

    records = await search_pubmed("BRCA1", max_results=10)
    assert len(records) == 1
    assert call_count == 2  # first attempt 429, second succeeds


@respx.mock
async def test_search_pubmed_raises_after_max_retries(monkeypatch) -> None:
    """Exhausting all retries raises MCPToolError."""
    from core.exceptions import MCPToolError

    monkeypatch.setattr(pubmed_tools, "_RETRY_BASE_DELAY", 0.0)
    monkeypatch.setattr(pubmed_tools, "_MAX_RETRIES", 3)

    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )

    with pytest.raises(MCPToolError, match="429"):
        await search_pubmed("BRCA1")


_PMC_JATS_XML = b"""<?xml version="1.0"?>
<pmc-articleset><article>
  <front><article-meta><abstract><p>Abstract prose.</p></abstract></article-meta></front>
  <body>
    <sec><title>Introduction</title><p>TRPC6 drives podocyte injury.</p></sec>
    <sec><title>Results</title>
      <p>Knockdown reduced proteinuria significantly.</p>
      <fig><caption><p>Figure caption noise</p></caption></fig>
      <table-wrap><label>Table 1</label><p>tabular noise</p></table-wrap>
    </sec>
    <ref-list><title>References</title><ref><p>Citation noise 2021</p></ref></ref-list>
  </body>
</article></pmc-articleset>"""

_PMC_JATS_NO_BODY = b"""<?xml version="1.0"?>
<pmc-articleset><article>
  <front><article-meta><abstract><p>Only metadata, not in OA subset.</p></abstract></article-meta></front>
</article></pmc-articleset>"""


def test_parse_jats_body_extracts_prose_and_skips_apparatus() -> None:
    text = _parse_jats_body(_PMC_JATS_XML)
    assert "Introduction" in text
    assert "TRPC6 drives podocyte injury." in text
    assert "Results" in text
    assert "Knockdown reduced proteinuria significantly." in text
    # References / figures / tables are stripped
    assert "Citation noise" not in text
    assert "Figure caption noise" not in text
    assert "tabular noise" not in text
    assert "References" not in text


def test_parse_jats_body_returns_empty_without_body() -> None:
    assert _parse_jats_body(_PMC_JATS_NO_BODY) == ""
    assert _parse_jats_body(b"not xml at all <") == ""


@respx.mock
async def test_fetch_full_text_returns_body() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=_PMC_JATS_XML)
    )
    text = await fetch_full_text("PMC123")
    assert "TRPC6 drives podocyte injury." in text


async def test_fetch_full_text_empty_pmc_id_no_request() -> None:
    # No respx mock registered: an empty id must short-circuit, not hit the network.
    assert await fetch_full_text("") == ""


@respx.mock
async def test_fetch_full_text_with_content_populates_body() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
        return_value=httpx.Response(200, json=_ELINK_IN_PMC)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=_PMC_JATS_XML)
    )
    result = await fetch_pmc_record("16060722", with_content=True)
    assert result.available is True
    assert result.pmc_id == "1182327"
    assert "Knockdown reduced proteinuria significantly." in result.full_text


@respx.mock
async def test_fetch_full_text_without_content_skips_body() -> None:
    """Default call (with_content=False) must not download the body."""
    elink = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
        return_value=httpx.Response(200, json=_ELINK_IN_PMC)
    )
    efetch = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=_PMC_JATS_XML)
    )
    result = await fetch_pmc_record("12345678")
    assert result.full_text == ""
    assert elink.called
    assert not efetch.called


def test_parse_abstracts_keeps_text_after_inline_markup() -> None:
    """Abstracts with <sup>/<i> children must not be truncated at the first inline tag."""
    xml = (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>28282151</PMID><Article>"
        '<Abstract><AbstractText Label="RESULTS">analysis using LC/MS<sup>E</sup>, which '
        "identified <i>C1orf87</i> at high   abundance.</AbstractText></Abstract>"
        "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )
    parsed = pubmed_tools._parse_abstracts_from_xml(xml)
    assert parsed == {
        "28282151": "RESULTS: analysis using LC/MSE, which identified C1orf87 at high abundance."
    }


_EFETCH_INDEXED_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation><PMID>12345678</PMID>
      <Article>
        <Abstract><AbstractText>Indexed abstract.</AbstractText></Abstract>
        <PublicationTypeList>
          <PublicationType UI="D016428">Journal Article</PublicationType>
          <PublicationType UI="D016449">Randomized Controlled Trial</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading>
          <DescriptorName UI="D000001" MajorTopicYN="N">Disease A</DescriptorName>
          <QualifierName UI="Q000188" MajorTopicYN="Y">drug therapy</QualifierName>
        </MeshHeading>
        <MeshHeading>
          <DescriptorName UI="D006801" MajorTopicYN="N">Humans</DescriptorName>
        </MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation><PMID>87654321</PMID>
      <Article><PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList></Article>
      <CommentsCorrectionsList>
        <CommentsCorrections RefType="RetractionIn"><PMID>99999999</PMID></CommentsCorrections>
      </CommentsCorrectionsList>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_metadata_keeps_publication_types_and_mesh_with_qualifiers() -> None:
    meta = pubmed_tools._parse_metadata_from_xml(_EFETCH_INDEXED_XML.decode())

    first = meta["12345678"]
    assert first.publication_types == ["Journal Article", "Randomized Controlled Trial"]
    assert [h.descriptor for h in first.mesh_headings] == ["Disease A", "Humans"]
    assert first.mesh_headings[0].qualifiers == ["drug therapy"]
    assert first.mesh_headings[0].major is True  # a major qualifier makes the heading major
    assert first.mesh_headings[1].major is False
    assert first.indexed is True
    assert first.retracted is False


def test_a_retraction_notice_marks_the_article_retracted_and_unindexed() -> None:
    meta = pubmed_tools._parse_metadata_from_xml(_EFETCH_INDEXED_XML.decode())

    second = meta["87654321"]
    assert second.retracted is True
    assert second.indexed is False  # no MeSH yet: "not indexed", not "lacks the heading"


@respx.mock
async def test_search_pubmed_records_carry_indexing_metadata() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_ESEARCH_HIT)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
        return_value=httpx.Response(200, json=_ESUMMARY_HIT)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=_EFETCH_INDEXED_XML)
    )

    [record] = await search_pubmed("q", max_results=10)

    assert record.abstract == "Indexed abstract."
    assert "Randomized Controlled Trial" in record.publication_types
    assert record.mesh_terms == ["Disease A", "Humans"]
    assert record.indexed is True


@respx.mock
async def test_publication_types_fall_back_to_esummary_when_efetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(pubmed_tools, "_backoff_sleep", AsyncMock())
    summary = {
        "result": {"12345678": {**_ESUMMARY_HIT["result"]["12345678"], "pubtype": ["Review"]}}
    }
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_ESEARCH_HIT)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
        return_value=httpx.Response(200, json=summary)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(503)
    )

    [record] = await search_pubmed("q", max_results=10)

    assert record.publication_types == ["Review"]
    assert record.indexed is False


@respx.mock
async def test_fetch_article_metadata_batches_and_skips_a_failed_batch(monkeypatch) -> None:
    monkeypatch.setattr(pubmed_tools, "_METADATA_BATCH", 1)
    monkeypatch.setattr(pubmed_tools, "_backoff_sleep", AsyncMock())
    route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi")
    route.side_effect = [
        httpx.Response(200, content=_EFETCH_INDEXED_XML),
        *[httpx.Response(503)] * pubmed_tools._MAX_RETRIES,
    ]

    meta = await pubmed_tools.fetch_article_metadata(["12345678", "87654321", "12345678"])

    assert "12345678" in meta  # the first batch's records are kept
    assert route.call_count == 1 + pubmed_tools._MAX_RETRIES  # duplicates fetched once
