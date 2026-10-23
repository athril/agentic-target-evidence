# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PubTator3 annotations (mcp_servers/pubtator): parsing a recorded BioC JSON export,
batching, the per-PMID cache, and a degradation when a batch fails."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_servers.pubtator import tools
from mcp_servers.pubtator.tools import get_annotations, parse_biocjson

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "pubtator"
_URL = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"


def _fixture(pmid: str) -> dict:
    return json.loads((_FIXTURES / f"{pmid}.json").read_text())


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tools, "_CACHE_DIR", tmp_path)

    async def no_wait(*_: object) -> None:
        return None

    monkeypatch.setattr("mcp_servers.pubmed.tools._throttle", no_wait)
    monkeypatch.setattr("mcp_servers.pubmed.tools._backoff_sleep", no_wait)


def test_a_variant_carries_its_gene_rs_id_and_hgvs():
    (doc,) = parse_biocjson(_fixture("16096813"))
    variants = {a.text: a for p in doc.passages for a in p.annotations if a.type == "Variant"}

    assert doc.pmid == "16096813"
    assert (variants["C-629A"].gene_id, variants["C-629A"].rsid) == ("1071", "rs1800775")
    assert variants["C-629A"].hgvs == "c.-629C>A"
    assert (variants["I405V"].rsid, variants["I405V"].hgvs) == ("rs5882", "p.I405V")


def test_relations_keep_both_identifiers_types_and_names():
    (doc,) = parse_biocjson(_fixture("16096813"))
    first = doc.relations[0]

    assert len(doc.relations) == 8
    assert (first.type, first.role1, first.role1_type, first.role1_name) == (
        "Positive_Correlation",
        "MESH:D002784",
        "Chemical",
        "Cholesterol",
    )
    assert (first.role2, first.role2_type, first.role2_name) == ("351", "Gene", "APP")


def test_offsets_are_into_the_whole_document():
    (doc,) = parse_biocjson(_fixture("16096813"))
    abstract = doc.passages[1]
    first = abstract.annotations[0]

    start = first.offset - abstract.offset
    assert abstract.text[start : start + first.length] == first.text


@respx.mock
async def test_pmids_are_fetched_once_then_served_from_the_cache():
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json=_fixture("16096813")))

    first = await get_annotations(["PMID:16096813", "16096813", "99999999"])
    second = await get_annotations(["16096813"])

    assert [d.pmid for d in first] == ["16096813"]  # 99999999 is not annotated
    assert [d.pmid for d in second] == ["16096813"]
    assert route.call_count == 1
    assert route.calls[0].request.url.params["pmids"] == "16096813,99999999"


@respx.mock
async def test_requests_are_batched_by_one_hundred():
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json={"PubTator3": []}))

    await get_annotations([str(n) for n in range(1, 151)])

    assert [len(c.request.url.params["pmids"].split(",")) for c in route.calls] == [100, 50]


@respx.mock
async def test_a_failed_batch_is_a_degradation(monkeypatch: pytest.MonkeyPatch):
    recorded: list[str] = []
    monkeypatch.setattr(tools, "record_degradation", lambda c, *_: recorded.append(c))
    respx.get(_URL).mock(return_value=httpx.Response(404))

    assert await get_annotations(["123"]) == []
    assert recorded == ["literature: PubTator3 annotations"]


@respx.mock
async def test_a_rejected_pmid_costs_only_itself(monkeypatch: pytest.MonkeyPatch):
    """A 400 for one PMID of a batch: the batch is split in halves down to that PMID, and
    the other papers are still annotated."""
    recorded: list[str] = []
    monkeypatch.setattr(tools, "record_degradation", lambda _c, cause, *_: recorded.append(cause))
    bad = "77"
    doc = _fixture("16096813")["PubTator3"][0]

    def reply(request: httpx.Request) -> httpx.Response:
        pmids = request.url.params["pmids"].split(",")
        if bad in pmids:
            return httpx.Response(400)
        return httpx.Response(200, json={"PubTator3": [{**doc, "pmid": p} for p in pmids]})

    route = respx.get(_URL).mock(side_effect=reply)
    wanted = [str(n) for n in range(1, 101)]

    docs = await get_annotations(wanted)

    assert [d.pmid for d in docs] == [p for p in wanted if p != bad]
    assert recorded == [f"HTTP 400 for PMID(s) {bad}"]
    # 1 batch, then one rejected half per level down to the single PMID: 1 + 2 * 7.
    assert route.call_count == 15
