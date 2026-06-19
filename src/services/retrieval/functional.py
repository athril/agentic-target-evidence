# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Functional genomics retrieval service — MCP-backed fetch.

Queries DepMap and Project Score CRISPR dependency (NON_SENSITIVE) and
internal CRISPR screens (SENSITIVE) via their respective MCP servers.
"""

from __future__ import annotations

import json
import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.depmap.tools import get_dependency
from mcp_servers.impc.tools import get_impc_phenotypes
from mcp_servers.internal_data.tools import query_internal_db
from mcp_servers.project_score.tools import get_project_score
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/functional"

_SCREENS_SQL = """
SELECT gene_symbol, screen_id, cell_line, cancer_type,
       gene_effect, is_essential, dataset_version
FROM functional_screens
WHERE gene_symbol = '{gene}'
ORDER BY gene_effect ASC
LIMIT 200
"""


async def fetch_functional(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> list[Evidence]:
    """Fetch functional genomics evidence from internal screens + public DepMap."""
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []

    # Internal CRISPR screens (SENSITIVE).
    sql = _SCREENS_SQL.format(gene=gene)
    async with span(f"{_SERVICE}:internal_screens", trace_id=trace_id, input_data=sql) as db_span:
        rows = await query_internal_db(sql)
        db_span.set_attribute("output", f"{len(rows)} rows returned")

    if rows:
        archive_rows = [{k: v for k, v in r.items() if k != "_classification"} for r in rows]
        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "functional",
            f"{gene}_screens.json",
            json.dumps(archive_rows, indent=2, default=str),
        )
        prov = make_provenance(_SERVICE, "query_internal_db", trace_id)
        for row in rows:
            evidences.append(
                Evidence(
                    evidence_id=uuid.uuid4(),
                    run_id=run_id,
                    gene=gene,
                    gene_id=gene_id,
                    disease=disease,
                    disease_id=disease_id,
                    evidence_type=EvidenceType.FUNCTIONAL_GENOMICS,
                    scope="abstract",
                    source=row.get("screen_id", "internal"),
                    source_link=f"internal://functional_screens/{row.get('screen_id', 'unknown')}",
                    artifact_uri=uri,
                    classification=DataClass.SENSITIVE,
                    provenance=prov,
                    direction=direction_enum,
                    extra={k: v for k, v in row.items() if k != "_classification"},
                )
            )

    # Public DepMap dependency (NON_SENSITIVE).
    async with span(f"{_SERVICE}:depmap", trace_id=trace_id, input_data=gene) as ds:
        bundle = await get_dependency(gene)
        ds.set_attribute("output", bundle.text)

    dep_uri = archive_raw(
        gene,
        disease_id,
        direction_enum.value,
        "functional",
        f"{gene}_depmap.json",
        bundle.model_dump_json(indent=2),
    )
    depmap_prov = make_provenance(_SERVICE, "depmap.get_dependency", trace_id)
    evidences.append(
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=run_id,
            gene=gene,
            gene_id=gene_id,
            disease=disease,
            disease_id=disease_id,
            evidence_type=EvidenceType.FUNCTIONAL_GENOMICS,
            scope="abstract",
            source=f"depmap:{gene}",
            source_link=bundle.source_link,
            artifact_uri=dep_uri,
            classification=DataClass.NON_SENSITIVE,
            provenance=depmap_prov,
            direction=direction_enum,
            extra=bundle.model_dump(),
        )
    )

    # Public Project Score (Sanger) CRISPR fitness (NON_SENSITIVE) — a second,
    # largely non-overlapping cell-line panel corroborating/extending DepMap.
    async with span(f"{_SERVICE}:project_score", trace_id=trace_id, input_data=gene) as ps_span:
        try:
            score_bundle = await get_project_score(gene)
            ps_span.set_attribute("output", score_bundle.text)
        except Exception as exc:
            ps_span.set_attribute("error", str(exc))
            score_bundle = None

    if score_bundle and score_bundle.sidg_id:
        score_uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "functional",
            f"{gene}_project_score.json",
            score_bundle.model_dump_json(indent=2),
        )
        score_prov = make_provenance(_SERVICE, "project_score.get_project_score", trace_id)
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.FUNCTIONAL_GENOMICS,
                scope="abstract",
                source=f"project_score:{gene}",
                source_link=score_bundle.source_link,
                artifact_uri=score_uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=score_prov,
                direction=direction_enum,
                extra=score_bundle.model_dump(),
            )
        )

    # Public IMPC knockout-mouse phenotype/viability (NON_SENSITIVE) — the
    # whole-organism analogue of DepMap's cell-line dependency signal.
    async with span(f"{_SERVICE}:impc", trace_id=trace_id, input_data=gene) as impc_span:
        try:
            impc_bundle = await get_impc_phenotypes(gene)
            impc_span.set_attribute("output", impc_bundle.text)
        except Exception as exc:
            impc_span.set_attribute("error", str(exc))
            impc_bundle = None

    if impc_bundle and impc_bundle.phenotypes:
        impc_uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "functional",
            f"{gene}_impc.json",
            impc_bundle.model_dump_json(indent=2),
        )
        impc_prov = make_provenance(_SERVICE, "impc.get_impc_phenotypes", trace_id)
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.FUNCTIONAL_GENOMICS,
                scope="abstract",
                source=f"impc:{gene}",
                source_link=impc_bundle.source_link,
                artifact_uri=impc_uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=impc_prov,
                direction=direction_enum,
                extra=impc_bundle.model_dump(),
            )
        )

    return evidences
