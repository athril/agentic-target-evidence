# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The degradation ledger (core/degradations.py) and the lookups that record into it.

The failure these prevent: a cross-ontology disease lookup failed with a DNS error in every
run of a development panel. The failure was cached for the rest of each run, and its only
trace was one WARNING line per log, found by grepping.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core import http as http_mod
from core.degradations import collecting, record_degradation, render_degradations
from core.exceptions import MCPToolError
from mcp_servers.opentargets import tools as ot
from schemas.state import merge_degradations


async def test_a_node_returns_what_was_recorded_while_it_ran():
    async def node(state: dict[str, Any]) -> dict[str, Any]:
        record_degradation("disease scope: parents", "ConnectError: dns", "fewer names")
        return {"x": 1}

    out = await collecting("scope_node", node)({})

    assert out["x"] == 1
    assert out["degradations"] == [
        {
            "component": "disease scope: parents",
            "cause": "ConnectError: dns",
            "effect": "fewer names",
            "node": "scope_node",
        }
    ]


async def test_a_failed_source_is_a_degradation_and_signatures_are_kept():
    async def node(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return {"failed_sources": ["gbd"]}

    wrapped = collecting("gbd", node)

    assert list(inspect.signature(wrapped).parameters) == ["state", "config"]
    out = await wrapped({}, {})
    assert out["degradations"][0]["component"] == "source: gbd"


def test_outside_a_node_a_degradation_is_only_logged():
    record_degradation("network: example.org", "ConnectError", "nothing")  # no error


def test_the_ledger_keeps_one_entry_per_component_and_cause():
    a = {"component": "network: h", "cause": "c", "effect": "e", "node": "n1"}
    b = {**a, "node": "n2"}
    c = {**a, "cause": "other"}

    assert merge_degradations([a], [b, c]) == [a, c]


def test_the_dossier_lists_degradations_and_says_nothing_for_a_clean_run():
    entry = {"component": "network: h", "cause": "ConnectError", "effect": "data missing"}

    assert render_degradations([]) == ""
    assert "Degraded run" in render_degradations([entry])
    assert "network: h" in render_degradations([entry])


async def test_a_request_that_never_connects_is_recorded_with_its_host(monkeypatch):
    waits: list[float] = []

    async def _sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(http_mod.asyncio, "sleep", _sleep)

    async def node(state: dict[str, Any]) -> dict[str, Any]:
        async def send() -> httpx.Response:
            raise httpx.ConnectError("[Errno -2] Name or service not known")

        with pytest.raises(httpx.ConnectError):
            await http_mod._send_with_retry(send, "GET", "https://api.example.org/terms")
        return {}

    out = await collecting("n", node)({})

    assert waits == list(http_mod._CONNECT_BACKOFF)  # connect errors wait longer
    entry = out["degradations"][0]
    assert entry["component"] == "network: api.example.org"
    assert "ConnectError on GET https://api.example.org/terms" in entry["cause"]


async def test_a_failed_scope_lookup_is_recorded_and_retried_later_not_cached(monkeypatch):
    ot._disease_ontology_cache.clear()
    ot._disease_ontology_failed.clear()
    graphql = AsyncMock(return_value={"disease": {"name": "disease a", "descendants": []}})
    monkeypatch.setattr(ot, "_graphql", graphql)
    failing = AsyncMock(side_effect=MCPToolError("cross-reference parents failed: ConnectError"))

    async def node(state: dict[str, Any]) -> dict[str, Any]:
        await ot.get_disease_descendants("MONDO_1")
        return {}

    with patch("mcp_servers.ontology.tools.xref_parents", failing):
        out = await collecting("n", node)({})
        again = await ot.get_disease_descendants("MONDO_1")  # within the retry window

    assert out["degradations"][0]["component"] == "disease scope: cross-ontology parents"
    assert again.names_partial
    assert "MONDO_1" not in ot._disease_ontology_cache
    assert failing.await_count == 1

    monkeypatch.setattr(ot, "_RETRY_AFTER_S", 0.0)
    with patch("mcp_servers.ontology.tools.xref_parents", AsyncMock(return_value=[])):
        healed = await ot.get_disease_descendants("MONDO_1")

    assert ot._disease_ontology_cache["MONDO_1"] is healed
    ot._disease_ontology_cache.clear()
    ot._disease_ontology_failed.clear()
