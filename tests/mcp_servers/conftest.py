# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Connector tests never wait out real retry backoff (rate-limit retries are mocked)."""

from __future__ import annotations

import pytest

from mcp_servers.clinicaltrials import tools as clinicaltrials_tools
from mcp_servers.pubmed import tools as pubmed_tools


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pubmed_tools, "_backoff_sleep", _no_sleep)
    monkeypatch.setattr(clinicaltrials_tools, "_backoff_sleep", _no_sleep)
