# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Indication-competition retrieval service — disease-keyed drug/trial landscape.

Produces COMPETITION evidence consumed by the commercial lens's competitive-
landscape axis. Unlike `fetch_openfda` / `fetch_trials`, this is target-agnostic
by design: it counts approved drugs and active trials for the *indication*,
by any mechanism, answering "how contested is this disease?" rather than "how
contested is this gene?" (see docs/internal/indication_competition_plan.md).

Both sources are the same NON_SENSITIVE, commercial-by-default public REST APIs
already wired for the gene-keyed queries — no new licensing gate. A query miss
(no confident phrase/condition match) emits no Evidence row rather than a
fabricated zero; the commercial lens must treat absence as "not retrievable",
never as "uncontested" (mirrors the GBD mapping="none" convention).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.clinicaltrials.tools import count_condition_trials
from mcp_servers.openfda.tools import count_indication_drugs
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/indication_competition"
# 2: other names of the disease are searched when its own name finds no confident match.
SEARCH_VERSION = 2
_MAX_ALTERNATIVES = 5


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _searchable(name: str) -> bool:
    """A name safe to search as free text: several words or a long single word (an
    abbreviation matches unrelated label text)."""
    return len(name.split()) >= 2 or len(name) >= 8


def lacks_name_search(ev: Evidence) -> bool:
    """A cached landscape from before other names were searched, whose drug count found no
    confident match: refetch it (a hyphenated disease name matched no label)."""
    extra = ev.extra or {}
    drugs = extra.get("drugs") or {}
    return int(extra.get("search_version") or 1) < SEARCH_VERSION and drugs.get("mapping") != (
        "phrase"
    )


async def fetch_indication_competition(
    disease: str,
    *,
    disease_id: str = "",
    gene: str = "",
    gene_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
    alternatives: Sequence[str] = (),
) -> list[Evidence]:
    """Fetch the disease-keyed drug/trial competition landscape. Returns [] cleanly
    on any API failure or when both queries fail to match a confident indication.

    `gene`/`gene_id` are accepted for signature symmetry with the other retrieval
    services but unused in the query — the landscape is a property of the
    disease, not of the gene→disease direction.

    ``alternatives`` are other names of the disease (its name forms first, then exact
    synonyms). When the disease's own name finds no confident match in the labels
    (phrase) or the registry, the first alternative that does is used, and the evidence
    records which name matched: "non-alcoholic steatohepatitis" matched no label while
    "nonalcoholic steatohepatitis" matched the approved drug.
    """
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )

    async with span(f"{_SERVICE}:landscape", trace_id=trace_id, input_data=disease) as s:
        try:
            drugs, trials = await asyncio.gather(
                count_indication_drugs(disease), count_condition_trials(disease)
            )
            drug_name = trial_name = disease
            others = [
                a
                for a in dict.fromkeys(alternatives)
                if _searchable(a) and a.lower() != disease.lower()
            ][:_MAX_ALTERNATIVES]
            for name in others:
                if drugs.mapping == "phrase":
                    break
                found = await count_indication_drugs(name)
                if found.mapping == "phrase":
                    drugs, drug_name = found, name
            for name in others:
                if trials.mapping != "none":
                    break
                hit = await count_condition_trials(name)
                if hit.mapping != "none":
                    trials, trial_name = hit, name
        except Exception:
            s.set_attribute("output", "failed")
            return []
        s.set_attribute("output", f"drugs={drugs.mapping} trials={trials.mapping}")

    if drugs.mapping == "none" and trials.mapping == "none":
        return []

    combined: dict[str, Any] = {"drugs": drugs.model_dump(), "trials": trials.model_dump()}
    text_parts = [t for t in (drugs.text, trials.text) if t]
    combined["text"] = " ".join(text_parts)
    combined["approved_drug_count"] = drugs.approved_drug_count
    combined["active_trial_count"] = trials.active_count
    combined["phase3_trial_count"] = trials.phase3_count
    combined["total_trial_count"] = trials.total_count
    combined["mapping"] = f"drugs={drugs.mapping},trials={trials.mapping}"
    combined["matched_names"] = {"drugs": drug_name, "trials": trial_name}
    combined["search_version"] = SEARCH_VERSION

    uri = archive_raw(
        gene or disease,
        disease_id,
        direction_enum.value,
        "indication_competition",
        f"{_slug(disease) or 'indication'}.json",
        json.dumps(combined, indent=2),
    )
    prov = make_provenance(
        _SERVICE, "openfda.count_indication_drugs+clinicaltrials.count_condition_trials", trace_id
    )

    return [
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=run_id,
            gene=gene,
            gene_id=gene_id,
            disease=disease,
            disease_id=disease_id,
            evidence_type=EvidenceType.COMPETITION,
            scope="abstract",
            source=f"competition:indication:{disease_id or _slug(disease)}",
            source_link=drugs.source_link or trials.source_link or "https://api.fda.gov",
            artifact_uri=uri,
            classification=DataClass.NON_SENSITIVE,
            provenance=prov,
            direction=Direction.UNSPECIFIED,
            extra=combined,
        )
    ]
