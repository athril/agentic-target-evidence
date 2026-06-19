# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Smoke test: live USPTO ODP API call + Langfuse telemetry trace.

Verifies:
  1. search_patents() returns at least one PatentRecord for a well-known target.
  2. The call is wrapped in a Langfuse span that appears in the UI.

Requirements:
  - USPTO_API_KEY in .env
  - Langfuse running at LANGFUSE_BASE_URL (default http://localhost:3000)
  - Internet access (api.uspto.gov)

Run:
    pytest tests/smoke/test_uspto_smoke.py -v -s -m smoke
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.smoke


def _uspto_reachable() -> bool:
    try:
        r = httpx.head("https://api.uspto.gov", timeout=5)
        return r.status_code < 500
    except Exception:
        return False


if not _uspto_reachable():
    pytest.skip("api.uspto.gov not reachable", allow_module_level=True)


async def test_search_patents_live_and_traced() -> None:
    """Hit the live ODP API, wrap in a Langfuse span, assert results arrive."""
    from core.telemetry.langfuse import span
    from core.telemetry.setup import init_telemetry
    from mcp_servers.uspto.tools import PatentRecord, search_patents

    init_telemetry()

    trace_id = f"smoke-uspto-{uuid.uuid4().hex[:8]}"
    gene, disease = "PTPN1", "pancreatic cancer"

    async with span(
        "smoke.uspto.search_patents",
        trace_id,
        input_data=f"gene={gene} disease={disease}",
        attributes={"smoke.gene": gene, "smoke.disease": disease},
    ) as current_span:
        records = await search_patents(gene, disease)

        count = len(records)
        current_span.set_attribute("uspto.result_count", count)
        current_span.set_attribute("gen_ai.completion", f"{count} patent(s) returned")

        print(f"\n[smoke] USPTO records returned: {count}")
        for r in records[:3]:
            print(f"  patent_id={r.patent_id!r}  title={r.title[:60]!r}  assignee={r.assignee!r}")

    # Flush so the span reaches Langfuse before pytest exits
    try:
        from langfuse import Langfuse

        Langfuse().flush()
        print(f"[smoke] Langfuse flushed — look for trace_id={trace_id!r}")
    except Exception as exc:
        print(f"[smoke] Langfuse flush skipped: {exc}")

    assert count > 0, (
        f"Expected >0 patents for {gene!r}/{disease!r}, got 0. "
        "Check USPTO_API_KEY and api.uspto.gov availability."
    )
    assert all(isinstance(r, PatentRecord) for r in records)
    assert all(r.patent_id for r in records), "Every record must have a patent_id"
