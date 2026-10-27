# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenFDA retrieval service — drug labels (SPL) and FAERS adverse event signal.

Produces REGULATORY evidence consumed by:
  - safety_lens: FAERS adverse-event signal (serious/death rates, top reactions,
    black-box warnings, contraindications) via the faers_text extra-context key.
  - commercial_lens: FDA-approved drug landscape (drugs naming the gene in MoA,
    approved indications) via the fda_label_text extra-context key.
  - regulatory_lens: approval precedent and label-derived safety flags
    (approval_precedent, label_safety, regulatory_de_risking axes) via fda_label_text.
Both sources are NON_SENSITIVE public FDA APIs.

Strategy:
  1. Search drug labels by gene symbol (MoA mentions) and disease indication, plus the
     labels of the target's approved known drugs (Open Targets) by generic name.
  2. Stamp each label with whether its drug acts on the target (``target_drug``) and
     how (``target_action_type``), so consumers need not re-derive it from MoA text.
  3. For up to _MAX_FAERS_DRUGS drugs found, target drugs first, fetch FAERS summaries.
Each label and each FAERS bundle becomes a separate Evidence row so the screening
agent and lenses can independently weight them.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

from core.evidence_text import indicates_disease, mentions_term
from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.openfda.tools import (
    DrugLabelRecord,
    search_adverse_events,
    search_drug_labels,
    search_labels_for_drugs,
)
from mcp_servers.opentargets.tools import SALT_SUFFIX_RE
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/openfda"
_MAX_FAERS_DRUGS = 5  # cap FAERS calls to avoid rate-limit pressure
_MAX_NAMED_DRUGS = 8  # approved target drugs whose labels are fetched by name
# Bumped when the label set or its stamps change, so cached rows from an older retrieval
# are refetched (workflow.openfda_node) instead of silently reused.
# 3: Warnings and Precautions of PLR labels; 4: labels and FAERS stamped with their
# relation to the target (target_relation).
# 5: the disease's synonyms searched, diagnostic labels dropped, originator labels preferred.
RETRIEVAL_VERSION = 5
logger = logging.getLogger(__name__)


def _drug_key(name: str) -> str:
    return SALT_SUFFIX_RE.sub("", " ".join(str(name).lower().split()))


async def _target_drugs(gene_id: str, disease: str, disease_id: str) -> dict[str, dict[str, Any]]:
    """The target's known drugs (Open Targets) by salt-free name: display name, action
    type, approved anywhere, approved or post-marketing in this indication."""
    if not gene_id:
        return {}
    try:
        from mcp_servers.opentargets.tools import get_known_drugs

        bundle = await get_known_drugs(gene_id)
    except Exception as exc:  # optional: labels are still searched by gene and disease
        logger.warning("openfda: known-drug lookup failed (%s); gene/disease search only", exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for d in bundle.drugs:
        key = _drug_key(d.drug_name)
        cur = out.setdefault(
            key,
            {
                "name": SALT_SUFFIX_RE.sub("", d.drug_name.strip()),
                "action_type": d.action_type,
                "approved": False,
                "in_indication": False,
            },
        )
        cur["approved"] = cur["approved"] or d.is_approved
        here = d.disease_id == disease_id or d.disease_name.lower() == disease.lower()
        cur["in_indication"] = cur["in_indication"] or (
            here and (d.approved_in_indication or (d.is_approved and d.max_phase >= 4))
        )
    return out


def _named_drugs(known: dict[str, dict[str, Any]]) -> list[str]:
    """Approved target drugs to fetch labels for, those approved in the indication first."""
    approved = [v for v in known.values() if v["approved"]]
    approved.sort(key=lambda v: (not v["in_indication"], v["name"]))
    return [v["name"] for v in approved][:_MAX_NAMED_DRUGS]


def _stamp(
    label: DrugLabelRecord, gene: str, disease: str, known: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """The label's extra, with whether (and how) its drug acts on the target and its
    relation to the target: acts on it, approved in the indication, or neither."""
    hit = known.get(_drug_key(label.drug_name))
    acts = hit is not None or mentions_term(label.mechanism_of_action, gene)
    relation = (
        "acts_on_target"
        if acts
        else "indication"
        if indicates_disease(label.indications_and_usage or "", disease)
        else "other"
    )
    return {
        **label.model_dump(),
        "target_drug": acts,
        "target_action_type": (hit or {}).get("action_type", ""),
        "target_relation": relation,
        "retrieval_version": RETRIEVAL_VERSION,
    }


async def fetch_openfda(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
    synonyms: list[str] | None = None,
) -> list[Evidence]:
    """Fetch OpenFDA label + FAERS evidence for a gene/disease target. ``synonyms`` are the
    disease's exact ontology synonyms, searched with its name."""
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []

    # ── Step 1: Drug labels ──────────────────────────────────────────────────
    async with span(f"{_SERVICE}:labels", trace_id=trace_id, input_data=f"{gene} | {disease}") as s:
        known = await _target_drugs(gene_id, disease, disease_id)
        labels = await search_drug_labels(gene, disease, synonyms)
        have = {_drug_key(lb.drug_name) for lb in labels}
        wanted = [n for n in _named_drugs(known) if _drug_key(n) not in have]
        if wanted:
            try:
                labels += [
                    lb
                    for lb in await search_labels_for_drugs(wanted)
                    if _drug_key(lb.drug_name) not in have
                ]
            except Exception as exc:  # the gene/disease labels are still useful
                logger.warning("openfda: label lookup by drug name failed: %s", exc)
        # Target drugs first: they carry the target's label safety, and FAERS is capped.
        labels.sort(key=lambda lb: _drug_key(lb.drug_name) not in known)
        s.set_attribute("output", f"{len(labels)} label(s) found")

    label_prov = make_provenance(_SERVICE, "openfda.search_drug_labels", trace_id)
    stamps = {label.drug_name: _stamp(label, gene, disease, known) for label in labels}
    for label in labels:
        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "openfda",
            f"label_{label.drug_name.replace(' ', '_')}.json",
            label.model_dump_json(indent=2),
        )
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.REGULATORY,
                scope="abstract",
                source=f"fda:label:{label.application_number or label.drug_name}",
                source_link=label.source_link,
                artifact_uri=uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=label_prov,
                direction=direction_enum,
                extra=stamps[label.drug_name],
            )
        )

    # ── Step 2: FAERS adverse event summaries for each labeled drug ──────────
    faers_prov = make_provenance(_SERVICE, "openfda.search_adverse_events", trace_id)
    for label in labels[:_MAX_FAERS_DRUGS]:
        async with span(
            f"{_SERVICE}:faers:{label.drug_name}",
            trace_id=trace_id,
            input_data=label.drug_name,
        ) as fs:
            bundle = await search_adverse_events(label.drug_name)
            fs.set_attribute("output", bundle.text)

        if bundle.total_reports == 0:
            continue

        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "openfda",
            f"faers_{label.drug_name.replace(' ', '_')}.json",
            bundle.model_dump_json(indent=2),
        )
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.REGULATORY,
                scope="abstract",
                source=f"fda:faers:{label.drug_name}",
                source_link=bundle.source_link,
                artifact_uri=uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=faers_prov,
                direction=direction_enum,
                # A FAERS bundle is about its label's drug: it relates to the target as
                # that drug does, so the safety lens reads only target drugs' reports.
                extra={
                    **bundle.model_dump(),
                    "target_drug": stamps[label.drug_name]["target_drug"],
                    "target_relation": stamps[label.drug_name]["target_relation"],
                    "retrieval_version": RETRIEVAL_VERSION,
                },
            )
        )

    return evidences
