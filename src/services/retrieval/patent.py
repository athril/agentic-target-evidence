# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Patent retrieval service — deterministic MCP-backed fetch.

Replaces PatentAgent.act() as the canonical retrieval logic.
The graph calls this directly; PatentAgent is a thin wrapper for backward compat.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.uspto.tools import PatentRecord, search_patents
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/patent"


def _render_markdown(record: PatentRecord) -> str:
    return (
        f"# {record.title}\n\n"
        f"**Patent ID:** {record.patent_id}  \n"
        f"**Assignee:** {record.assignee or '—'}  \n"
        f"**Filing Date:** {record.filing_date or '—'}  \n"
        f"**Google Patents:** {record.source_link}  \n"
        f"**USPTO Patent Center:** {record.uspto_link}\n\n"
        f"## Abstract\n\n{record.abstract or '_No abstract available._'}\n"
    )


async def fetch_patents(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> list[Evidence]:
    """Fetch patent evidence for a gene/disease pair."""
    async with span(_SERVICE, trace_id=trace_id, input_data=f"{gene}|{disease}"):
        records = await search_patents(gene, disease)

    prov = make_provenance(_SERVICE, "search_patents", trace_id)
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []
    for r in records:
        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "patents",
            f"{r.app_number}.md",
            _render_markdown(r),
        )
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.PATENT,
                scope="abstract",
                source=r.app_number,
                source_link=r.source_link,
                query_used=r.query_used or None,
                artifact_uri=uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=prov,
                direction=direction_enum,
                extra={
                    "title": r.title,
                    "abstract": r.abstract,
                    "assignee": r.assignee,
                    "filing_date": r.filing_date,
                    "uspto_link": r.uspto_link,
                },
            )
        )
    return evidences
