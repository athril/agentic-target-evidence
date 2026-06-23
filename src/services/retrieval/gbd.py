# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GBD (Global Burden of Disease, IHME) retrieval service — disease-keyed prevalence/incidence.

Produces EPIDEMIOLOGY evidence consumed by the commercial_lens's market-size
axis via the gbd_prevalence_text extra-context key, ordered ahead of
Orphanet's rare-disease prevalence (orphanet_prevalence_text) since GBD
covers the common-disease, whole-population case Orphanet cannot reach.

Disease-keyed rather than gene-keyed: the gene is only carried to stamp the
Evidence row's required `gene` field, not used in the GBD lookup itself. See
mcp_servers/gbd/tools.py for the cause-mapping strategy and the
GBD_ENABLED/GBD_DATA_PATH gating (off by default — non-commercial license).
"""

from __future__ import annotations

import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.gbd.tools import get_disease_burden
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/gbd"


async def fetch_gbd(
    disease: str,
    *,
    disease_id: str = "",
    gene: str = "",
    gene_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> list[Evidence]:
    """Fetch GBD disease-burden evidence for a disease. Returns [] cleanly on a
    disabled source or an unconfident cause mapping — never a fabricated row.
    """
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )

    async with span(f"{_SERVICE}:burden", trace_id=trace_id, input_data=disease) as s:
        bundle = await get_disease_burden(disease, disease_id=disease_id)
        s.set_attribute("output", bundle.text or f"mapping={bundle.mapping}")

    if bundle.mapping == "none" or not bundle.records:
        return []

    uri = archive_raw(
        gene or disease,
        disease_id,
        direction_enum.value,
        "gbd",
        f"{bundle.cause_name.replace(' ', '_') or 'burden'}.json",
        bundle.model_dump_json(indent=2),
    )
    prov = make_provenance(_SERVICE, "gbd.get_disease_burden", trace_id)
    return [
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=run_id,
            gene=gene,
            gene_id=gene_id,
            disease=disease,
            disease_id=disease_id,
            evidence_type=EvidenceType.EPIDEMIOLOGY,
            scope="abstract",
            source=f"gbd:burden:{bundle.records[0].cause_id}",
            source_link="https://ghdx.healthdata.org",
            artifact_uri=uri,
            classification=DataClass.NON_SENSITIVE,
            provenance=prov,
            direction=direction_enum,
            extra=bundle.model_dump(),
        )
    ]
