# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""BioGRID ORCS connector, on rows in the shape of BioGRID's own examples
(BioGRID/ORCS-REST-EXAMPLES). The access key must never reach an exception's text: gather
writes that text into the run's records."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.biogrid_orcs import tools

_B = "https://orcsws.thebiogrid.org"
_KEY = "k" * 32
_SCORE = {"SCREEN_ID": "17", "HIT": "YES", "SCORE.1": "20.1", "IDENTIFIER_ID": "7023"}
_SCREEN = {
    "SCREEN_ID": "17", "PHENOTYPE": "viability", "SCREEN_TYPE": "Negative Selection",
    "EXPERIMENTAL_SETUP": "Timecourse", "CONDITION_NAME": "-", "CELL_LINE": "HeLa",
    "SIGNIFICANCE_CRITERIA": "Score.1 (Bayes Factor) > 15.47", "SCORE.1_TYPE": "Bayes Factor",
    "SOURCE_TYPE": "pubmed", "SOURCE_ID": "26627737", "AUTHOR": "Hart T (2015)",
    "METHODOLOGY": "Knockout", "CELL_TYPE": "Cervical Adenocarcinoma Cell Line",
}  # fmt: skip


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tools.ENV_KEY, _KEY)


@respx.mock
async def test_joins_each_score_to_its_screen() -> None:
    respx.get(f"{_B}/gene/7023").mock(return_value=httpx.Response(200, json=[_SCORE]))
    route = respx.get(f"{_B}/screens/").mock(return_value=httpx.Response(200, json=[_SCREEN]))
    b = await tools.get_gene_screens(7023)
    [s] = b.screens
    assert (s.hit, s.phenotype, s.pmid, s.condition) == ("YES", "viability", "26627737", "")
    assert s.significance_criteria.startswith("Score.1")
    assert route.calls[0].request.url.params["screenID"] == "17"
    assert _KEY not in b.source_link


@respx.mock
async def test_the_service_error_payload_raises() -> None:
    respx.get(f"{_B}/gene/7023").mock(
        return_value=httpx.Response(
            200, json={"STATUS": "ERROR", "MESSAGE": "Your Access Key is Not"}
        )
    )
    with pytest.raises(MCPToolError, match="Access Key"):
        await tools.get_gene_screens(7023)


@respx.mock
@pytest.mark.parametrize("failure", ["status", "connection"])
async def test_the_access_key_never_reaches_an_exception_or_a_log(
    failure: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "_BACKOFF_S", 0)
    caplog.set_level(logging.DEBUG)
    route = respx.get(f"{_B}/gene/7023")
    if failure == "status":
        route.mock(return_value=httpx.Response(403))
    else:
        route.mock(side_effect=httpx.ConnectError("to https://x/?accesskey=" + _KEY))
    with pytest.raises(MCPToolError) as err:
        await tools.get_gene_screens(7023)
    assert _KEY not in str(err.value) and err.value.__cause__ is None
    assert not [r for r in caplog.records if _KEY in r.getMessage()]


def test_configured_only_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tools.orcs_configured()
    monkeypatch.setenv(tools.ENV_KEY, "  ")
    assert not tools.orcs_configured()
