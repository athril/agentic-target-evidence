# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""LangGraph StateGraph assembly.

build_graph(router, checkpointer) returns a compiled graph that drives the full
gene-target validation pipeline.

Node topology
-----------------------------------------------------
START
  restart_router (entry; jumps to any node on --resume)
  ├─ literature ──────────────────────────────────┐
  ├─ patent (service) ─────────────────────────────┤
  ├─ clinical_trial (service) ─────────────────────┤──> screening_first
  ├─ opentargets (service) ────────────────────────┤        │
  ├─ genetics ─────────────────────────────────────┤  knowledge_extraction
  ├─ omics ────────────────────────────────────────┤        │
  ├─ functional (service) ─────────────────────────┤  screening_second
  ├─ druggability (service) ────────────────────────┤        │
  ├─ openfda (service) ─────────────────────────────┤  claim_extraction  (model-op service)
  └─ gbd (service) ──────────────────────────────────────┘
                                                           │
                                                    source_quality  (model-op service)
                                                           │
                                                hitl_gate
                                     genetics biology safety clinical commercial regulatory (lenses)
                                                   experiment
                                            critic  reviewer  reconciler
                                                  investigator
                                                     report
                                                        │
                                                       END
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse, get_client, observe, propagate_attributes
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, interrupt

from agents.challenge.critic.agent import CriticAgent
from agents.challenge.reviewer.agent import ReviewerAgent
from agents.interpretation._lens_base import LensName, lens_input_digest
from agents.interpretation.biology_lens.agent import BiologyLensAgent
from agents.interpretation.clinical_lens.agent import ClinicalLensAgent
from agents.interpretation.commercial_lens.agent import CommercialLensAgent
from agents.interpretation.genetics_lens.agent import GeneticsLensAgent
from agents.interpretation.regulatory_lens.agent import RegulatoryLensAgent
from agents.interpretation.safety_lens.agent import SafetyLensAgent
from agents.interpretation.safety_lens.verdict import NARRATIVE_SKILL, verdict_digest
from agents.retrieval.genetics.agent import GeneticsAgent
from agents.retrieval.literature.agent import (
    LiteratureAgent,
    lacks_role_queries,
    role_query_evidence,
    with_article_metadata,
)
from agents.retrieval.omics.agent import OmicsAgent
from agents.screening.knowledge_extraction.agent import KnowledgeExtractionAgent
from agents.screening.screening.agent import (
    ScreeningAgent,
    inherit_faers_verdicts,
    is_no_verdict,
    is_reusable_cached_verdict,
)
from agents.screening.source_quality.agent import SourceQualityAgent
from agents.synthesis.experiment.agent import ExperimentAgent
from agents.synthesis.investigator.agent import InvestigatorAgent
from agents.synthesis.report.agent import ReportAgent
from agents.synthesis.report.lens_report import write_lens_report
from agents.synthesis.report.provenance import collect_provenance
from core.degradations import collecting, record_degradation, recorded
from core.evidence_text import FDA_FAERS_PREFIX, target_relation
from core.exceptions import describe_exception
from core.persistence.artifact_store import export_summary_csv
from core.persistence.db import get_session
from core.persistence.repos.evidence import EvidenceRepository
from core.persistence.repos.llm_cache import LlmCacheRepository
from core.persistence.repos.runs import RunRepository
from core.routing.router import Router
from core.telemetry import init_telemetry
from core.telemetry.projects import ensure_langfuse_project
from harness.context import RunContext
from harness.skills import load_skill
from mcp_servers.opentargets.tools import get_disease_descendants
from mcp_servers.pubmed.tools import fetch_pmc_record
from schemas.evidence import (
    CoreClaim,
    DataClass,
    Evidence,
    EvidenceType,
    lens_fingerprint,
    source_fingerprint,
    source_quality_fingerprint,
)
from schemas.messages import AgentMessage
from schemas.state import PipelineState, merge_degradations
from schemas.statement import TargetStatement
from services.decision.reconciler import reconcile
from services.evidence.claim_extraction import extract_claims, structured_claims
from services.evidence.clinical_trial_interpret import build_trial_facts, link_trial_publications
from services.evidence.commercial_interpret import TargetTrial, target_programmes
from services.evidence.constraint_interpret import mendelian_grade_from_signals
from services.evidence.direction_of_effect import (
    HUMAN_GENETICS,
    DirectionReading,
    build_direction_reading,
)
from services.evidence.disease_class import DiseaseClass, resolve_disease_class
from services.evidence.disease_scope import (
    CLASSIFICATION_RANK,
    DiseaseScope,
    name_forms,
    names_match,
    scope_calls,
    strongest_in_scope,
)
from services.evidence.grounding import GroundedPaper, GroundedSentence, TargetIdentity
from services.evidence.lens_inputs import missing_lens_inputs
from services.evidence.liabilities import MOUSE_PHENOTYPES, target_safety
from services.evidence.programme_outcomes import Engagement, ProgrammeConfig, ProgrammeReading
from services.evidence.readings import build_dependency_reading
from services.evidence.statements import Selection
from services.retrieval.clinical_trial import fetch_trials
from services.retrieval.druggability import fetch_druggability
from services.retrieval.functional import fetch_functional
from services.retrieval.gbd import fetch_gbd
from services.retrieval.indication_competition import (
    fetch_indication_competition,
    lacks_name_search,
)
from services.retrieval.openfda import fetch_openfda
from services.retrieval.opentargets import fetch_opentargets
from services.retrieval.patent import fetch_patents

if TYPE_CHECKING:
    from schemas.verdicts import LensVerdict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers for node functions
# ---------------------------------------------------------------------------


def _trace_title(target_gene: str, gene_id: str, disease: str, disease_id: str) -> str:
    """Human-readable Langfuse trace title, e.g.
    'BRCA1 (ENSG00000012048) | breast cancer (EFO_0000305)'.
    IDs are omitted when not yet resolved (populated downstream by OpenTargets).
    """
    gene = target_gene + (f" ({gene_id})" if gene_id else "")
    dis = disease + (f" ({disease_id})" if disease_id else "")
    return f"{gene} | {dis}"


def _ctx(state: PipelineState, router: Router) -> RunContext:
    return RunContext(
        run_id=state["run_id"],
        trace_id=str(state["run_id"]),
        router=router,
    )


def _task_msg(
    state: PipelineState,
    to_agent: str,
    task_spec: dict[str, Any],
    payload: Any = None,
) -> AgentMessage:
    # The (gene, disease, direction) entity is universal — inject direction into
    # every task_spec centrally so each agent is direction-aware by construction.
    # Every agent contract declares "direction" in its consumes set.
    spec = {"direction": state.get("direction") or "unspecified", **task_spec}
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=state["run_id"],
        from_agent="planner",
        to_agent=to_agent,
        intent="task",
        task_spec=spec,
        payload=payload,
        trace_id=str(state["run_id"]),
    )


def _evidences(result: AgentMessage) -> list[Evidence]:
    return [e for e in (result.payload or []) if isinstance(e, Evidence)]


# Acquisition-phase evidence bucket field names (in state), used by the raw-evidence
# combiner below.
_EVIDENCE_BUCKETS = (
    "literature_evidence",
    "patent_evidence",
    "trial_evidence",
    "opentargets_evidence",
    "genetics_evidence",
    "omics_evidence",
    "functional_evidence",
    "druggability_evidence",
    "openfda_evidence",
    "gbd_evidence",
)


def _all_raw_evidence(state: PipelineState) -> list[Evidence]:
    """Combine all acquisition-phase evidence buckets, deduplicated by logical source.

    Evidence buckets use the ``_append`` reducer, so a re-acquisition (e.g. a
    rerun that re-enters at an acquisition node) appends fresh-UUID copies of
    items it already holds — without dedup those duplicates would flow as
    duplicate claims to the lenses. We collapse by ``(evidence_type, source)``
    keeping the **last** occurrence (the freshly re-fetched copy is appended
    last), mirroring the "normalise on every read" philosophy of
    ``_dedup_screened`` and the screening cache's ``(type, source)`` item
    identity. This is a no-op on normal runs — each logical source appears
    exactly once.
    """
    combined: list[Evidence] = []
    for bucket in _EVIDENCE_BUCKETS:
        combined.extend(cast("list[Evidence]", state.get(bucket, [])))
    deduped: dict[tuple[str, str], Evidence] = {}
    for ev in combined:
        deduped[(ev.evidence_type.value, ev.source)] = ev
    return list(deduped.values())


def _with_target_drug_trials(evidences: list[Evidence], state: PipelineState) -> list[Evidence]:
    """Mark a trial whose interventions name one of the target's known drugs as a trial of
    the target (``matched_by`` "drug:<name>"), however the search found it.

    A trial retrieved by the gene query whose record names only the drug (an outcome trial
    of a transfer-protein inhibitor) was left to LLM screening, which judged it "uncertain"
    because its condition was the broader cardiovascular term; that verdict was cached, so
    the trial never reached the lenses. Marked, it meets `is_target_drug_trial` and is kept
    deterministically.
    """
    from core.evidence_text import mentions_term
    from services.evidence.drug_precedent import drug_key

    names = sorted(
        {
            drug_key(str(d.get("drug_name") or ""))
            for d in _ot_extra(state).get("known_drugs") or []
            if d.get("drug_name")
        }
        - {""}
    )
    if not names:
        return evidences
    out: list[Evidence] = []
    for ev in evidences:
        extra = ev.extra or {}
        if ev.evidence_type == EvidenceType.CLINICAL_TRIAL and not str(
            extra.get("matched_by") or ""
        ).startswith("drug:"):
            interventions = " ".join(str(i) for i in extra.get("interventions") or [])
            named = next((n for n in names if len(n) >= 4 and mentions_term(interventions, n)), "")
            if named:
                ev = ev.model_copy(update={"extra": {**extra, "matched_by": f"drug:{named}"}})
        out.append(ev)
    return out


def _ot_extra(state: PipelineState) -> dict[str, Any]:
    """Extract the opentargets `extra` dict from state (empty dict if not present)."""
    for ev in state.get("opentargets_evidence", []):
        if ev.extra:
            return ev.extra
    return {}


def _depmap_extra(state: PipelineState) -> dict[str, Any]:
    """Extract the DepMap DependencyBundle fields from functional_evidence (empty dict if absent)."""
    gene = state.get("target_gene", "")
    for ev in state.get("functional_evidence", []):
        if str(getattr(ev, "source", "")).startswith(f"depmap:{gene}"):
            return ev.extra or {}
    return {}


def _project_score_extra(state: PipelineState) -> dict[str, Any]:
    """Extract the Project Score bundle fields from functional_evidence (empty dict if absent)."""
    gene = state.get("target_gene", "")
    for ev in state.get("functional_evidence", []):
        if str(getattr(ev, "source", "")).startswith(f"project_score:{gene}"):
            return ev.extra or {}
    return {}


def _dependency_reading_json(state: PipelineState) -> dict[str, Any] | None:
    """Cell-line dependency merged across DepMap and Project Score (None when neither has data)."""
    reading = build_dependency_reading(_depmap_extra(state), _project_score_extra(state))
    return reading.model_dump(mode="json") if reading else None


def _trial_facts_json(
    evidences: list[Evidence], *, disease: str = "", scope: DiseaseScope | None = None
) -> list[dict[str, Any]]:
    """Authoritative per-trial phase/status facts for the clinical guards, each linked to
    the retrieved documents that name its registry id (its publications). With
    ``disease``, each fact says whether the trial studies that indication; with ``scope``,
    whether it measured the indication (`relevant_to_indication`)."""
    from core.evidence_text import screenable_text
    from services.evidence.clinical_trial_interpret import mark_indication, mark_relevance

    trials = [e for e in evidences if e.evidence_type == EvidenceType.CLINICAL_TRIAL]
    # A publication names its trial in its text, or PubMed links it (its DataBank ids).
    documents = [
        (
            str(e.evidence_id),
            f"{e.extra.get('title') or ''}\n{screenable_text(e)}\n"
            + " ".join(str(r) for r in e.extra.get("registry_ids") or []),
        )
        for e in evidences
        if e.evidence_type != EvidenceType.CLINICAL_TRIAL
    ]
    facts = link_trial_publications(build_trial_facts(trials), documents)
    if disease:
        facts = mark_indication(facts, disease)
    if scope is not None:
        facts = mark_relevance(facts, scope)
    return [f.model_dump(mode="json") for f in facts]


def _curated_mechanism(reading: DirectionReading) -> dict[str, Any] | None:
    """The disease's own Mendelian mechanism from curated, direction-annotated Open Targets
    rows (one consistent direction), in the shape of ``mechanism_direction``; None when there
    are none or they disagree."""
    curated = [
        o
        for o in reading.observations
        if o.evidence == "mendelian" and o.source.startswith("Open Targets")
    ]
    favours = {o.favours for o in curated}
    if len(favours) != 1:
        return None
    lower_helps = favours == {"decrease"}
    return {
        "direction": "inhibit" if lower_helps else "activate",
        "mechanism": "gof" if lower_helps else "lof",
        "confidence": 0.85,
        "rationale": "Curated mechanism of this disease: " + "; ".join(o.detail for o in curated),
        "supporting_variant_ids": [],
    }


async def _text_scope(state: PipelineState) -> DiseaseScope:
    """The run disease's scope for text (services/evidence/disease_scope.py)."""
    from services.evidence.disease_scope import scope_from_ontology

    disease = state.get("disease") or ""
    disease_id = state.get("disease_id") or ""
    onto = await get_disease_descendants(disease_id) if disease_id else None
    return scope_from_ontology(disease, onto)


def _genetic_direction(reading: DirectionReading) -> dict[str, Any]:
    """The human-genetics part of a direction reading, for the lens direction guard
    (services/evidence/direction_guard.py): requested effect and each side's strength."""
    genetic = reading.restricted_to(HUMAN_GENETICS)
    return {
        "requested": genetic.requested,
        "support": genetic.support,
        "against": genetic.against,
    }


async def _direction_reading(
    state: PipelineState, floor_signals: dict[str, Any]
) -> DirectionReading:
    """Direction-of-effect evidence for the requested direction
    (services/evidence/direction_of_effect.py); one reading for every lens and gate."""
    from services.evidence.drug_precedent import PrecedentReading

    disease = state.get("disease") or ""
    disease_id = state.get("disease_id") or ""
    onto = await get_disease_descendants(disease_id) if disease_id else None
    mendelian = mendelian_grade_from_signals(floor_signals)
    related = list(floor_signals.get("out_of_scope_curations") or [])
    labels = [
        dict(ev.extra or {})
        for ev in state.get("openfda_evidence", [])
        if ev.source.startswith("fda:label:")
    ]
    from mcp_servers.opentargets.tools import get_direction_evidence, get_qtl_colocalisations

    scope = await _text_scope(state)
    scope_ids = await _disease_scope(state)
    gene_id = str(state.get("gene_id") or "")
    structured = await get_direction_evidence(gene_id, scope_ids) if scope_ids else []
    qtl = await get_qtl_colocalisations(gene_id, scope_ids) if scope_ids else []
    return build_direction_reading(
        str(state.get("direction") or ""),
        structured_rows=structured,
        qtl_rows=qtl,
        disease=disease,
        scope=scope,
        statements=_statements(state),
        precedent=PrecedentReading.model_validate(await _drug_precedent(state)),
        mechanism=floor_signals.get("mechanism_direction"),
        mechanism_in_scope=mendelian
        and not (floor_signals.get("mechanism_direction") or {}).get("from_other_disorders"),
        mechanism_disorder=disease if mendelian else "; ".join(related) or "a related disorder",
        labels=labels,
        disease_names=onto.names if onto else (),
        parent_names=onto.parent_names if onto else (),
        gene=str(state.get("target_gene") or ""),
        somatic_indication=DiseaseClass.ONCOLOGY.value
        in await _resolve_disease_classes(state, floor_signals),
    )


def _statements(state: PipelineState) -> list[TargetStatement]:
    """The run's target statements (claim_extraction node)."""
    return [TargetStatement.model_validate(st) for st in state.get("target_statements") or []]


_MESH_DESCRIPTORS: dict[str, str | None] = {}


async def _disease_mesh(disease: str) -> str | None:
    """The disease's MeSH descriptor (PubMed's own term mapping), once per process."""
    if disease not in _MESH_DESCRIPTORS:
        from mcp_servers.pubmed.tools import resolve_mesh_term

        try:
            _MESH_DESCRIPTORS[disease] = await resolve_mesh_term(disease)
        except Exception as exc:  # noqa: BLE001 — ranking degrades to text scope
            logger.warning("MeSH lookup for %r failed: %s", disease, describe_exception(exc))
            return None
    return _MESH_DESCRIPTORS[disease]


_MODEL_PHENOTYPES_VERSION = 1
_MODEL_PHENOTYPES: dict[str, list[str]] = {}  # per process, by MeSH descriptor


async def _model_phenotypes(disease: str) -> list[str]:
    """The MeSH phenotypes animal models of the disease are indexed under
    (services/evidence/model_phenotypes.py), cached per descriptor; [] without one."""
    import hashlib

    from services.evidence.model_phenotypes import find_model_phenotypes

    descriptor = await _disease_mesh(disease) if disease else None
    if not descriptor:
        return []
    if descriptor in _MODEL_PHENOTYPES:
        return _MODEL_PHENOTYPES[descriptor]
    key = hashlib.sha256(
        f"model_phenotypes|{_MODEL_PHENOTYPES_VERSION}|{descriptor}".encode()
    ).hexdigest()
    cached = await _llm_cache_get(key, "pubmed")
    if cached is not None and isinstance(cached.get("phenotypes"), list):
        _MODEL_PHENOTYPES[descriptor] = [str(p) for p in cached["phenotypes"]]
        return _MODEL_PHENOTYPES[descriptor]
    found = await find_model_phenotypes(descriptor)
    if found is None:  # the lookup failed: try again next time
        return []
    names = [p.descriptor for p in found]
    # "No phenotype" is an answer too: without it every lens node repeated the lookup.
    _MODEL_PHENOTYPES[descriptor] = names
    await _llm_cache_set(key, "pubmed", "model_phenotypes", {"phenotypes": names})
    return names


def _same_name(a: str, b: str) -> bool:
    """Two disease names alike up to case, typography, possessives and a plural."""
    from core.evidence_text import fold_name

    fa, fb = fold_name(a), fold_name(b)
    return fa == fb or fa.rstrip("s") == fb.rstrip("s")


async def _literature_index(state: PipelineState) -> dict[str, Any]:
    """The run's literature index for lens input selection
    (services/evidence/literature_selection.py): one record per kept paper with its
    scope, study design per lens, impact and study cluster, plus the names claims are
    matched against."""
    from services.evidence.literature_selection import build_literature_index

    disease = state.get("disease") or ""
    disease_id = state.get("disease_id") or ""
    onto = await get_disease_descendants(disease_id) if disease_id else None
    names = [disease, *sorted(onto.names if onto else ())]
    mesh = await _disease_mesh(disease) if disease else None
    papers = [ev for ev in _keep_evidence(state) if ev.evidence_type in _LITERATURE_EVIDENCE]
    aliases = list((state.get("resolved_context") or {}).get("gene_aliases") or [])
    drugs = [
        str(d.get("drug_name") or "")
        for d in _ot_extra(state).get("known_drugs") or []
        if isinstance(d, dict)
    ]
    index = build_literature_index(
        papers,
        state.get("source_quality") or {},
        model_terms=await _model_phenotypes(disease),
        disease_terms=names,
        parent_terms=sorted(onto.parent_names if onto else ()),
        mesh_descriptor=mesh or "",
        mesh_is_disease=bool(mesh) and any(_same_name(mesh or "", n) for n in names),
        target_terms=[str(state.get("target_gene") or ""), *aliases],
        drug_terms=drugs,
        scope=await _text_scope(state),
    )
    return index.model_dump(mode="json")


async def _dependency_profile(state: PipelineState, *, is_oncology: bool) -> dict[str, Any] | None:
    """What the cell-line dependency pattern means for this indication and direction
    (services/evidence/readings.dependency_profile)."""
    from services.evidence.drug_precedent import requested_effect
    from services.evidence.readings import DependencyReading, dependency_profile

    raw = _dependency_reading_json(state)
    reading = DependencyReading.model_validate(raw) if raw else None
    disease_id = state.get("disease_id") or ""
    onto = await get_disease_descendants(disease_id) if disease_id else None
    names = [
        state.get("disease") or "",
        *(onto.names if onto else ()),
        *(onto.parent_names if onto else ()),
    ]
    profile = dependency_profile(
        reading,
        _depmap_extra(state).get("lineage_breakdown") or [],
        names,
        is_oncology=is_oncology,
        lowers_activity=requested_effect(str(state.get("direction") or "")) != "increase",
    )
    return profile.model_dump(mode="json") if profile else None


async def _candidate_liabilities(
    state: PipelineState, floor_signals: dict[str, Any]
) -> list[dict[str, Any]]:
    """Candidate on-target liabilities of the requested change (see
    services/evidence/liabilities.py); shared by the safety lens, the score and the report."""
    return (await _liability_split(state, floor_signals))[0]


async def _safety_stops(state: PipelineState) -> tuple[dict[str, list[str]], list[str]]:
    """Trials of requested-direction drugs stopped for safety — the Open Targets stop
    category ``Safety_Sideeffects`` or a stop reason naming harm — and posted trials in
    the indication with excess deaths or serious adverse events on the drug arms: by drug
    in this indication, and listed for the others.

    Another indication's stop is not counted: 11 of 12 drugged targets outside the panel
    had one somewhere (a statin, a DPP4 inhibitor among them), so every repurposing
    question would have been neutral whatever the class's record.
    """
    from services.evidence.drug_precedent import (
        drug_effect,
        drug_key,
        requested_effect,
        row_in_indication,
    )
    from services.evidence.liabilities import is_safety_stop, trial_harms

    want = requested_effect(str(state.get("direction") or ""))
    scope_ids = await _disease_scope(state)
    disease = state.get("disease") or ""
    here: dict[str, list[str]] = {}
    elsewhere: list[str] = []
    for row in _ot_extra(state).get("known_drugs") or []:
        if not isinstance(row, dict):
            continue
        if want in ("decrease", "increase") and drug_effect(row.get("action_type")) != want:
            continue
        drug = drug_key(str(row.get("drug_name") or ""))
        stops = [
            f"{r.get('trial_id') or 'trial'} (phase {float(r.get('stage') or 0):g}, "
            f"{str(r.get('status') or 'status unknown').lower()}): {r.get('why') or 'no reason given'}"
            for r in row.get("stop_reports") or []
            if isinstance(r, dict) and is_safety_stop(r)
        ]
        if not drug or not stops:
            continue
        if row_in_indication(row, scope_ids, disease):
            here.setdefault(drug, [])
            here[drug] += [x for x in stops if x not in here[drug]]
        else:
            where = str(row.get("disease_name") or "another indication")
            elsewhere += [f"{drug.lower()} in {where}: {x}" for x in stops]
    drugs = [
        drug_key(str(row.get("drug_name") or ""))
        for row in _ot_extra(state).get("known_drugs") or []
        if isinstance(row, dict)
        and (want not in ("decrease", "increase") or drug_effect(row.get("action_type")) == want)
    ]
    facts = _trial_facts_json(
        _keep_evidence(state), disease=disease, scope=await _text_scope(state)
    )
    for drug, harms in trial_harms(facts, drugs).items():
        here.setdefault(drug, [])
        here[drug] += [x for x in harms if x not in here[drug]]
    return here, list(dict.fromkeys(elsewhere))


def _worsened_programmes(reading: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Harm in people in this indication from the programme reading: (source, detail) for
    each requested-direction compound on which a reported trial saw the disease worsen,
    when two or more did. One compound cannot tell itself from the mechanism, as with
    safety stops; the clinical lens reads it either way."""
    drugs = [
        str(p.get("drug") or "").lower()
        for p in (reading or {}).get("programmes") or []
        if isinstance(p, dict) and p.get("worsened")
    ]
    drugs = [d for d in dict.fromkeys(drugs) if d]
    if len(drugs) < 2:
        return []
    return [
        (f"publications ({d})", f"the disease worsened on {d} in a reported trial") for d in drugs
    ]


async def _liability_split(
    state: PipelineState, floor_signals: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """(counted, opposite, covered) candidate liabilities from this gene's data, split by
    the change of the target that causes them (``liabilities.split_by_direction``)."""
    from core.evidence_text import label_acts_on_target
    from services.evidence.drug_precedent import drug_effect, requested_effect
    from services.evidence.liabilities import (
        candidate_liabilities,
        genetic_direction_harms,
        split_by_direction,
    )

    ot = _ot_extra(state)
    dependency = _dependency_reading_json(state) or {}
    want = requested_effect(str(state.get("direction") or ""))
    # Only labels of drugs acting on the target, and not of drugs acting on it the other
    # way: chemotherapy labels approved in the indication are not the target's liabilities
    # (ESR1: docetaxel, capecitabine), nor is an agonist's warning a degrader's (estradiol).
    boxed = [
        (
            str((ev.extra or {}).get("drug_name") or ev.source),
            str((ev.extra or {}).get("boxed_warning") or ""),
        )
        for ev in _keep_evidence(state)
        if ev.source.startswith("fda:label:")
        and label_acts_on_target(ev)
        and not _opposite_effect(drug_effect((ev.extra or {}).get("target_action_type")), want)
    ]
    precedent = await _drug_precedent(state)
    reading = await _direction_reading(state, floor_signals)
    worsened = _worsened_programmes(state.get("programme_reading"))
    safety = target_safety(ot)
    stops_here, _ = await _safety_stops(state)
    constraint = floor_signals.get("constraint_reading") or {}
    mouse = ot.get("mouse_phenotype_labels") or []
    items = candidate_liabilities(
        safety_events=safety["events"],
        event_directions=safety["event_directions"],
        mouse_phenotypes=mouse,
        constraint=constraint,
        direction=str(state.get("direction") or ""),
        common_essential=bool(dependency.get("is_common_essential")),
        boxed_warnings=boxed,
        approved_here=precedent.get("concordant_approved") or [],
        safety_stops=stops_here,
        # Harm from the requested change in this disease, seen in people: a label warning
        # that names the disease. The Mendelian mechanism and related disorders are
        # efficacy evidence the genetics lens and the direction gate count.
        direction_harms=[
            (o.source, o.detail)
            for o in reading.contradicting
            if o.outcome == "harm"
            and o.perturbation == reading.requested
            and o.evidence == "label_warning"
            and o.in_scope
        ]
        + worsened
        + genetic_direction_harms(reading),
    )
    # Data on lowering examined whatever they showed: every mouse phenotype reads as
    # lowering, and homozygous loss-of-function carriers show complete loss in people.
    examined = ["decrease"] if mouse or constraint.get("hom_lof_present") is True else []
    counted, opposite, covered = split_by_direction(
        items, str(state.get("direction") or ""), examined=examined
    )
    return (
        [li.model_dump(mode="json") for li in counted],
        [li.model_dump(mode="json") for li in opposite],
        covered,
    )


async def _approved_label_warnings(state: PipelineState) -> list[dict[str, str]]:
    """Boxed and Warnings & Precautions text of the labels of drugs acting on the target in
    the requested direction and approved in this indication (the safety lens settles
    liabilities against them)."""
    from core.evidence_text import label_acts_on_target
    from services.evidence.drug_precedent import (
        drug_effect,
        drug_key,
        names_drug,
        requested_effect,
    )

    approved = {
        drug_key(str(d)) for d in (await _drug_precedent(state)).get("concordant_approved") or []
    }
    want = requested_effect(str(state.get("direction") or ""))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for ev in _keep_evidence(state):
        x = ev.extra or {}
        name = drug_key(str(x.get("drug_name") or ""))
        # A combination product counts when an ingredient is approved here (CFTR's triple
        # combinations); a label with no recorded action is kept unless it acts the other
        # way, as for the boxed-warning findings.
        if (
            not ev.source.startswith("fda:label:")
            or not names_drug(name, approved)
            or name in seen
            or not label_acts_on_target(ev)
            or _opposite_effect(drug_effect(x.get("target_action_type")), want)
        ):
            continue
        seen.add(name)
        text = " ".join(str(x.get(k) or "") for k in ("boxed_warning", "warnings"))
        out.append(
            {"drug": name, "text": " ".join(text.split()), "evidence_id": str(ev.evidence_id)}
        )
    return out


def _opposite_effect(effect: str, want: str) -> bool:
    return {effect, want} == {"decrease", "increase"}


async def _decisive_claim_ids(state: PipelineState) -> list[str]:
    """Claims every lens is shown when they are routed to it (claim_relevance): approvals
    in the indication and boxed warnings from the label claims, plus the claims of
    stopped late-phase trials of the target's drugs in the indication and of curated
    Mendelian records for the disease."""
    from services.evidence.claim_relevance import decisive_claim_ids, is_decisive_trial

    scope = await _text_scope(state)
    scope_ids = await _disease_scope(state)
    drugs = [
        str(d.get("drug_name") or "")
        for d in _ot_extra(state).get("known_drugs") or []
        if isinstance(d, dict) and d.get("drug_name")
    ]

    def names_disease(text: str) -> bool:
        return scope.match(text) == "in_scope"

    sources: set[str] = set()
    for ev in _keep_evidence(state):
        if ev.evidence_type == EvidenceType.CLINICAL_TRIAL and is_decisive_trial(
            ev, drugs, names_disease
        ):
            sources.add(str(ev.evidence_id))
        # A ClinGen/GenCC curation row whose classification is for this disease.
        assocs = (ev.extra or {}).get("associations")
        if (
            ev.evidence_type == EvidenceType.GENETICS
            and assocs
            and "total" in (ev.extra or {})
            and strongest_in_scope(assocs, scope_ids=scope_ids, disease_name=ev.disease or "")
        ):
            sources.add(str(ev.evidence_id))
    return sorted(decisive_claim_ids(state.get("extracted_claims", []), sources))


def _liability_statuses(state: PipelineState, count: int) -> list[str] | None:
    """The safety lens's status for each of the ``count`` candidate liabilities, in order
    (``LensVerdict.liability_statuses``), or None when the verdict carries none for this
    list (a verdict cached before the statuses, or a different list)."""
    safety = next((lv for lv in state.get("lens_verdicts", []) if lv.lens == "safety"), None)
    if safety is None:
        return None
    rows = safety.liability_statuses
    by_index = {int(r["index"]): str(r.get("status") or "") for r in rows if "index" in r}
    if sorted(by_index) != list(range(1, count + 1)):
        return None
    return [by_index[i] for i in range(1, count + 1)]


# Registry statuses of a trial that dosed people: a terminated or closed trial counts only
# when it enrolled someone.
_DOSED_STATUSES = {"TERMINATED", "ACTIVE_NOT_RECRUITING"}


def _dosed(fact: dict[str, Any]) -> bool:
    status = str(fact.get("status") or "").upper()
    return status == "COMPLETED" or (
        status in _DOSED_STATUSES and int(fact.get("enrollment") or 0) > 0
    )


def _human_exposure(state: PipelineState) -> bool:
    """Whether a drug acting on the target in the requested direction has human clinical
    exposure: an approved known drug, or a phase 2+ trial of a known drug that dosed people
    — completed, or terminated or closed after enrolling (Open Targets — investigational
    names mined from trial titles are not certain to act on the target). A terminated
    phase 3 dosed its patients: BACE1 inhibitors were stopped in phase 3 after years of
    exposure. People given an inhibitor say nothing about the safety of raising the target;
    a drug of unknown action does not count. A run without a requested change counts every
    known drug."""
    from services.evidence.drug_precedent import drug_effect, requested_effect
    from services.retrieval.clinical_trial import _drug_key

    want = requested_effect(str(state.get("direction") or ""))
    known = [
        d
        for d in _ot_extra(state).get("known_drugs") or []
        if want not in ("decrease", "increase") or drug_effect(d.get("action_type")) == want
    ]
    if any(d.get("is_approved") for d in known):
        return True
    known_keys = {_drug_key(str(d.get("drug_name") or "")) for d in known}
    return any(
        f["matched_by"].startswith("drug:")
        and _drug_key(f["matched_by"][5:]) in known_keys
        and max(f.get("phase_numbers") or [0]) >= 2
        and _dosed(f)
        for f in _trial_facts_json(_keep_evidence(state))
    )


async def _drug_precedent(state: PipelineState) -> dict[str, Any]:
    """What drugs acting on the target did in this indication, against the requested
    direction (services/evidence/drug_precedent.py), with its prompt block."""
    from services.evidence.drug_precedent import assess_precedent, render_precedent_block

    reading = assess_precedent(
        _ot_extra(state).get("known_drugs") or [],
        direction=str(state.get("direction") or "unspecified"),
        disease=state.get("disease") or "",
        scope_ids=await _disease_scope(state),
    )
    return {
        **reading.model_dump(mode="json"),
        "concordant_approved": reading.concordant_approved,
        "discordant_approved": reading.discordant_approved,
        "failures": reading.failures,
        "text": render_precedent_block(reading),
    }


async def _programme_strength(state: PipelineState) -> float | None:
    """The falsification strength to gate the score on; None when it cannot be trusted
    (older Open Targets rows without stop reports), which keeps the flat precedent caps."""
    reading = state.get("programme_reading") or await _programme_reading(state)
    return float(reading.get("falsification") or 0.0) if reading.get("available") else None


async def _programme_reading(
    state: PipelineState,
    ctx: RunContext | None = None,
    disease_classes: Iterable[str] = (),
) -> dict[str, Any]:
    """How strongly the failed programmes against the target falsify its mechanism
    (services/evidence/programme_outcomes.py), with its prompt block.

    Computed once, by the clinical lens node, which passes ``ctx`` so that each efficacy
    failure is asked whether its drug engaged the target; the experiment and report nodes
    read the result from state. Without ``ctx`` every programme stays unassessed.
    Unavailable (``available`` false) when the Open Targets rows predate stop reports, so
    the score falls back to the flat precedent caps instead of gating on a strength
    computed from nothing.
    """
    from services.evidence.programme_outcomes import (
        classify_programmes,
        load_programme_config,
        render_programme_block,
    )

    disease = state.get("disease") or ""
    cfg = load_programme_config()
    trial_facts = _trial_facts_json(
        _keep_evidence(state), disease=disease, scope=await _text_scope(state)
    )
    reading = classify_programmes(
        _ot_extra(state).get("known_drugs") or [],
        trial_facts,
        direction=str(state.get("direction") or "unspecified"),
        disease=disease,
        scope_ids=await _disease_scope(state),
        config=cfg,
    )
    if ctx is not None and reading.available:
        reading = await _publication_outcomes(state, reading, cfg, ctx)
        reading = await _assess_engagement(
            state, reading, trial_facts, cfg, ctx, frozenset(disease_classes)
        )
    return {
        **reading.model_dump(mode="json"),
        "text": render_programme_block(reading, trial_facts),
    }


async def _publication_outcomes(
    state: PipelineState, reading: ProgrammeReading, cfg: ProgrammeConfig, ctx: RunContext
) -> ProgrammeReading:
    """Merge the trial outcomes the literature reports for the target's same-direction
    drugs into the programmes (agents/interpretation/clinical_lens/publication_outcomes.py),
    caching each answer per claim text. A failed question leaves the registry reading."""
    import hashlib

    from agents.interpretation.clinical_lens.publication_outcomes import (
        ask_publication_outcomes,
        candidate_claims,
        linked_trials,
        outcome_digest,
        to_outcomes,
    )
    from core.routing.classify import classify
    from services.evidence.drug_precedent import drug_effect, drug_key
    from services.evidence.programme_outcomes import apply_publication_outcomes

    disease = state.get("disease") or ""
    rows = [r for r in _ot_extra(state).get("known_drugs") or [] if isinstance(r, dict)]
    effects = {
        drug_key(str(r.get("drug_name") or "")): drug_effect(r.get("action_type")) for r in rows
    }
    drugs = sorted(
        {
            str(r.get("drug_name") or "")
            for r in rows
            if effects.get(drug_key(str(r.get("drug_name") or ""))) == reading.requested
        }
        - {""}
    )
    if not drugs:
        return reading
    papers = (await _literature_index(state)).get("papers") or {}
    scope = await _text_scope(state)
    linked = linked_trials(
        _trial_facts_json(_keep_evidence(state), disease=disease, scope=scope), drugs
    )
    claims = candidate_claims(state.get("extracted_claims", []), drugs, papers, scope, linked)
    if not claims:
        logger.info("[node] clinical_lens: no literature claim reports a trial of the drugs")
        return reading
    model_fp = (
        ctx.router.select(DataClass.NON_SENSITIVE, "clinical_lens")[1]
        if state.get("model_fingerprint")
        else ""
    )
    use_cache = bool(model_fp) and not state.get("force_refresh", False)
    digest = outcome_digest()

    def key(claim: CoreClaim) -> str:
        raw = f"publication_outcome|{digest}|{disease}|{'|'.join(drugs)}|{claim.claim_text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    answers: dict[str, Any] = {}
    todo: list[CoreClaim] = []
    for c in claims:
        cached = await _llm_cache_get(key(c), model_fp) if use_cache else None
        if cached is not None and isinstance(cached.get("answer"), dict):
            answers[str(c.evidence_id)] = cached["answer"]
        else:
            todo.append(c)
    if todo:
        fresh = await ask_publication_outcomes(
            todo,
            drugs=drugs,
            disease=disease,
            ctx=ctx,
            classification=classify(cast("list[Evidence]", todo)),
        )
        for c in todo:
            answer = fresh.get(str(c.evidence_id))
            if answer is None:
                continue
            answers[str(c.evidence_id)] = answer
            if use_cache:
                await _llm_cache_set(key(c), model_fp, "publication_outcome", {"answer": answer})
    outcomes = to_outcomes(answers, claims, drugs, linked)
    logger.info(
        "[node] clinical_lens: publication outcomes for %d drug(s) from %d claim(s): %s",
        len(outcomes),
        len(claims),
        {d: [o.result for o in os] for d, os in outcomes.items()},
    )
    return apply_publication_outcomes(reading, outcomes, effects, cfg, disease)


async def _causality_reading(
    state: PipelineState,
    keep_ev: list[Evidence],
    floor_signals: dict[str, Any],
    direction_reading: DirectionReading,
    *,
    is_oncology: bool = False,
) -> dict[str, Any]:
    """The independent in-scope sources of human genetic association behind the causality
    axis (agents/interpretation/genetics_lens/causality.py). Available when the literature
    was read through target statements (a run before them keeps the lens's own axis)."""
    from agents.interpretation.genetics_lens.causality import causality_reading
    from mcp_servers.opentargets.tools import get_somatic_evidence
    from services.evidence.constraint_interpret import mendelian_grade_from_signals

    claims = list(state.get("extracted_claims", []))
    reading = causality_reading(
        direction_reading.restricted_to(HUMAN_GENETICS).observations,
        gwas_studies=sorted(
            {
                e.source
                for e in keep_ev
                if e.source.startswith("gwas_catalog:") and (e.extra or {}).get("trait")
            }
        ),
        paper_of={str(c.evidence_id): str(c.source_evidence_id) for c in claims},
        curated=mendelian_grade_from_signals(floor_signals),
        available=bool(state.get("target_statements")),
        somatic=(
            [
                r.datasource
                for r in await get_somatic_evidence(
                    str(state.get("gene_id") or ""), await _disease_scope(state) or ()
                )
            ]
            if is_oncology
            else []
        ),
    )
    return reading.model_dump(mode="json")


async def _target_identity(state: PipelineState) -> TargetIdentity:
    """The target as identifiers: the planner's HGNC record (looked up again for a run
    resumed from a checkpoint that predates it), and its orthologs' NCBI Gene ids."""
    from agents.planner.agent import hgnc_context
    from mcp_servers.ontology.tools import ncbi_orthologs, resolve_hgnc_symbol
    from services.evidence.grounding import TargetIdentity

    gene = str(state.get("target_gene") or "")
    ctx_ = dict(state.get("resolved_context") or {})
    if not ctx_.get("ncbi_gene_id") and gene:
        try:
            ctx_.update(hgnc_context(await resolve_hgnc_symbol(gene)))
        except Exception as exc:
            record_degradation(
                "grounding: HGNC",
                f"{type(exc).__name__}: {exc}",
                "literature mentions are matched to the target by symbol, not by NCBI Gene id",
            )
    ncbi_id = str(ctx_.get("ncbi_gene_id") or "")
    return TargetIdentity(
        symbol=str(ctx_.get("hgnc_symbol") or gene),
        ncbi_id=ncbi_id,
        name=str(ctx_.get("gene_name") or ""),
        aliases=list(ctx_.get("gene_aliases") or []),
        previous_symbols=list(ctx_.get("previous_symbols") or []),
        ortholog_ids=await ncbi_orthologs(ncbi_id) if ncbi_id else [],
    )


async def _ground_literature(
    state: PipelineState, evidences: Iterable[Evidence]
) -> dict[str, GroundedPaper]:
    """{evidence id: grounded paper} for the PubMed rows PubTator3 has annotated
    (services/evidence/grounding.py)."""
    from mcp_servers.ontology.tools import hgnc_by_entrez
    from mcp_servers.pubtator.tools import get_annotations
    from services.evidence.grounding import ground_paper, needs_rehoming

    rows = {
        ev.source.removeprefix("PMID:"): ev
        for ev in evidences
        if ev.evidence_type in _LITERATURE_EVIDENCE and ev.source.startswith("PMID:")
    }
    if not rows:
        return {}
    docs = await get_annotations(list(rows))
    if not docs:
        return {}
    target = await _target_identity(state)
    ids = sorted(set().union(*(needs_rehoming(d, target) for d in docs)))
    hgnc = {i: await hgnc_by_entrez(i) for i in ids}
    disease_id = state.get("disease_id") or ""
    onto = await get_disease_descendants(disease_id) if disease_id else None
    text_scope = await _text_scope(state)
    drugs = [
        str(d.get("drug_name") or "")
        for d in _ot_extra(state).get("known_drugs") or []
        if isinstance(d, dict)
    ]
    out: dict[str, GroundedPaper] = {}
    for doc in docs:
        ev = rows.get(doc.pmid)
        if ev is not None:
            out[str(ev.evidence_id)] = ground_paper(
                doc,
                target,
                hgnc=hgnc,
                drug_names=drugs,
                scope_mesh=onto.mesh_ids if onto else (),
                scope=text_scope,
            )
    logger.info(
        "[node] claim_extraction: %d of %d paper(s) grounded by PubTator3 "
        "(%d gene id(s) checked by the approved-symbol rule)",
        len(out),
        len(rows),
        len(ids),
    )
    return out


async def _read_statements(
    state: PipelineState,
    papers: dict[str, GroundedPaper],
    claims: list[CoreClaim],
    evidences: Iterable[Evidence],
    ctx: RunContext,
    model_fp: str,
) -> list[TargetStatement]:
    """The statements of each grounded paper's selected sentences
    (services/evidence/statements.py); the reader's answers cached per rendered sentence
    and question digest."""
    import hashlib

    from services.evidence.claim_effect import target_drug_effects
    from services.evidence.grounding import cap_run, select_sentences
    from services.evidence.statements import (
        SKILL,
        assemble,
        design_from_metadata,
        read_sentences,
        reader_digest,
        render,
        sentence_key,
    )
    from services.evidence.variant_effect import lookup as variant_lookup

    claimed: dict[str, set[int]] = {}
    for c in claims:
        if c.sentence is not None and c.source_evidence_id is not None:
            claimed.setdefault(str(c.source_evidence_id), set()).add(c.sentence)
    selected = [
        (eid, s)
        for eid, paper in papers.items()
        for s in select_sentences(paper, claimed.get(eid, ()))
    ]
    if not selected:
        return []
    capped = cap_run(selected, claimed)
    if len(capped) < len(selected):
        logger.info(
            "[node] claim_extraction: %d of %d selected sentence(s) kept by the per-run cap",
            len(capped),
            len(selected),
        )
        selected = capped
    digest = reader_digest(ctx.load_skill(SKILL))
    use_cache = bool(model_fp) and not state.get("force_refresh", False)

    def key(sentence: GroundedSentence) -> str:
        raw = f"statement|{digest}|{sentence_key(sentence)}|{render([sentence])}"
        return hashlib.sha256(raw.encode()).hexdigest()

    answers: dict[str, Selection] = {}
    todo: list[GroundedSentence] = []
    for _, sentence in selected:
        cached = await _llm_cache_get(key(sentence), model_fp) if use_cache else None
        if cached is not None and isinstance(cached.get("answer"), dict):
            answers[sentence_key(sentence)] = Selection.model_validate(cached["answer"])
        else:
            todo.append(sentence)
    if todo:
        fresh = await read_sentences(todo, ctx)
        for sentence in todo:
            answer = fresh.get(sentence_key(sentence))
            if answer is None:
                continue
            answers[sentence_key(sentence)] = answer
            if use_cache:
                await _llm_cache_set(
                    key(sentence), model_fp, "statement", {"answer": answer.model_dump()}
                )
    by_id = {str(ev.evidence_id): ev for ev in evidences}
    drug_effects = target_drug_effects(_ot_extra(state).get("known_drugs") or [])
    variants = await _target_variants(state, [s for _, s in selected])
    out: list[TargetStatement] = []
    for eid, sentence in selected:
        extra = (by_id[eid].extra or {}) if eid in by_id else {}
        out.append(
            assemble(
                sentence,
                answers.get(sentence_key(sentence)),
                design=design_from_metadata(
                    extra.get("publication_types") or [], extra.get("mesh_headings") or []
                ),
                drug_effects=drug_effects,
                variant_effect=variant_lookup(variants),
                species=next(iter(papers[eid].species()), ""),
            )
        )
    logger.info(
        "[node] claim_extraction: %d sentence(s) selected from %d paper(s), %d read "
        "(%d from the cache), %d typing the target's change",
        len(selected),
        len(papers),
        len(answers),
        len(selected) - len(todo),
        sum(1 for st in out if st.effect_on_target != "none"),
    )
    return out


async def _target_variants(
    state: PipelineState, sentences: Iterable[GroundedSentence]
) -> dict[str, list[Any]]:
    """{rs id: Open Targets records} for the target's variants the sentences name, one
    lookup per rs id (services/evidence/variant_effect.py reads them)."""
    from mcp_servers.opentargets.tools import get_variant_target_effects

    gene_id = str(state.get("gene_id") or "")
    rsids = sorted(
        {
            m.rsid
            for s in sentences
            for m in s.mentions
            if m.kind == "variant" and m.is_target and m.rsid
        }
    )
    found: dict[str, list[Any]] = {}
    for rsid in rsids:
        found[rsid] = list(await get_variant_target_effects(rsid, gene_id))
    if rsids:
        logger.info(
            "[node] claim_extraction: %d variant(s) of the target looked up, %d with a "
            "curated direction or QTL",
            len(rsids),
            sum(1 for v in found.values() if any(r.curated or r.qtls for r in v)),
        )
    return found


def _apply_statements(
    claims: list[CoreClaim], papers: dict[str, GroundedPaper], statements: list[TargetStatement]
) -> list[CoreClaim]:
    """Each literature claim with the effect of its sentence's statement
    (claim_effect.with_statement)."""
    from services.evidence.claim_effect import with_statement

    source_of = {eid: paper.source for eid, paper in papers.items()}
    by_sentence = {(st.source, st.sentence): st for st in statements}
    out: list[CoreClaim] = []
    for c in claims:
        source = source_of.get(str(c.source_evidence_id))
        statement = (
            by_sentence.get((source, c.sentence)) if source and c.sentence is not None else None
        )
        out.append(with_statement(c, statement))
    return out


async def _perturbation_reading(state: PipelineState) -> dict[str, Any]:
    """Which way perturbing the target moved models of the disease
    (agents/interpretation/biology_lens/perturbations.py), its own direction reading, from
    the run's target statements. Empty when no statement is such a perturbation."""
    from agents.interpretation.biology_lens.perturbations import (
        perturbation_observations,
        perturbation_reading,
    )
    from services.evidence.drug_precedent import requested_effect

    papers = (await _literature_index(state)).get("papers") or {}
    observations = perturbation_observations(_statements(state), papers)
    if not observations:
        return {}
    reading = perturbation_reading(
        requested_effect(str(state.get("direction") or "")), observations
    )
    logger.info(
        "[node] biology_lens: perturbation reading %s from %d paper(s)",
        reading.verdict,
        len(reading.observations),
    )
    return reading.model_dump(mode="json")


async def _assess_engagement(
    state: PipelineState,
    reading: ProgrammeReading,
    trial_facts: list[dict[str, Any]],
    cfg: ProgrammeConfig,
    ctx: RunContext,
    disease_classes: frozenset[str],
) -> ProgrammeReading:
    """Ask, for each efficacy failure at phase 2 or later, whether its drug engaged the
    target (agents/interpretation/clinical_lens/engagement.py), caching each answer.

    A failed question leaves that programme unassessed: the reading is never lost to it.
    """
    from agents.interpretation.clinical_lens.engagement import (
        ask_engagement,
        engagement_digest,
        inputs_digest,
        programme_claims,
        programme_facts,
        programmes_to_ask,
    )
    from core.routing.classify import classify
    from schemas.evidence import engagement_fingerprint
    from services.evidence.programme_outcomes import apply_engagement

    asked = programmes_to_ask(reading)
    if not asked:
        return reading
    gene, disease = state["target_gene"], state["disease"]
    direction = state.get("direction") or "unspecified"
    claims = list(state.get("extracted_claims", []))
    quality_map = state.get("source_quality", {})
    model_fp = (
        ctx.router.select(DataClass.NON_SENSITIVE, "clinical_lens")[1]
        if state.get("model_fingerprint")
        else ""
    )
    use_cache = bool(model_fp) and not state.get("force_refresh", False)
    answers: dict[str, tuple[Engagement, list[str]]] = {}
    for programme in asked:
        named = programme_claims(programme, claims)
        facts = programme_facts(programme, trial_facts, disease)
        key = engagement_fingerprint(
            gene,
            disease,
            direction,
            programme.drug,
            inputs=engagement_digest() + inputs_digest(facts, named),
        )
        cached = await _llm_cache_get(key, model_fp) if use_cache else None
        if cached is not None and cached.get("engagement"):
            answers[programme.drug] = (cached["engagement"], list(cached.get("claim_ids") or []))
            continue
        try:
            answer = await ask_engagement(
                programme,
                facts=facts,
                claims=named,
                quality_map=quality_map,
                disease_classes=disease_classes,
                ctx=ctx,
                classification=classify(cast("list[Evidence]", named))
                if named
                else DataClass.NON_SENSITIVE,
            )
        except Exception as exc:
            logger.warning(
                "engagement question failed for %s: %s", programme.drug, describe_exception(exc)
            )
            continue
        answers[programme.drug] = answer
        if use_cache:
            await _llm_cache_set(
                key,
                model_fp,
                "engagement",
                {"engagement": answer[0], "claim_ids": answer[1]},
            )
    logger.info(
        "[node] clinical_lens: target engagement asked for %d programme(s): %s",
        len(asked),
        {d: a[0] for d, a in answers.items()},
    )
    return apply_engagement(reading, answers, cfg, disease)


def _competition_extra(state: PipelineState) -> dict[str, Any]:
    """Extract the indication-competition `extra` dict from state (empty dict if absent).

    Reads `competition_evidence` directly — that bucket is deliberately excluded
    from `_EVIDENCE_BUCKETS` (see module docstring on the bucket in state.py), so
    this mirrors `_ot_extra`/`_depmap_extra` rather than going through
    `_keep_evidence`.
    """
    for ev in state.get("competition_evidence", []):
        if ev.extra:
            return ev.extra
    return {}


def _target_programme_lines(
    state: PipelineState, scope_ids: frozenset[str] | set[str] | None = None
) -> list[str]:
    """The target's drug programmes with their competitive status (commercial lens).

    Built from Open Targets' known drugs and the retrieved phased trials whose record
    names the target — the rule screening uses to keep such trials
    (is_target_drug_trial); see `target_programmes`.
    """
    from core.evidence_text import is_target_drug_trial

    trials = [
        TargetTrial(
            nct=ev.source,
            phase=str((ev.extra or {}).get("phase") or ""),
            status=str((ev.extra or {}).get("status") or ""),
            interventions=tuple(str(i) for i in (ev.extra or {}).get("interventions") or []),
        )
        for ev in state.get("trial_evidence", [])
        if is_target_drug_trial(ev)
    ]
    from services.evidence.drug_precedent import row_approved_in_indication, row_in_indication

    disease = state.get("disease") or ""
    return target_programmes(
        list(_ot_extra(state).get("known_drugs") or []),
        trials,
        state.get("direction") or "",
        in_indication=lambda row: (
            row_in_indication(row, scope_ids, disease) and row_approved_in_indication(row)
        ),
    )


def _competition_claims(state: PipelineState) -> list[CoreClaim]:
    """Citable claims for the indication's drug/trial landscape (commercial lens).

    `competition_evidence` never enters screening or claim extraction (see
    `_competition_extra`), so without these the commercial lens could only read the
    landscape counts as prompt text — which it cannot cite, so they never counted
    toward its confidence.
    """
    return [
        c
        for ev in state.get("competition_evidence", [])
        for c in structured_claims(ev, state["run_id"], str(state["run_id"]))
    ]


_DECISIVE_VERDICTS = frozenset({"keep", "drop"})


def _verdict_of(ev: Evidence) -> str | None:
    return (ev.extra.get("screening_verdict") or {}).get("verdict")


def _dedup_screened(state: PipelineState) -> list[Evidence]:
    """Deduplicate screened_evidence keeping the last (most recent) version per ID."""
    seen: dict[Any, Evidence] = {}
    for ev in state.get("screened_evidence", []):
        seen[ev.evidence_id] = ev
    return list(seen.values())


async def _enrich_uncertain_with_full_text(
    evidences: list[Evidence], *, force: bool
) -> list[Evidence]:
    """Fetch PMC Open Access full text for ``uncertain`` PubMed items.

    Pass 1 leaves an item ``uncertain`` precisely when the abstract is not enough
    to decide. For such items with a PMID, this downloads the OA body, stores it
    in ``extra["full_text"]`` and upgrades ``scope`` to ``full_text`` so the
    second screening pass can re-judge on real content (see
    ScreeningAgent pass 2). Items with no PMID or no OA full text are returned
    unchanged and stay ``abstract``-scope (so pass 2 skips them, as before).
    Network/parse failures degrade to the original item.
    """
    out: list[Evidence] = []
    for ev in evidences:
        verdict = ev.extra.get("screening_verdict", {}).get("verdict")
        pmid = ev.source[5:] if ev.source.startswith("PMID:") else None
        already_have = ev.scope == "full_text" and ev.extra.get("full_text")
        if verdict != "uncertain" or not pmid or (already_have and not force):
            out.append(ev)
            continue
        try:
            ft = await fetch_pmc_record(pmid, with_content=True)
        except Exception as exc:  # noqa: BLE001 — degrade to abstract on any fetch error
            logger.warning("[node] screening_second: full-text fetch failed for %s: %s", pmid, exc)
            ft = None
        if ft and ft.full_text:
            out.append(
                ev.model_copy(
                    update={
                        "scope": "full_text",
                        "artifact_uri": ft.full_text_url,
                        "extra": {
                            **ev.extra,
                            "full_text": ft.full_text,
                            "full_text_sections": [
                                sec.model_dump(mode="json") for sec in ft.sections
                            ],
                            "full_text_url": ft.full_text_url,
                        },
                    }
                )
            )
        else:
            out.append(ev)
    return out


_LITERATURE_EVIDENCE = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.CONFERENCE, EvidenceType.BOOK}
)


def _log_quality_coverage(evidences: list[Evidence], quality_map: dict[str, Any]) -> None:
    """Log the share of literature documents with a quality score; a run whose literature
    has none ranks it by extractor confidence alone, which must be visible."""
    literature = [ev for ev in evidences if ev.evidence_type in _LITERATURE_EVIDENCE]
    if not literature:
        return
    scored = sum(
        1
        for ev in literature
        if (quality_map.get(str(ev.evidence_id)) or {}).get("sjr_score") is not None
    )
    log = logger.warning if scored == 0 else logger.info
    log(
        "[node] source_quality: %d/%d literature sources have a quality score",
        scored,
        len(literature),
    )


def _keep_evidence(state: PipelineState) -> list[Evidence]:
    """Return deduplicated screened evidence marked 'keep', applying HITL overrides."""
    overrides = state.get("hitl_overrides", {})
    result = []
    for ev in _dedup_screened(state):
        ev_id = str(ev.evidence_id)
        if ev_id in overrides:
            if overrides[ev_id]:
                result.append(ev)
        elif ev.extra.get("screening_verdict", {}).get("verdict") == "keep":
            result.append(ev)
    return result


_REVIEWED_LENSES = ("genetics", "biology", "safety", "clinical", "commercial", "regulatory")


def _missing_inputs(state: PipelineState, lens: str) -> list[str]:
    """The lens's required structured inputs absent from this run (services/evidence/lens_inputs)."""
    return missing_lens_inputs(
        lens,
        (ev.source for ev in _all_raw_evidence(state)),
        (ev.source for ev in _keep_evidence(state)),
    )


def _screening_no_verdict(screened: list[Evidence]) -> dict[str, dict[str, int]]:
    """Per evidence kind: items screened, and those left with an LLM non-answer."""
    counts: dict[str, dict[str, int]] = {}
    for ev in screened:
        c = counts.setdefault(ev.evidence_type.value, {"screened": 0, "no_verdict": 0})
        c["screened"] += 1
        c["no_verdict"] += is_no_verdict(ev.extra.get("screening_verdict"))
    return counts


def _review_stage_facts(state: PipelineState) -> dict[str, Any]:
    """The structured facts the reviewer turns into per-stage gaps."""
    kept = _keep_evidence(state)
    screened = _dedup_screened(state)
    kept_keys = {(ev.evidence_type.value, ev.source) for ev in kept}

    def _counts(buckets: tuple[str, ...]) -> dict[str, int]:
        evs = [ev for b in buckets for ev in cast("list[Evidence]", state.get(b, []))]
        keys = {(ev.evidence_type.value, ev.source) for ev in evs}
        return {"retrieved": len(keys), "kept": len(keys & kept_keys)}

    kept_trials = [ev for ev in kept if ev.evidence_type == EvidenceType.CLINICAL_TRIAL]
    lenses = {
        lv.lens: {
            "verdict": lv.overall_verdict,
            # Flags pointing at published text (as the critic's prose_qa counts them);
            # score adjustments such as the Mendelian floor carry no excerpt.
            "open_checks": sorted(
                {
                    f.rule_id
                    for f in lv.validation_flags
                    if f.severity in ("medium", "high")
                    and f.claim_excerpt.strip()
                    and f.rule_id != "prose_repaired"
                }
            ),
            "missing_inputs": _missing_inputs(state, lv.lens),
        }
        for lv in state.get("lens_verdicts", [])
    }
    return {
        "literature": _counts(("literature_evidence",)),
        "genetics": _counts(("genetics_evidence", "omics_evidence")),
        "clinical": {
            **_counts(("trial_evidence",)),
            "with_results": sum(1 for ev in kept_trials if (ev.extra or {}).get("results")),
        },
        "screened": len(screened),
        "screening_no_verdict": _screening_no_verdict(screened),
        "claims": len(list(state.get("extracted_claims", []))),
        "lenses": lenses,
        "expected_lenses": list(_REVIEWED_LENSES),
        "experiments": len(state.get("experiment_results", [])),
        "failed_sources": sorted(set(state.get("failed_sources", []) or [])),
        # Approved drugs on the target, and how many kept phase 3/4 trials test them:
        # an approved target with no kept late-phase trial means the outcome trials
        # behind the approvals are missing from the evidence.
        "approved_drugs": sorted(
            {
                str(d.get("drug_name") or "")
                for d in (_ot_extra(state).get("known_drugs") or [])
                if d.get("is_approved") and d.get("drug_name")
            }
        ),
        "late_phase_trials_kept": sum(
            1
            for ev in kept_trials
            if any(p in str((ev.extra or {}).get("phase") or "").upper() for p in ("3", "4"))
        ),
    }


async def _persist_evidence(evidences: list[Evidence], label: str) -> None:
    if not evidences:
        return
    try:
        async with get_session() as session:
            await EvidenceRepository(session).bulk_upsert(evidences)
    except Exception as exc:
        # str() of a SQLAlchemy error embeds the statement and every parameter — one
        # failed bulk write logged a 1 MB line.
        cause = getattr(exc, "orig", None) or exc
        logger.warning(
            "[persist] %s — DB write failed (%d items), evidence retained in state: %s",
            label,
            len(evidences),
            describe_exception(cause)[:500],
        )


# ---------------------------------------------------------------------------
# Rerun cache helpers
# ---------------------------------------------------------------------------


def _std_uuid(value: Any) -> Any:
    """A driver UUID (any uuid.UUID subclass) as a plain uuid.UUID; other values unchanged."""
    return uuid.UUID(str(value)) if isinstance(value, uuid.UUID) else value


def _row_to_evidence(row: Any) -> Evidence:
    """Reconstruct an Evidence schema object from a persisted EvidenceRow.

    The row's original run_id is preserved. Cache-hit nodes return these objects
    into state without calling _persist_evidence — rows already exist in the DB.
    """
    from schemas.evidence import DataClass, Direction, EvidenceType, Provenance

    return Evidence(
        # asyncpg returns its own UUID subclass, which the checkpoint serializer refuses
        # to restore ("Blocked deserialization of asyncpg.pgproto.pgproto.UUID"): after a
        # resume the id came back as a string and id-set lookups silently missed.
        evidence_id=_std_uuid(row.evidence_id),
        run_id=_std_uuid(row.run_id),
        schema_version=row.schema_version,
        gene=row.gene,
        gene_id=row.gene_id or "",
        disease=row.disease,
        disease_id=row.disease_id or "",
        direction=Direction(row.direction),
        availability_date=row.availability_date,
        population=row.population,
        evidence_type=EvidenceType(row.evidence_type),
        scope=row.scope,
        source=row.source,
        source_link=row.source_link,
        claim_text=row.claim_text or "",
        source_evidence_id=_std_uuid(row.source_evidence_id),
        query_used=row.query_used,
        artifact_uri=row.artifact_uri,
        extra=row.extra or {},
        classification=DataClass(row.classification),
        provenance=Provenance(
            agent_name=row.prov_agent_name,
            tool_name=row.prov_tool_name,
            timestamp=row.prov_timestamp,
            model_used=row.prov_model_used,
            trace_id=row.prov_trace_id,
        ),
    )


async def _evidence_cache_lookup(
    gene: str,
    disease: str,
    direction: str | None,
    evidence_type: str | None,
) -> list[Evidence]:
    """Query prior-run evidence by target identity. Returns [] on miss or DB error.

    ``direction=None`` matches rows of any direction (see `_complete_producer_cache`).

    Rows that no longer validate (e.g. written under an older Evidence schema) are
    skipped one by one, so a single stale row cannot discard the whole cache hit.
    """
    try:
        async with get_session() as session:
            rows = await EvidenceRepository(session).find_by_target(
                gene, disease, direction, evidence_type
            )
    except Exception as exc:
        logger.warning(
            "[cache] evidence lookup failed, fetching fresh: %s", describe_exception(exc)
        )
        return []
    evidence: list[Evidence] = []
    skipped = 0
    for row in rows:
        try:
            evidence.append(_row_to_evidence(row))
        except ValueError:  # pydantic ValidationError is a ValueError subclass
            skipped += 1
    if skipped:
        logger.warning(
            "[cache] %s evidence: skipped %d stale cached row(s), kept %d",
            evidence_type or "all",
            skipped,
            len(evidence),
        )
    return evidence


@dataclass(frozen=True)
class ProducerCache:
    """What a reusable cached retrieval of one producer must contain.

    A cache hit used to be "any row of the producer's evidence types". Another
    producer's rows of the same type (the Open Targets association is `genetics` too)
    then counted as a hit and the producer never ran, and rows the producer stored
    without the run's direction were never found. Here a hit needs this producer's own
    rows, at the current retrieval version, including every source a complete
    retrieval always writes.
    """

    producer: str  # Provenance.agent_name of the rows
    required_sources: tuple[str, ...]  # source prefixes a complete retrieval always has
    retrieval_version: int = 1  # bump when the producer's output changes shape


# The genetics agent's evidence (gnomAD, ClinVar, GWAS, curations) depends on the gene
# and disease only, so it is looked up for any direction.
# 2: the disease-scoped GWAS Catalog scan reads every significant association; version-1
#    rows stopped at 1000 and lost the disease's hits on loci led by stronger traits
#    (CETP: coronary artery disease hits rank 2253-2683).
# 3: ClinVar is read from ClinVar itself (`clinvar:`), with the condition each call is for;
#    version-2 rows carry gnomAD's condition-less mirror, so no call could be scoped.
GENETICS_CACHE = ProducerCache(
    producer="genetics", required_sources=("gnomad:",), retrieval_version=3
)


def _complete_producer_cache(rows: list[Evidence], spec: ProducerCache) -> list[Evidence]:
    """The producer's cached rows, newest per source, or [] when they are incomplete."""

    def version(ev: Evidence) -> int:
        return int((ev.extra or {}).get("retrieval_version") or 1)

    newest: dict[tuple[str, str], Evidence] = {}
    for ev in rows:
        if ev.provenance.agent_name != spec.producer or version(ev) < spec.retrieval_version:
            continue
        key = (ev.evidence_type.value, ev.source)
        if key not in newest or ev.provenance.timestamp > newest[key].provenance.timestamp:
            newest[key] = ev
    found = list(newest.values())
    if not all(any(ev.source.startswith(p) for ev in found) for p in spec.required_sources):
        return []
    return found


def _stamp_retrieval_version(evidences: list[Evidence], spec: ProducerCache) -> list[Evidence]:
    return [
        ev.model_copy(
            update={"extra": {**(ev.extra or {}), "retrieval_version": spec.retrieval_version}}
        )
        for ev in evidences
    ]


def _current_openfda_rows(rows: list[Evidence]) -> list[Evidence]:
    """Cached openFDA rows from the current label retrieval, or [] to refetch.

    The evidence cache keeps every past retrieval's rows. Labels from before
    ``RETRIEVAL_VERSION`` lack the target-drug stamps (and the labels found by drug
    name), so a cache holding only those is a miss. Rows of an older retrieval are
    dropped, FAERS bundles included: an unstamped bundle would reach the safety lens
    whatever its drug.
    """
    from services.retrieval.openfda import RETRIEVAL_VERSION

    def version(ev: Evidence) -> int:
        return int((ev.extra or {}).get("retrieval_version") or 1)

    labels = [ev for ev in rows if ev.source.startswith("fda:label:")]
    if not labels or max(version(ev) for ev in labels) < RETRIEVAL_VERSION:
        return []
    return [ev for ev in rows if version(ev) >= RETRIEVAL_VERSION]


def _current_opentargets_rows(rows: list[Evidence]) -> list[Evidence]:
    """Cached Open Targets rows whose known drugs carry stop reports; [] refetches.

    The evidence cache keeps every past retrieval's rows. Rows cached before the
    known-drug query kept every clinical report have no ``stop_reports``, and programme
    outcomes cannot be classified from them (services/evidence/programme_outcomes.py), so
    a cache holding only those is a miss.
    """
    return [
        ev
        for ev in rows
        if all("stop_reports" in d for d in (ev.extra or {}).get("known_drugs") or [])
    ]


async def _llm_cache_get(cache_key: str, model_used: str) -> dict[str, Any] | None:
    """Look up a single LLM decision. Returns None on miss or DB error."""
    if not model_used:
        return None
    try:
        async with get_session() as session:
            return await LlmCacheRepository(session).get(cache_key, model_used)
    except Exception as exc:
        logger.warning("[cache] get failed, treating as miss: %s", describe_exception(exc))
        return None


async def _llm_cache_set(
    cache_key: str,
    model_used: str,
    decision_type: str,
    payload: dict[str, Any],
) -> None:
    """Persist an LLM decision. Swallows errors so cache failures never abort the pipeline."""
    if not model_used:
        return
    try:
        async with get_session() as session:
            await LlmCacheRepository(session).set(cache_key, model_used, decision_type, payload)
    except Exception as exc:
        logger.warning("[cache] set failed: %s", describe_exception(exc))


def _lens_cache_key(
    gene: str,
    disease: str,
    direction: str,
    lens: LensName,
    spec: dict[str, Any],
    extra: str = "",
    skill: str = "",
) -> str:
    """Lens cache key over the lens's inputs, versioned by the LensVerdict schema.

    ``spec`` is the task_spec the lens agent will receive; its digest covers the routed
    claims, the extra-context fields and the lens skill prompt (see `lens_input_digest`).
    ``skill`` names the prompt when it is not ``<lens>_lens`` (the safety narrative);
    ``extra`` carries what else decides the verdict (the safety aggregation's version).
    """
    from schemas.verdicts import LensVerdict

    schema_version = LensVerdict.model_fields["schema_version"].default
    inputs = lens_input_digest(lens, spec, load_skill(skill or f"{lens}_lens"), extra)
    return lens_fingerprint(
        gene, disease, direction, lens, schema_version=schema_version, inputs=inputs
    )


async def _lens_cache_get(cache_key: str, lens: str, model_used: str) -> LensVerdict | None:
    """Look up a cached lens verdict. Returns None on miss or when the row no longer validates."""
    from pydantic import ValidationError

    from schemas.verdicts import LensVerdict

    cached = await _llm_cache_get(cache_key, model_used)
    if cached is None:
        return None
    try:
        verdict = LensVerdict.from_dict(cached)
    except ValidationError as exc:
        logger.warning("[cache] %s_lens: stale cached verdict, recomputing: %s", lens, exc)
        return None
    # The canned no-axes insufficient_evidence verdict comes from a rule (empty-evidence
    # guard or unparseable LLM output), not from reasoning. It is free to recompute, and
    # replaying a parse failure would pin the lens to it for as long as its inputs hold.
    if verdict.overall_verdict == "insufficient_evidence" and not verdict.axes:
        logger.info("[cache] %s_lens: cached verdict is a canned evidence gap, recomputing", lens)
        return None
    return verdict


# ---------------------------------------------------------------------------
# Restart-from-node configuration
# ---------------------------------------------------------------------------

_ACQUISITION_NODE_NAMES = (
    "literature",
    "patent",
    "clinical_trial",
    "opentargets",
    "genetics",
    "omics",
    "functional",
    "druggability",
    "openfda",
    "gbd",
    "indication_competition",
)

# Maps user-facing node names / aliases to canonical jump targets (actual node names).
NODE_TO_JUMP_TARGET: dict[str, str] = {
    "report": "report",
    "investigator": "investigator",
    "experiment": "experiment",
    "challenge": "experiment",  # restarts critic+reviewer+reconciler
    "critic": "experiment",
    "reviewer": "experiment",
    "reconciler": "experiment",
    "hitl_gate": "hitl_gate",
    "lenses": "hitl_gate",  # restarts all 6 lenses
    "genetics_lens": "hitl_gate",
    "biology_lens": "hitl_gate",
    "safety_lens": "hitl_gate",
    "clinical_lens": "hitl_gate",
    "commercial_lens": "hitl_gate",
    "regulatory_lens": "hitl_gate",
    "claim_extraction": "claim_extraction",
    "knowledge_extraction": "knowledge_extraction",
    "screening": "screening_first",
    "screening_first": "screening_first",
}

_REPORT_CLEAR: dict[str, object] = {
    "report_uri": None,
    "full_report_uri": None,
    "messages": [],
    "investigation_summary": "",
    "investigation_error": "",
}
_CHALLENGE_CLEAR: dict[str, object] = {
    "experiment_results": [],
    "critiques": [],
    "review_gaps": [],
    "agreement_map": None,
}
_INVESTIGATION_CLEAR: dict[str, object] = {"investigation_tools_used": []}
_LENS_CLEAR: dict[str, object] = {
    "lens_verdicts": [],
    "failed_lenses": [],
    "programme_reading": None,
}
_HITL_CLEAR = {"hitl_approved": False, "hitl_overrides": {}}
_CLAIMS_CLEAR: dict[str, object] = {
    "extracted_claims": [],
    "dropped_claims": [],
    "target_statements": [],
}
_SCREENING_CLEAR: dict[str, object] = {"screened_evidence": []}
_SOURCE_QUALITY_CLEAR: dict[str, object] = {"source_quality": {}}

# Fields to zero out per jump target; all other fields are copied from the old checkpoint.
# Because resume_pipeline injects these into a fresh thread's initial_state (not via
# aupdate_state), the _append / replace_last reducers do not run — the value is seeded
# directly.  screened_evidence is intentionally kept for post-screening restart points:
# _dedup_screened() normalises the accumulated list on every read, so seed + new output
# is safe under the existing _append + dedup pattern.
CLEAR_FROM_NODE: dict[str, dict[str, object]] = {
    "report": {
        **_REPORT_CLEAR,
    },
    "investigator": {
        # investigator → report: clear the investigation outputs so the rerun repopulates
        # them, plus the report it feeds. _REPORT_CLEAR already zeroes investigation_summary.
        **_INVESTIGATION_CLEAR,
        **_REPORT_CLEAR,
    },
    "experiment": {
        **_CHALLENGE_CLEAR,
        **_INVESTIGATION_CLEAR,
        **_REPORT_CLEAR,
    },
    "hitl_gate": {
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_INVESTIGATION_CLEAR,
        **_REPORT_CLEAR,
    },
    "claim_extraction": {
        **_CLAIMS_CLEAR,
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_INVESTIGATION_CLEAR,
        **_REPORT_CLEAR,
    },
    "knowledge_extraction": {
        **_CLAIMS_CLEAR,
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_INVESTIGATION_CLEAR,
        **_REPORT_CLEAR,
    },
    "screening_first": {
        **_SCREENING_CLEAR,
        **_CLAIMS_CLEAR,
        **_SOURCE_QUALITY_CLEAR,
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_INVESTIGATION_CLEAR,
        **_REPORT_CLEAR,
    },
}

# Upstream fields that must be non-empty for the restart to produce meaningful output.
# resume_pipeline emits a WARNING (not an error) if any are absent in the old checkpoint.
_REQUIRED_UPSTREAM: dict[str, list[str]] = {
    "report": ["lens_verdicts", "experiment_results", "agreement_map"],
    "investigator": ["lens_verdicts", "review_gaps", "agreement_map"],
    "experiment": ["lens_verdicts"],
    "hitl_gate": ["screened_evidence", "extracted_claims", "source_quality"],
    "claim_extraction": ["screened_evidence"],
    "knowledge_extraction": ["screened_evidence"],
    "screening_first": [],
}


# ---------------------------------------------------------------------------
# Safety lens structured-evidence helper
# ---------------------------------------------------------------------------


def _safety_structured_summary(rows: list[Evidence]) -> str:
    """Compact text summary of expression/constraint/omics/genetics evidence rows.

    Used by safety_lens_node to feed the LLM with structured data it cannot
    obtain from extracted_claims when knowledge-extraction has produced 0 claims.
    Mirrors the same injection pattern as ot_safety_text / ot_mouse_text.
    """
    from schemas.evidence import EvidenceType

    _TYPES = {
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.GENETICS,
        EvidenceType.CONSTRAINT,
    }
    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type not in _TYPES:
            continue
        # Granular rows (GTEx tissue, HPA, GWAS, gnomAD text) carry claim_text.
        # Blob/archive rows have empty claim_text but bundle text in extra["text"].
        text = ev.claim_text or (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    if not lines:
        return ""
    return "Structured expression / constraint / genetics evidence:\n" + "\n".join(lines)


def _biology_expression_summary(rows: list[Evidence]) -> str:
    """Compact tissue/anatomical expression summary for the biology lens.

    Reads the same GTEx/HPA/SPOKE-anatomy rows as `_safety_structured_summary`
    (OMICS/EXPRESSION evidence types) but frames them for mechanism-of-action /
    disease-tissue-overlap reasoning rather than safety-liability reasoning.
    """
    from schemas.evidence import EvidenceType

    _TYPES = {EvidenceType.OMICS, EvidenceType.EXPRESSION}
    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type not in _TYPES:
            continue
        text = ev.claim_text or (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    if not lines:
        return ""
    return (
        "Tissue/anatomical expression evidence (relevant to mechanism-of-action and "
        "disease-tissue overlap):\n" + "\n".join(lines)
    )


def _gtex_bundle_extra(rows: list[Evidence]) -> tuple[list[dict[str, Any]], str]:
    """Pull the full per-tissue GTEx list + HPA specificity off the GTEx/HPA archive blob.

    The blob row (source prefix ``gtex_hpa:``) carries the complete
    ``gtex_expressions`` list and ``hpa_tissue_specificity`` in ``extra`` — the
    granular per-tissue claim rows only cover the top-N + safety-sentinel tissues,
    which may not include the disease-relevant tissue for an arbitrary indication.
    """
    for ev in rows:
        if ev.source.startswith("gtex_hpa:"):
            extra = ev.extra or {}
            return extra.get("gtex_expressions") or [], extra.get("hpa_tissue_specificity") or ""
    return [], ""


async def _disease_tissue_context(state: PipelineState, rows: list[Evidence]) -> dict[str, Any]:
    """Resolve disease-tissue TPM/specificity + the deterministic relevance note.

    Shared by biology_lens_node and safety_lens_node so both lenses are grounded
    in the same disease-relevant tissue rather than inferring it from bulk-TPM rank.
    """
    from services.evidence.disease_tissue import (
        build_disease_tissue_expression_note,
        extract_tissue_tpm,
        resolve_disease_tissue,
        top_tpm_tissues,
    )
    from services.evidence.site_of_action import is_secreted

    gtex_expressions, hpa_specificity = _gtex_bundle_extra(rows)
    disease_id = state.get("disease_id") or ""
    area_names: set[str] = set()
    if disease_id:
        area_names = (await get_disease_descendants(disease_id)).therapeutic_area_names
    info = resolve_disease_tissue(
        disease_id, disease_name=state.get("disease") or "", area_names=area_names
    )
    note = build_disease_tissue_expression_note(gtex_expressions, info, state["disease"])

    bulk_tpm: float | None = None
    disease_tissue_label = ""
    if info and info.gtex_tissues:
        bulk_tpm, _rank, _total = extract_tissue_tpm(gtex_expressions, info.gtex_tissues[0])
        disease_tissue_label = info.gtex_tissues[0]

    return {
        "bulk_tpm": bulk_tpm,
        "hpa_specificity": hpa_specificity,
        "disease_tissue": disease_tissue_label,
        "disease_tissue_expression_note": note,
        "disease_cell_types": list(info.cell_types) if info else [],
        # Lists the post-LLM tissue-relevance guard needs to detect bulk-rank misuse.
        "top_tpm_tissues": top_tpm_tissues(gtex_expressions),
        # Every tissue's rank, for the tissue-claim guard ("kidney-specific" at 29/54).
        "gtex_tissue_ranks": {
            t["tissue"]: i + 1
            for i, t in enumerate(
                sorted(gtex_expressions, key=lambda t: t.get("median_tpm", 0.0), reverse=True)
            )
            if t.get("tissue")
        },
        "disease_relevant_tissues": list(info.gtex_tissues) if info else [],
        # A secreted protein acts beyond the tissues that make it (site_of_action.py).
        "secreted": is_secreted(
            loc
            for ev in rows
            if ev.evidence_type == EvidenceType.DRUGGABILITY and ev.source.startswith("uniprot:")
            for loc in (ev.extra or {}).get("subcellular_location") or []
        ),
    }


def _biology_regulatory_element_summary(rows: list[Evidence]) -> str:
    """Compact ENCODE cis-regulatory assay coverage summary for the biology lens.

    ENCODE's region-search-derived REGULATORY_ELEMENT rows are coarser than true
    cCRE (PLS/ELS/CTCF) classification — see mcp_servers/encode/tools.py — but still
    signal how well-characterized a locus's regulatory landscape is.
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.REGULATORY_ELEMENT:
            continue
        text = ev.claim_text or (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    if not lines:
        return ""
    return "Cis-regulatory assay coverage at the gene locus (ENCODE):\n" + "\n".join(lines)


def _faers_safety_summary(rows: list[Evidence]) -> str:
    """Compact FAERS adverse-event summary for the safety lens.

    Reads Evidence.extra directly from REGULATORY rows whose source starts with
    'fda:faers:'. Carries the tools.py caveat: FAERS is signal-generating, not
    ground truth. The safety lens must apply biological-plausibility checks before
    letting a death/serious rate shift the toxicity axis.
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.REGULATORY:
            continue
        if not ev.source.startswith("fda:faers:"):
            continue
        # Reports of a drug acting on other proteins are that drug's effects, not the
        # target's (core.evidence_text.target_relation).
        if target_relation(ev) not in (None, "acts_on_target"):
            continue
        ex = ev.extra or {}
        drug = ex.get("drug_name", ev.source)
        total = ex.get("total_reports", 0)
        if not total:
            continue
        parts = [f"FAERS — {drug}: {total:,} total reports"]
        # Shares of the reports, not rates: FAERS has no exposure denominator, and a report
        # with a fatal outcome is not a death the drug caused.
        serious_share = ex.get("serious_rate")
        fatal_share = ex.get("death_rate")
        if serious_share is not None:
            parts.append(f"{serious_share:.1%} of reports serious")
        if fatal_share is not None:
            parts.append(f"{fatal_share:.1%} of reports with a fatal outcome")
        top = ex.get("top_reactions") or []
        if top:
            top3 = ", ".join(f"{r.get('reaction', '?')} ({r.get('count', 0):,})" for r in top[:3])
            parts.append(f"top reactions: {top3}")
        bw = ex.get("boxed_warning", "")
        if bw:
            parts.append(f"BLACK BOX: {bw[:120]}")
        contra = ex.get("contraindications", "")
        if contra:
            parts.append(f"contraindications: {contra[:120]}")
        lines.append(". ".join(parts) + ".")
    if not lines:
        return ""
    return (
        "FAERS adverse-event signal (signal-generating only — not ground truth; spontaneous "
        "reports with no exposure denominator, so counts and shares are not incidence and do "
        "not show causation; apply biological-plausibility check before drawing safety "
        "conclusions):\n" + "\n".join(lines)
    )


def _patent_counts(rows: list[Evidence], gene: str) -> tuple[int, int]:
    """(target_patents, indication_only_patents) for the commercial lens.

    The USPTO title query matches the gene OR the disease, so only titles naming the gene are
    target IP. Recomputed from the stored title (not a stored flag) so evidence cached before
    the flag existed is counted correctly too.
    """
    from mcp_servers.uspto.tools import title_mentions

    target = sum(1 for ev in rows if title_mentions(str(ev.extra.get("title", "")), gene))
    return target, len(rows) - target


def _patent_family_groups(rows: list[Evidence]) -> dict[str, str]:
    """Map each patent's evidence id to an approximate family key (assignee + title).

    The USPTO search returns no family id, but continuations and divisionals of one
    application keep its assignee and title — five "INHIBITORS OF TRPC6" filings from
    one company are one piece of IP, not five. The lens confidence scorer counts each
    group as a single independent source.
    """
    from schemas.evidence import EvidenceType

    groups: dict[str, str] = {}
    for ev in rows:
        if ev.evidence_type != EvidenceType.PATENT:
            continue
        assignee = " ".join(str(ev.extra.get("assignee") or "").lower().split())
        title = " ".join(str(ev.extra.get("title") or "").lower().split())
        if assignee and title:
            groups[str(ev.evidence_id)] = f"patent-family:{assignee}|{title}"
    return groups


def _fda_label_summary(
    rows: list[Evidence],
    *,
    direction: str = "",
    disease_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
    scope: DiseaseScope | None = None,
) -> str:
    """Compact FDA-label summary for the commercial and regulatory lenses.

    Reads Evidence.extra from REGULATORY rows whose source starts with
    'fda:label:'. Surfaces approved-drug MoA, indications, and label-level
    safety flags (black-box warnings, contraindications). For a drug acting on the
    target it also says whether it acts in the requested direction (an agonist's warning
    is not a degrader's) and quotes a warning that the drug can cause or worsen this
    indication: the lens sorted a mixed pool of on-target, opposite-direction and
    competitor labels itself and missed both.
    """
    from core.evidence_text import indicates_disease, label_acts_on_target, label_indications
    from schemas.evidence import EvidenceType
    from services.evidence.direction_of_effect import label_forms, warns_of
    from services.evidence.drug_precedent import drug_effect, requested_effect

    want = requested_effect(direction)
    forms, parent_forms = label_forms(disease_names, parent_names, scope)

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.REGULATORY:
            continue
        if not ev.source.startswith("fda:label:"):
            continue
        ex = ev.extra or {}
        drug = ex.get("drug_name", ev.source)
        moa = (ex.get("mechanism_of_action") or "")[:200]
        # Every indication, not a fixed-length prefix: the first 200 characters of
        # sparsentan's label held only its IgAN indication and hid the FSGS one.
        indications = label_indications(ex.get("indications_and_usage") or "")
        app = ex.get("application_number", "")
        bw = (ex.get("boxed_warning") or "")[:200]
        contra = (ex.get("contraindications") or "")[:150]
        parts = [f"Drug: {drug}"]
        if app:
            parts.append(f"NDA/BLA: {app}")
        if moa:
            parts.append(f"MoA: {moa}")
        if indications:
            parts.append("Indications: " + "; ".join(indications))
            in_disease = any(indicates_disease(i, ev.disease) for i in indications)
            parts.append(
                f"Approved in this indication ({ev.disease}): {'YES' if in_disease else 'no'}"
            )
        parts.append(f"BLACK BOX: {bw}" if bw else "BLACK BOX: none")
        # Whether this label bears on the target at all (its MoA names the gene).
        acts_on_target = label_acts_on_target(ev)
        parts.append(
            f"Labelled mechanism names {ev.gene}: "
            + ("YES" if acts_on_target else f"no — its safety findings are not {ev.gene} effects")
        )
        if acts_on_target and want != "unknown":
            effect = drug_effect(ex.get("target_action_type"))
            parts.append(
                "Direction vs requested: "
                + (
                    "same"
                    if effect == want
                    else "unknown"
                    if effect == "unknown"
                    else "OPPOSITE — its pharmacology and warnings are the other direction's"
                )
            )
        if acts_on_target and (forms or parent_forms):
            warning_text = " ".join(str(ex.get(k) or "") for k in ("boxed_warning", "warnings"))
            sentences = re.split(r"(?<=[.;])\s+", warning_text)
            hit = next((x for x in sentences if warns_of(x, forms)), None) or next(
                (x for x in sentences if warns_of(x, parent_forms)), None
            )
            if hit:
                parts.append(
                    f'Warns it can cause or worsen this indication: YES — "{" ".join(hit.split())[:160]}"'
                )
        if contra:
            parts.append(f"Contraindications: {contra}")
        lines.append(" | ".join(parts))
    if not lines:
        return ""
    return "FDA-approved drug labels (MoA, indications, label safety flags):\n" + "\n".join(lines)


async def _disease_name_sets(state: PipelineState) -> dict[str, list[str]]:
    """The run disease's names and synonyms and its parents' names (Open Targets)."""
    disease = state.get("disease") or ""
    disease_id = state.get("disease_id") or ""
    onto = await get_disease_descendants(disease_id) if disease_id else None
    names = sorted({disease, *(onto.names if onto else ())} - {""})
    return {"disease_names": names, "parent_names": sorted(onto.parent_names if onto else ())}


def _norm_disease_name(name: str) -> str:
    low = re.sub(r"'s\b", "", name.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", low).split())


def _names_disease(disorder: str, disease: str) -> bool:
    """A prevalence record is the indication's own only when it names the indication
    itself (case, punctuation and possessives aside). A more specific record is a
    subtype — "young-onset Parkinson disease" is not Parkinson disease — see
    `_is_subtype`."""
    a, b = _norm_disease_name(disorder), _norm_disease_name(disease)
    return bool(a and b) and a == b


def _is_subtype(disorder: str, disease: str) -> bool:
    """The record names a form of the indication (its name contains the indication's)."""
    a, b = _norm_disease_name(disorder), _norm_disease_name(disease)
    return bool(a and b) and a != b and f" {b} " in f" {a} "


def orphanet_prevalence_summary(rows: list[Evidence], disease: str) -> tuple[str, bool]:
    """Compact disease-prevalence summary for the commercial lens's market-size axis, and
    whether any record is the indication's own.

    Reads GENETICS rows whose source starts with 'orphanet_prevalence:'. Those records
    come from the *gene's* Orphanet associations, so they size the gene's rare disorders,
    not necessarily the run's indication (PCSK9 × myocardial infarction: homozygous FH at
    1-9 / 1,000,000 was read as "MI is ultra-rare"). Records naming the indication are
    listed as its prevalence; the others are named but marked as not the indication's.
    """
    from schemas.evidence import EvidenceType

    indication: list[str] = []
    subtype: list[str] = []
    other: list[str] = []
    legacy: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.GENETICS:
            continue
        if not ev.source.startswith("orphanet_prevalence:"):
            continue
        records = (ev.extra or {}).get("records") or []
        if not records:  # older rows carry only the pre-rendered summary
            if (ev.extra or {}).get("summary"):
                legacy.append(ev.extra["summary"])
            continue
        for r in records:
            name = str(r.get("disorder_name") or "")
            line = (
                f"{name} (ORPHA:{r.get('orphacode', '')}): {r.get('prevalence_class', '')} "
                f"[{r.get('prevalence_type', '')}, {r.get('geographic_area', '')}, "
                f"{r.get('validation_status', '')}]"
            )
            if _names_disease(name, disease):
                indication.append(line)
            elif _is_subtype(name, disease):
                subtype.append(line)
            else:
                other.append(line)
    lines: list[str] = []
    if indication:
        lines.append(f"Orphanet prevalence of {disease}:")
        lines.extend(f"  • {ln}" for ln in dict.fromkeys(indication))
    if subtype:
        lines.append(
            f"Orphanet prevalence of subtypes of {disease} only — a lower bound; {disease} "
            "as a whole is more common. Do not size the whole indication from these, and "
            "do not call it rare because a subtype is:"
        )
        lines.extend(f"  • {ln}" for ln in dict.fromkeys(subtype))
    if other:
        names = sorted({ln.split(" (ORPHA:")[0] for ln in other})
        lines.append(
            f"Orphanet prevalence records were retrieved only for disorders associated with "
            f"the gene ({'; '.join(names)}). They do not name {disease}: they are those "
            f"disorders' prevalence, not the indication's — do not size {disease} from them."
        )
    lines.extend(legacy)
    return "\n".join(lines), bool(indication or legacy)


def _gbd_prevalence_summary(rows: list[Evidence]) -> str:
    """Compact disease-prevalence summary for the commercial lens's market-size axis.

    Reads Evidence.extra from EPIDEMIOLOGY rows whose source starts with
    'gbd:burden:'. Whole-population, disease-keyed prevalence/incidence —
    the common-disease counterpart to _orphanet_prevalence_summary's
    rare-disease, gene-first signal.
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.EPIDEMIOLOGY:
            continue
        if not ev.source.startswith("gbd:burden:"):
            continue
        text = (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    return "\n".join(lines)


def _indication_competition_summary(state: PipelineState) -> str:
    """Disease-keyed drug/trial landscape text for the commercial lens, regardless
    of mechanism. Reads `competition_evidence` straight from state (it never enters
    screening — see `_competition_extra`), so "" when the query found no confident
    indication match rather than when screening dropped it.
    """
    return str(_competition_extra(state).get("text", ""))


# ---------------------------------------------------------------------------
# Genetics lens structured-evidence helpers (B2 grounding)
# ---------------------------------------------------------------------------


def _genetics_source_evidence_text(evidence_rows: list[Evidence]) -> str:
    """Compact structured text of genetics+constraint evidence for the genetics lens prompt.

    Rendered from structured fields (not free-text) so the lens can reason over
    gnomAD, ClinVar, OT scores, and GWAS even when claim extraction yields 0 claims.
    """
    from schemas.evidence import EvidenceType

    _TYPES = {EvidenceType.GENETICS, EvidenceType.CONSTRAINT}
    lines: list[str] = []
    for ev in evidence_rows:
        if ev.evidence_type not in _TYPES:
            continue
        x = ev.extra or {}
        text = x.get("text") or ""
        if not text:
            if "genetic_score" in x:
                text = (
                    f"OT genetic_score={x.get('genetic_score')}, overall={x.get('overall_score')}"
                )
            elif "summary" in x:
                text = str(x["summary"])[:300]
            elif "pvalue" in x:
                text = (
                    f"GWAS p={x.get('pvalue')}, trait={x.get('trait')}, "
                    f"study={x.get('study_accession') or x.get('study_id')}"
                )
                sample_size = x.get("initial_sample_size")
                if sample_size:
                    text += f", cohort={sample_size}"
            elif "pathogenic" in x:
                p = len(x.get("pathogenic") or [])
                lp = len(x.get("likely_pathogenic") or [])
                text = f"ClinVar: {p} Pathogenic, {lp} Likely-Pathogenic variants"
            elif "loeuf" in x or "pli" in x:
                from services.evidence.constraint_interpret import interpret_constraint as _ic

                _r = _ic(
                    gene_symbol=ev.gene or "unknown",
                    loeuf=x.get("loeuf"),
                    pli=x.get("pli"),
                    mis_z=x.get("mis_z"),
                    moeuf=x.get("moeuf"),
                )
                text = _r.summary_text or (
                    f"gnomAD: LOEUF={x.get('loeuf')}, pLI={x.get('pli')}, mis_z={x.get('mis_z')}"
                )
        if text:
            lines.append(f"[{ev.evidence_type.value}|{ev.source}] {text[:300]}")
    if not lines:
        return ""
    return "Source genetics/constraint evidence:\n" + "\n".join(lines)


def _genetics_floor_signals(
    evidence_rows: list[Evidence],
    disease_scope: frozenset[str] | None = None,
    disease_ancestors: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Extract key genetics signals used for the B3 verdict floor check.

    Returns max OT genetic_score, total P/LP ClinVar variant count, count of
    high-star P/LP variants, a pre-computed ConstraintReading (as flags dict),
    the inferred mechanism direction, and a segregation keyword signal.

    All computed values are deterministic — they feed the post-LLM floor checks
    and direction reconciliation in the genetics lens agent.
    """
    import re

    from schemas.evidence import EvidenceType
    from services.evidence.constraint_interpret import (
        infer_mechanism_direction as _imd,
    )
    from services.evidence.constraint_interpret import (
        interpret_constraint as _ic,
    )

    max_genetic_score = 0.0
    segregation_signal = False

    # Accumulate across evidence rows
    gene_symbol = "unknown"
    constraint_kwargs: dict[str, Any] = {}
    lof_kwargs: dict[str, Any] = {}
    p_calls: list[dict[str, Any]] = []
    lp_calls: list[dict[str, Any]] = []
    graph_association: dict[str, Any] | None = None
    inheritance_mode: str | None = None
    hpo_phenotype_count = 0
    hpo_specificity_band = "unknown"
    clingen_classification: str | None = None
    out_of_scope_curations: list[str] = []
    # GWAS Catalog traits matched to the run's disease. The connector already scopes hits
    # to the disease, and emits them as one row per study (``trait`` field) plus, when it
    # dropped off-target hits, a ``locus_breadth_summary`` row with ``kept_traits``.
    gwas_traits: set[str] = set()

    def _conflicting(v: dict[str, Any]) -> bool:
        return "conflicting" in str(v.get("clinical_significance") or "").lower()

    _SEG_RE = re.compile(r"segregat|pedigree|de[ -]?novo|co-?segregat|famil", re.IGNORECASE)
    # SPOKE edge sources that corroborate gene-disease causality (vs. pure
    # text-mining co-mention sources, which carry the same DISEASES score key
    # but weaker evidential weight).
    _CORROBORATING_SPOKE_SOURCES = frozenset({"GWAS", "HPO", "DISEASES", "ClinVar"})
    _MENDELIAN_SPOKE_SOURCES = frozenset({"HPO", "ClinVar", "OMIM", "Orphanet"})

    for ev in evidence_rows:
        x = ev.extra or {}
        gene_symbol = ev.gene or gene_symbol

        if ev.evidence_type == EvidenceType.GENETICS:
            if ev.source.startswith("gwas_catalog:"):
                if x.get("trait"):
                    gwas_traits.add(str(x["trait"]))
                gwas_traits.update(str(t) for t in x.get("kept_traits") or [])
            if "hpo_specificity_band" in x:
                # Ontology constraint bundle: inheritance mode + HPO breadth.
                if x.get("inheritance_mode"):
                    inheritance_mode = x["inheritance_mode"]
                hpo_phenotype_count = x.get("hpo_phenotype_count") or 0
                hpo_specificity_band = x.get("hpo_specificity_band") or "unknown"
            if "associations" in x and "total" in x:
                # ClinGen / GenCC gene-disease validity row. Only a curation for the
                # run's disease (or an ontology descendant) counts: the gene's other
                # Mendelian diseases are not validation for this indication.
                assocs = x.get("associations") or []
                disease_name = ev.disease or ""
                if disease_scope is None and not disease_name:
                    scoped = assocs[0].get("classification") if assocs else None
                else:
                    scoped = strongest_in_scope(
                        assocs, scope_ids=disease_scope, disease_name=disease_name
                    )
                    if assocs and scoped is None:
                        out_of_scope_curations.extend(
                            str(a.get("disease_label") or a.get("disease_title") or "")
                            for a in assocs[:3]
                        )
                if scoped and (
                    clingen_classification is None
                    or CLASSIFICATION_RANK.get(scoped.lower(), -1)
                    > CLASSIFICATION_RANK.get(clingen_classification.lower(), -1)
                ):
                    clingen_classification = scoped
            if "edge_sources" in x:
                # SPOKE graph association row (Disease-ASSOCIATES-Gene edge).
                sources = x.get("edge_sources") or []
                score = x.get("diseases_score")
                corroborates = bool(set(sources) & _CORROBORATING_SPOKE_SOURCES)
                if graph_association is None or (
                    score is not None and score > (graph_association.get("diseases_score") or 0.0)
                ):
                    graph_association = {
                        "disease_name": x.get("disease_name", ""),
                        "edge_sources": sources,
                        "gwas_pvalue": x.get("gwas_pvalue"),
                        "diseases_score": score,
                        "corroborates_causality": corroborates,
                        # Only curated Mendelian sources make an edge Mendelian-grade;
                        # GWAS and DISEASES text-mining edges are association evidence
                        # (PCSK9 × MI was graded "Mendelian" from a DISEASES/GWAS edge).
                        "mendelian_sources": bool(set(sources) & _MENDELIAN_SPOKE_SOURCES),
                    }
            gs = x.get("genetic_score", 0.0)
            if isinstance(gs, (int, float)) and gs > max_genetic_score:
                max_genetic_score = float(gs)
            # Scan text fields for segregation/pedigree keywords (Step 3 bonus)
            for field in ("text", "summary", "assoc_text"):
                val = x.get(field) or ""
                if val and _SEG_RE.search(val):
                    segregation_signal = True
                    break

        elif ev.evidence_type == EvidenceType.CONSTRAINT:
            if "pathogenic" in x:
                # "Conflicting classifications of pathogenicity" sits in the pathogenic
                # bucket but is not a P/LP assertion.
                p_calls += [v for v in x.get("pathogenic") or [] if not _conflicting(v)]
                lp_calls += [v for v in x.get("likely_pathogenic") or [] if not _conflicting(v)]
            elif "loeuf" in x or "pli" in x:
                # gnomAD constraint — keep the latest row (one per gene expected)
                constraint_kwargs = {
                    "loeuf": x.get("loeuf"),
                    "pli": x.get("pli"),
                    "mis_z": x.get("mis_z"),
                    "moeuf": x.get("moeuf"),
                }
            elif "any_homozygous" in x:
                # gnomAD LoF variants: whether people lacking the target exist. Never read
                # before, so hom_lof_present was None in every run (HSD17B13's common
                # homozygous carriers did not reach the safety lens).
                lof_kwargs = {
                    "hc_lof_count": x.get("hc_lof_count"),
                    "any_homozygous": x.get("any_homozygous"),
                }

    run_disease = next((ev.disease for ev in evidence_rows if ev.disease), "")
    gene_only = disease_scope is None and not any(ev.disease for ev in evidence_rows)

    # ClinVar calls for the run's disease (services/evidence/disease_scope.scope_calls): a
    # call is for the condition it names. ClinVar's own record names it; gnomAD's mirror does
    # not, and its calls count only when a curation for the indication agrees.
    curation_agrees = (
        CLASSIFICATION_RANK.get((clingen_classification or "").lower(), -1)
        >= CLASSIFICATION_RANK["moderate"]
    )
    scoped_p, scoped_lp = (
        scope_calls(
            calls,
            scope_ids=disease_scope,
            disease_name=run_disease,
            curation_agrees=curation_agrees,
            ancestor_ids=disease_ancestors,
            gene=gene_symbol,
        )
        for calls in (p_calls, lp_calls)
    )  # fmt: skip
    plp_count = len(scoped_p.counted) + len(scoped_lp.counted)
    high_star_plp = sum(1 for v in scoped_p.counted if (v.get("gold_stars") or 0) >= 1)
    other_calls = scoped_p.other + scoped_lp.other
    # The mechanism of the disorder the run's calls cause; the calls of the gene's other
    # disorders only when none is left, read then as a related disorder's mechanism.
    all_plp = scoped_p.votes + scoped_lp.votes
    from_other_disorders = not all_plp and bool(other_calls)
    if from_other_disorders:
        all_plp = other_calls

    # Compute ConstraintReading and mechanism direction
    constraint_reading_flags: dict[str, Any] = {}
    mechanism_direction: dict[str, Any] | None = None

    if constraint_kwargs:
        reading = _ic(gene_symbol=gene_symbol, **constraint_kwargs, **lof_kwargs)
        # Full ConstraintReading.model_dump() — not just the boolean flags — so the
        # lens agent's post-LLM guard (apply_constraint_guards) can reconstruct the
        # reading and annotate hallucinated/inverted constraint claims in the
        # LLM-generated narrative/rationale without re-querying evidence.
        constraint_reading_flags = reading.model_dump()
        if all_plp:
            md = _imd(reading, all_plp, inheritance_mode=inheritance_mode)
            mechanism_direction = {
                "direction": md.direction.value,
                "mechanism": md.mechanism,
                "confidence": md.confidence,
                "rationale": md.rationale,
                "supporting_variant_ids": md.supporting_variant_ids,
                "from_other_disorders": from_other_disorders,
            }

    return {
        "max_genetic_score": max_genetic_score,
        "plp_count": plp_count,
        "high_star_plp": high_star_plp,
        "segregation_signal": segregation_signal,
        "constraint_reading": constraint_reading_flags,
        "mechanism_direction": mechanism_direction,
        "graph_association": graph_association,
        "inheritance_mode": inheritance_mode,
        "hpo_phenotype_count": hpo_phenotype_count,
        "hpo_specificity_band": hpo_specificity_band,
        "clingen_classification": clingen_classification,
        # Curated diseases of the gene that are NOT the run's indication (for prose:
        # "its Mendelian curation is for X, not for this disease").
        "out_of_scope_curations": sorted({c for c in out_of_scope_curations if c}),
        "gwas_traits_in_scope": len(gwas_traits),
        # ClinVar via gnomAD carries no condition, so gene-wide P/LP counts are
        # indication-specific only when a curation for the indication agrees.
        "graph_in_scope": graph_association is None
        or not run_disease
        or names_match(str(graph_association.get("disease_name") or ""), run_disease),
        "plp_in_scope": curation_agrees
        or scoped_p.named_in_scope + scoped_lp.named_in_scope > 0
        or gene_only,
        # ClinVar calls naming only other disorders of the gene: never counted for this one.
        "plp_out_of_scope": len(other_calls),
        "plp_out_of_scope_conditions": sorted(
            {str(c.get("name") or "") for v in other_calls for c in v.get("conditions") or []}
            - {""}
        )[:5],
    }


async def _disease_ancestors(state: PipelineState) -> frozenset[str]:
    """The run disease's ontology ancestors (cached per disease): broader classes of it."""
    disease_id = state.get("disease_id") or ""
    if not disease_id:
        return frozenset()
    return frozenset((await get_disease_descendants(disease_id)).ancestor_ids)


async def _disease_scope(state: PipelineState) -> frozenset[str] | None:
    """The run disease's id plus its ontology descendants (cached per disease)."""
    disease_id = state.get("disease_id") or ""
    if not disease_id:
        return None
    onto = await get_disease_descendants(disease_id)
    return frozenset(onto.efo_ids or {disease_id})


async def _resolve_disease_classes(
    state: PipelineState, floor_signals: dict[str, Any]
) -> list[str]:
    """Resolve the disease-class set once per lens node and serialise it for
    task_spec (replaces the old `_ONCOLOGY_AREA_IDS` binary — see
    services.evidence.disease_class). ``floor_signals`` should be that node's
    already-computed `_genetics_floor_signals(...)` result so the Mendelian
    floor check reuses signals the node needed anyway.
    """
    disease_id = state.get("disease_id") or ""
    therapeutic_areas: set[str] = set()
    if disease_id:
        onto = await get_disease_descendants(disease_id)
        therapeutic_areas = onto.therapeutic_areas
    classes = resolve_disease_class(disease_id, therapeutic_areas, floor_signals)
    return sorted(c.value for c in classes)


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_graph(
    router: Router, checkpointer: Checkpointer = None
) -> CompiledStateGraph[PipelineState, None, PipelineState, PipelineState]:
    """Build and compile the pipeline StateGraph.

    Pass checkpointer=None for in-memory execution (e.g. tests).
    In production pass AsyncPostgresSaver from core.checkpoint.
    """
    init_telemetry()

    def _c(state: PipelineState) -> RunContext:
        return _ctx(state, router)

    def _task_fp(state: PipelineState, task: str) -> str:
        """LLM-cache model key for one task: the model the router picks for it.

        The run's model_fingerprint is the screening model, so keying a lens on it would
        let a verdict cached under all_local (qwen) be replayed under hybrid (Claude).
        An empty run fingerprint still means "caching disabled".
        """
        if not state.get("model_fingerprint", ""):
            return ""
        return router.select(DataClass.NON_SENSITIVE, task)[1]

    # ── Restart router ─────────────────────────────────────────────────────
    # Entry point for every graph invocation. The actual routing lives in the
    # conditional edge below (_restart_route); this node is a passthrough so the
    # decision is made by an edge, not a Command. (A Command(goto=...) does NOT
    # suppress a node's static out-edges in this LangGraph version — they fire in
    # addition to the goto target, which would re-run all acquisition on a resume.)

    async def restart_router_node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        return {}

    def _restart_route(state: PipelineState, config: RunnableConfig) -> list[str] | str:
        # Restart run → jump straight to the requested node (exclusive).
        # Fresh run   → fan out to all acquisition nodes in parallel.
        jump_target = config.get("configurable", {}).get("restart_from")
        if jump_target:
            logger.info("[restart_router] jumping to node: %s", jump_target)
            return str(jump_target)
        return list(_ACQUISITION_NODE_NAMES)

    # ── Data acquisition nodes ──────────────────────────────────────────────
    # All acquisition nodes swallow exceptions so one failing data source
    # cannot abort the whole pipeline. Errors are logged at WARNING level and
    # appear in Langfuse traces via the enclosing OTel span.

    async def literature_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "article")
            if cached:
                logger.info("[node] literature: %d items from cache (skipping API)", len(cached))
                # Rows cached before publication types and MeSH headings were kept get
                # them now (one efetch per 200 PMIDs), and are stored back.
                cached, enriched = await with_article_metadata(cached)
                if enriched:
                    logger.info("[node] literature: indexing metadata added to %d rows", enriched)
                    await _persist_evidence(cached, "literature")
                # Rows cached before the current role queries get those queries' papers.
                if lacks_role_queries(cached):
                    added = await role_query_evidence(
                        _task_msg(state, "literature", {}),
                        gene=gene,
                        gene_id=state.get("gene_id") or "",
                        disease=disease,
                        disease_id=state.get("disease_id") or "",
                        direction=direction,
                        disease_mesh=await _disease_mesh(disease),
                        phenotypes=await _model_phenotypes(disease),
                        seen={ev.source.removeprefix("PMID:") for ev in cached},
                    )
                    logger.info(
                        "[node] literature: %d rows from the current role queries", len(added)
                    )
                    await _persist_evidence(added, "literature")
                    cached = [*cached, *added]
                return {"literature_evidence": cached, "messages": []}
        msg = _task_msg(
            state,
            "literature",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "population": state.get("population"),
                "model_phenotypes": await _model_phenotypes(disease),
            },
        )
        try:
            result = await LiteratureAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning(
                "literature node failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"literature_evidence": [], "failed_sources": ["literature"], "messages": []}
        ev = _evidences(result)
        await _persist_evidence(ev, "literature")
        logger.info("[node] literature: %d items", len(ev))
        return {"literature_evidence": ev, "messages": [result]}

    async def patent_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "patent")
            if cached:
                logger.info("[node] patent: %d items from cache (skipping API)", len(cached))
                return {"patent_evidence": cached}
        try:
            ev = await fetch_patents(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "patent service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"patent_evidence": [], "failed_sources": ["patent"]}
        await _persist_evidence(ev, "patent")
        logger.info("[node] patent: %d items", len(ev))
        return {"patent_evidence": ev}

    async def clinical_trial_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "clinical_trial")
            if cached:
                logger.info(
                    "[node] clinical_trial: %d items from cache (skipping API)", len(cached)
                )
                # Trials cached before hard-endpoint secondaries and per-arm deaths were
                # kept have their posted results fetched again, and are stored back.
                from services.retrieval.clinical_trial import with_current_results

                cached, refreshed = await with_current_results(cached)
                if refreshed:
                    logger.info("[node] clinical_trial: results refetched for %d trials", refreshed)
                    await _persist_evidence(cached, "clinical_trial")
                return {"trial_evidence": cached}
        try:
            ev = await fetch_trials(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                population=state.get("population"),
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "clinical_trial service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"trial_evidence": [], "failed_sources": ["clinical_trial"]}
        await _persist_evidence(ev, "clinical_trial")
        logger.info("[node] clinical_trial: %d items", len(ev))
        return {"trial_evidence": ev}

    async def opentargets_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            # opentargets produces "genetics" type evidence; pass None to catch all types it may emit
            cached = await _evidence_cache_lookup(gene, disease, direction, None)
            ot_cached = _current_opentargets_rows(
                [
                    e
                    for e in cached
                    if e.evidence_type.value in ("genetics",)
                    and e.provenance.agent_name == "opentargets"
                ]
            )
            if ot_cached:
                logger.info(
                    "[node] opentargets: %d items from cache (skipping API)", len(ot_cached)
                )
                updates: dict[str, Any] = {"opentargets_evidence": ot_cached}
                if ot_cached[0].gene_id:
                    updates["gene_id"] = ot_cached[0].gene_id
                if ot_cached[0].disease_id:
                    updates["disease_id"] = ot_cached[0].disease_id
                return updates
        try:
            result = await fetch_opentargets(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "opentargets service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"opentargets_evidence": [], "failed_sources": ["opentargets"]}
        await _persist_evidence(result.evidences, "opentargets")
        logger.info("[node] opentargets: %d items", len(result.evidences))
        updates = {"opentargets_evidence": result.evidences}
        if result.gene_id:
            updates["gene_id"] = result.gene_id
        if result.disease_id:
            updates["disease_id"] = result.disease_id
        return updates

    async def genetics_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        if not state.get("force_refresh", False):
            gen_cached = _complete_producer_cache(
                await _evidence_cache_lookup(gene, disease, None, None), GENETICS_CACHE
            )
            if gen_cached:
                logger.info(
                    "[node] genetics: %d items from cache (skipping agent)", len(gen_cached)
                )
                return {"genetics_evidence": gen_cached, "messages": []}
        msg = _task_msg(
            state,
            "genetics",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
            },
        )
        try:
            result = await GeneticsAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning(
                "genetics node failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"genetics_evidence": [], "failed_sources": ["genetics"], "messages": []}
        ev = _stamp_retrieval_version(_evidences(result), GENETICS_CACHE)
        await _persist_evidence(ev, "genetics")
        logger.info("[node] genetics: %d items", len(ev))
        return {"genetics_evidence": ev, "messages": [result]}

    async def omics_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            # omics agent produces OMICS + EXPRESSION types; check for either
            cached = await _evidence_cache_lookup(gene, disease, direction, None)
            omics_cached = [e for e in cached if e.evidence_type.value in ("omics", "expression")]
            if omics_cached:
                logger.info("[node] omics: %d items from cache (skipping agent)", len(omics_cached))
                return {"omics_evidence": omics_cached, "messages": []}
        msg = _task_msg(
            state,
            "omics",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "tissue": state.get("tissue"),
            },
        )
        try:
            result = await OmicsAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning(
                "omics node failed, continuing without: %s", describe_exception(exc), exc_info=True
            )
            return {"omics_evidence": [], "failed_sources": ["omics"], "messages": []}
        ev = _evidences(result)
        await _persist_evidence(ev, "omics")
        logger.info("[node] omics: %d items", len(ev))
        return {"omics_evidence": ev, "messages": [result]}

    async def functional_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "functional_genomics")
            if cached:
                logger.info("[node] functional: %d items from cache (skipping API)", len(cached))
                return {"functional_evidence": cached}
        try:
            ev = await fetch_functional(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "functional service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"functional_evidence": [], "failed_sources": ["functional"]}
        await _persist_evidence(ev, "functional")
        logger.info("[node] functional: %d items", len(ev))
        return {"functional_evidence": ev}

    async def druggability_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "druggability")
            if cached:
                logger.info("[node] druggability: %d items from cache (skipping API)", len(cached))
                return {"druggability_evidence": cached}
        try:
            ev = await fetch_druggability(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "druggability service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"druggability_evidence": [], "failed_sources": ["druggability"]}
        await _persist_evidence(ev, "druggability")
        logger.info("[node] druggability: %d items", len(ev))
        return {"druggability_evidence": ev}

    async def openfda_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = _current_openfda_rows(
                await _evidence_cache_lookup(gene, disease, direction, "regulatory")
            )
            if cached:
                logger.info("[node] openfda: %d items from cache (skipping API)", len(cached))
                return {"openfda_evidence": cached}
        try:
            ev = await fetch_openfda(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "openfda service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"openfda_evidence": [], "failed_sources": ["openfda"]}
        await _persist_evidence(ev, "openfda")
        logger.info("[node] openfda: %d items", len(ev))
        return {"openfda_evidence": ev}

    async def gbd_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "epidemiology")
            if cached:
                logger.info("[node] gbd: %d items from cache (skipping lookup)", len(cached))
                return {"gbd_evidence": cached}
        try:
            ev = await fetch_gbd(
                disease,
                disease_id=state.get("disease_id") or "",
                gene=gene,
                gene_id=state.get("gene_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "gbd service failed, continuing without: %s", describe_exception(exc), exc_info=True
            )
            return {"gbd_evidence": [], "failed_sources": ["gbd"]}
        await _persist_evidence(ev, "gbd")
        logger.info("[node] gbd: %d items", len(ev))
        return {"gbd_evidence": ev}

    async def indication_competition_node(state: PipelineState) -> dict[str, Any]:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "competition")
            if cached and not any(lacks_name_search(ev) for ev in cached):
                logger.info(
                    "[node] indication_competition: %d items from cache (skipping API)",
                    len(cached),
                )
                return {"competition_evidence": cached}
        scope = await _text_scope(state)
        forms = sorted(name_forms(disease))
        try:
            ev = await fetch_indication_competition(
                disease=disease,
                disease_id=state.get("disease_id") or "",
                gene=gene,
                gene_id=state.get("gene_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
                alternatives=[*forms, *sorted(scope.names - set(forms))],
            )
        except Exception as exc:
            logger.warning(
                "indication_competition service failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"competition_evidence": [], "failed_sources": ["indication_competition"]}
        await _persist_evidence(ev, "indication_competition")
        logger.info("[node] indication_competition: %d items", len(ev))
        return {"competition_evidence": ev}

    # ── Processing nodes ────────────────────────────────────────────────────

    async def screening_first_node(state: PipelineState) -> dict[str, Any]:
        from core.evidence_text import is_target_drug_trial

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        all_ev = _with_target_drug_trials(_all_raw_evidence(state), state)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        logger.info("[node] screening_first: %d total evidence items to screen", len(all_ev))

        if not force and model_fp:
            cache_hits: list[Evidence] = []
            cache_misses: list[Evidence] = []
            for ev in all_ev:
                # A trial of the target's drug is kept by rule (never cached): a verdict an
                # LLM gave it earlier must not stand in for the rule.
                if is_target_drug_trial(ev):
                    cache_misses.append(ev)
                    continue
                ck = source_fingerprint(gene, disease, direction, ev.evidence_type.value, ev.source)
                cached_verdict = await _llm_cache_get(ck, model_fp)
                if cached_verdict is not None and is_reusable_cached_verdict(cached_verdict):
                    cache_hits.append(
                        ev.model_copy(
                            update={"extra": {**ev.extra, "screening_verdict": cached_verdict}}
                        )
                    )
                else:
                    cache_misses.append(ev)
            logger.info(
                "[node] screening_first: %d cache hits, %d misses",
                len(cache_hits),
                len(cache_misses),
            )
            if not cache_misses:
                kept = sum(
                    1
                    for e in cache_hits
                    if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
                )
                logger.info(
                    "[node] screening_first: %d keep of %d (all from cache)", kept, len(cache_hits)
                )
                return {"screened_evidence": cache_hits, "messages": []}
            # Screen only the misses, then write new verdicts to cache
            msg = _task_msg(
                state,
                "screening",
                {"target_gene": gene, "disease": disease, "pass_type": "first"},
                payload=cache_misses,
            )
            result = await ScreeningAgent().run(msg, _c(state))
            freshly_screened = _evidences(result)
            for ev in freshly_screened:
                verdict = ev.extra.get("screening_verdict")
                if verdict and not verdict.get("deterministic"):
                    ck = source_fingerprint(
                        gene, disease, direction, ev.evidence_type.value, ev.source
                    )
                    await _llm_cache_set(ck, model_fp, "screening", verdict)
            # Re-derive FAERS verdicts across the merge: a bundle screened fresh here
            # may have its parent label among the cache hits.
            all_screened = inherit_faers_verdicts(cache_hits + freshly_screened)
            kept = sum(
                1
                for e in all_screened
                if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
            )
            logger.info(
                "[node] screening_first: %d screened — %d keep / %d drop+uncertain",
                len(all_screened),
                kept,
                len(all_screened) - kept,
            )
            return {"screened_evidence": all_screened, "messages": [result]}

        # No cache (force_refresh or no model fingerprint) — original path
        msg = _task_msg(
            state,
            "screening",
            {
                "target_gene": gene,
                "disease": disease,
                "pass_type": "first",
            },
            payload=all_ev,
        )
        result = await ScreeningAgent().run(msg, _c(state))
        screened_ev = _evidences(result)
        if model_fp:
            for e in screened_ev:
                verdict = e.extra.get("screening_verdict")
                if verdict and not verdict.get("deterministic"):
                    ck = source_fingerprint(
                        gene, disease, direction, e.evidence_type.value, e.source
                    )
                    await _llm_cache_set(ck, model_fp, "screening", verdict)
        kept = sum(
            1 for e in screened_ev if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        )
        logger.info(
            "[node] screening_first: %d screened — %d keep / %d drop+uncertain",
            len(screened_ev),
            kept,
            len(screened_ev) - kept,
        )
        return {"screened_evidence": screened_ev, "messages": [result]}

    async def knowledge_extraction_node(state: PipelineState) -> dict[str, Any]:
        deduped = _dedup_screened(state)
        logger.info("[node] knowledge_extraction: %d items", len(deduped))
        msg = _task_msg(
            state,
            "knowledge_extraction",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
            },
            payload=deduped,
        )
        result = await KnowledgeExtractionAgent().run(msg, _c(state))
        return {"screened_evidence": _evidences(result), "messages": [result]}

    async def screening_second_node(state: PipelineState) -> dict[str, Any]:
        """Re-judge the items pass 1 left uncertain that have PMC Open Access full text.

        Verdicts are cached under the second-pass key, and only a decisive one (keep or
        drop) is reused or written: a pass-1 ``uncertain`` must never stand in for the
        full-text judgement. Uncertain items without full text stay uncertain.
        """
        model_fp = state.get("model_fingerprint", "")
        use_cache = bool(model_fp) and not state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        # Fetch full text for uncertain items so pass 2 re-judges on real content,
        # not the same abstract pass 1 already saw.
        deduped = await _enrich_uncertain_with_full_text(
            _dedup_screened(state), force=state.get("force_refresh", False)
        )
        decided = [e for e in deduped if _verdict_of(e) != "uncertain"]
        uncertain = [e for e in deduped if _verdict_of(e) == "uncertain"]
        to_judge = [e for e in uncertain if e.scope == "full_text"]
        stay = [e for e in uncertain if e.scope != "full_text"]
        logger.info(
            "[node] screening_second: %d items (%d uncertain, %d with full text to re-screen)",
            len(deduped),
            len(uncertain),
            len(to_judge),
        )

        def _key(ev: Evidence) -> str:
            return source_fingerprint(
                gene, disease, direction, ev.evidence_type.value, ev.source, screening_pass="second"
            )

        cache_hits: list[Evidence] = []
        misses = to_judge
        if use_cache:
            misses = []
            for ev in to_judge:
                cached = await _llm_cache_get(_key(ev), model_fp)
                if (
                    cached is not None
                    and cached.get("verdict") in _DECISIVE_VERDICTS
                    and is_reusable_cached_verdict(cached)
                ):
                    cache_hits.append(
                        ev.model_copy(update={"extra": {**ev.extra, "screening_verdict": cached}})
                    )
                else:
                    misses.append(ev)

        fresh: list[Evidence] = []
        messages: list[AgentMessage] = []
        if misses:
            msg = _task_msg(
                state,
                "screening",
                {
                    "target_gene": gene,
                    "disease": disease,
                    "pass_type": "second",
                    # Names the full-text excerpt looks for (screening/excerpt.py).
                    "target_names": [
                        gene,
                        *((state.get("resolved_context") or {}).get("gene_aliases") or []),
                    ],
                    "drug_names": sorted(
                        {
                            str(d.get("drug_name"))
                            for d in _ot_extra(state).get("known_drugs") or []
                            if isinstance(d, dict) and d.get("drug_name")
                        }
                    ),
                    "disease_scope": (await _text_scope(state)).model_dump(mode="json"),
                },
                payload=misses,
            )
            result = await ScreeningAgent().run(msg, _c(state))
            fresh = _evidences(result)
            messages = [result]
            if model_fp:
                for ev in fresh:
                    verdict = ev.extra.get("screening_verdict") or {}
                    if verdict.get("verdict") in _DECISIVE_VERDICTS and not verdict.get(
                        "deterministic"
                    ):
                        await _llm_cache_set(_key(ev), model_fp, "screening", verdict)

        all_screened = decided + stay + cache_hits + fresh
        rejudged = cache_hits + fresh
        logger.info(
            "[node] screening_second: %d re-judged (%d from cache) — %d keep, %d drop, "
            "%d still uncertain; final %d keep of %d",
            len(rejudged),
            len(cache_hits),
            sum(1 for e in rejudged if _verdict_of(e) == "keep"),
            sum(1 for e in rejudged if _verdict_of(e) == "drop"),
            sum(1 for e in rejudged if _verdict_of(e) == "uncertain"),
            sum(1 for e in all_screened if _verdict_of(e) == "keep"),
            len(all_screened),
        )
        return {"screened_evidence": all_screened, "messages": messages}

    async def claim_extraction_node(state: PipelineState) -> dict[str, Any]:
        """Extract atomic CoreClaims from screened evidence (pre-HITL).

        Runs on screened+deduplicated evidence so lenses receive typed claims,
        not raw documents. Empty screened set is a no-op.
        """
        keep_evidence = [
            e
            for e in _dedup_screened(state)
            if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]
        if not keep_evidence:
            logger.info("[node] claim_extraction: no keep-evidence, skipping")
            return {"extracted_claims": []}
        logger.info(
            "[node] claim_extraction: extracting from %d evidence items", len(keep_evidence)
        )
        from services.evidence.claim_effect import target_drug_effects
        from services.evidence.claim_extraction import DroppedClaim

        dropped: list[DroppedClaim] = []
        statements: list[TargetStatement] = []
        try:
            papers = await _ground_literature(state, keep_evidence)
            claims = await extract_claims(
                keep_evidence,
                target_gene=state["target_gene"],
                disease=state["disease"],
                ctx=_c(state),
                gene_aliases=list((state.get("resolved_context") or {}).get("gene_aliases") or []),
                target_drugs=target_drug_effects(_ot_extra(state).get("known_drugs") or []),
                papers=papers,
                dropped=dropped,
            )
            statements = await _read_statements(
                state,
                papers,
                claims,
                keep_evidence,
                _c(state),
                _task_fp(state, "claim_extraction"),
            )
            claims = _apply_statements(claims, papers, statements)
        except Exception as exc:
            logger.warning(
                "claim_extraction failed, continuing without: %s",
                describe_exception(exc),
                exc_info=True,
            )
            return {"extracted_claims": []}
        logger.info(
            "[node] claim_extraction: %d claims extracted, %d left out",
            len(claims),
            len(dropped),
        )
        return {
            "extracted_claims": claims,
            "dropped_claims": [d.model_dump() for d in dropped],
            "target_statements": [st.model_dump(mode="json") for st in statements],
        }

    async def source_quality_node(state: PipelineState) -> dict[str, Any]:
        """Score each kept Evidence's source quality, once, before the lenses run."""
        from services.evidence.quality_cache import from_cache, to_cache

        model_fp = _task_fp(state, "source_quality")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        keep_evidence = [
            e
            for e in _dedup_screened(state)
            if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]
        if not keep_evidence:
            logger.info("[node] source_quality: no keep-evidence, skipping")
            return {"source_quality": {}}
        # A resumed run carries the scores of the run it continues (CLEAR_FROM_NODE clears
        # them only when screening is re-run); rescoring the same sources would repeat
        # every journal lookup against external quotas.
        carried = state.get("source_quality") or {}
        if carried and {str(e.evidence_id) for e in keep_evidence} <= set(carried):
            logger.info("[node] source_quality: reusing %d scores carried in state", len(carried))
            _log_quality_coverage(keep_evidence, carried)
            return {"source_quality": carried}
        use_cache = bool(model_fp) and not force
        ck = source_quality_fingerprint(gene, disease, direction)
        cached = await _llm_cache_get(ck, model_fp) if use_cache else None
        # The cache stores entries by source and is re-keyed onto this run's evidence
        # UUIDs; only documents it does not cover are scored.
        reused, missing = from_cache(cached, keep_evidence)
        if not missing:
            logger.info("[node] source_quality: cache HIT (%d sources)", len(reused))
            _log_quality_coverage(keep_evidence, reused)
            return {"source_quality": reused}
        msg = _task_msg(
            state,
            "source_quality",
            {"target_gene": gene, "disease": disease},
            payload=missing,
        )
        result = await SourceQualityAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        quality_map = {**reused, **payload.get("source_quality", {})}
        if quality_map and use_cache:
            await _llm_cache_set(
                ck, model_fp, "source_quality", to_cache(quality_map, keep_evidence, cached)
            )
        logger.info(
            "[node] source_quality: %d sources scored, %d reused from the cache",
            len(quality_map) - len(reused),
            len(reused),
        )
        _log_quality_coverage(keep_evidence, quality_map)
        return {"source_quality": quality_map, "messages": [result]}

    async def hitl_gate_node(state: PipelineState) -> dict[str, Any]:
        kept = _keep_evidence(state)
        if not state.get("hitl_approved", False):
            logger.info(
                "[node] hitl_gate: pausing — %d keep-evidence items awaiting human review",
                len(kept),
            )
            interrupt(
                {
                    "screened_evidence_count": len(_dedup_screened(state)),
                    "awaiting": "human review of screened evidence",
                }
            )
        logger.info(
            "[node] hitl_gate: approved — %d evidence items proceeding to reasoning", len(kept)
        )
        # Overrides are applied by downstream nodes. The programme reading is computed here,
        # once, so the clinical and safety lenses, which run in parallel, both read it.
        classes = await _resolve_disease_classes(
            state,
            _genetics_floor_signals(
                kept, await _disease_scope(state), await _disease_ancestors(state)
            ),
        )
        return {"programme_reading": await _programme_reading(state, _c(state), classes)}

    # ── Reasoning nodes — lens architecture ──────────────────────────────────

    async def genetics_lens_node(state: PipelineState) -> dict[str, Any]:
        from schemas.verdicts import LensVerdict

        model_fp = _task_fp(state, "genetics_lens")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        keep_ev = _keep_evidence(state)
        floor_signals = _genetics_floor_signals(
            keep_ev, await _disease_scope(state), await _disease_ancestors(state)
        )
        direction_reading = await _direction_reading(state, floor_signals)
        curated = _curated_mechanism(direction_reading)
        if curated is not None:
            # The disease's curated mechanism replaces the one inferred from the gene's
            # whole variant spectrum, which pools every disorder of the gene.
            floor_signals = {
                **floor_signals,
                "mechanism_direction": curated,
                "mechanism_curated_in_scope": True,
            }
        disease_classes = await _resolve_disease_classes(state, floor_signals)
        spec: dict[str, Any] = {
            "target_gene": gene,
            "disease": disease,
            "gene_id": state.get("gene_id") or "",
            "disease_id": state.get("disease_id") or "",
            "extracted_claims": [
                c.model_dump(mode="json") for c in state.get("extracted_claims", [])
            ],
            "source_quality": state.get("source_quality", {}),
            "literature_index": await _literature_index(state),
            "decisive_claim_ids": await _decisive_claim_ids(state),
            "source_evidence_text": _genetics_source_evidence_text(keep_ev),
            "floor_signals": floor_signals,
            "disease_classes": disease_classes,
            "drug_precedent": await _drug_precedent(state),
            "missing_inputs": _missing_inputs(state, "genetics"),
            "direction_reading": direction_reading.model_dump(mode="json"),
            "direction_reading_text": direction_reading.restricted_to(HUMAN_GENETICS).render(),
            "genetic_direction": _genetic_direction(direction_reading),
            "causality_reading": await _causality_reading(
                state,
                keep_ev,
                floor_signals,
                direction_reading,
                is_oncology=DiseaseClass.ONCOLOGY.value in disease_classes,
            ),
        }
        ck = _lens_cache_key(gene, disease, direction, "genetics", spec)
        if not force and model_fp:
            cached = await _lens_cache_get(ck, "genetics", model_fp)
            if cached is not None:
                logger.info("[node] genetics_lens: cache HIT")
                return {"lens_verdicts": [cached], "messages": []}
        msg = _task_msg(state, "genetics_lens", spec)
        try:
            result = await GeneticsLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("genetics_lens failed: %s", describe_exception(exc), exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["genetics"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=keep_ev,
                claims=list(state.get("extracted_claims", [])),
                quality_map=state.get("source_quality", {}),
                provenance=collect_provenance(router),
            )
        logger.info("[node] genetics_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def biology_lens_node(state: PipelineState) -> dict[str, Any]:
        from schemas.verdicts import LensVerdict

        model_fp = _task_fp(state, "biology_lens")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        ot = _ot_extra(state)
        dm = _depmap_extra(state)
        disease_id = state.get("disease_id") or ""
        keep_ev_bio = _keep_evidence(state)
        floor_signals_bio = _genetics_floor_signals(
            keep_ev_bio, await _disease_scope(state), await _disease_ancestors(state)
        )
        disease_classes_bio = await _resolve_disease_classes(state, floor_signals_bio)
        is_oncology = DiseaseClass.ONCOLOGY.value in disease_classes_bio
        tissue_ctx = await _disease_tissue_context(state, keep_ev_bio)
        direction_bio = await _direction_reading(state, floor_signals_bio)
        spec: dict[str, Any] = {
            "target_gene": gene,
            "disease": disease,
            "gene_id": state.get("gene_id") or "",
            "disease_id": disease_id,
            "extracted_claims": [
                c.model_dump(mode="json") for c in state.get("extracted_claims", [])
            ],
            "source_quality": state.get("source_quality", {}),
            "literature_index": await _literature_index(state),
            "decisive_claim_ids": await _decisive_claim_ids(state),
            "ot_tractability_text": ot.get("tract_text", ""),
            "ot_mouse_phenotype_count": ot.get("mouse_phenotype_count", 0),
            "ot_mouse_phenotype_labels": ot.get("mouse_phenotype_labels", []),
            "ot_mouse_text": ot.get("mouse_text", ""),
            "depmap_text": dm.get("text", ""),
            "depmap_mean_chronos": dm.get("gene_effect_mean"),
            "depmap_std_chronos": dm.get("gene_effect_std"),
            "depmap_dependency_fraction": dm.get("dependency_fraction"),
            "depmap_is_common_essential": dm.get("is_common_essential", False),
            "depmap_is_strongly_selective": dm.get("is_strongly_selective", False),
            "depmap_selective_lineages": dm.get("selective_lineages", []),
            "depmap_lineage_breakdown": dm.get("lineage_breakdown", []),
            "dependency_reading": _dependency_reading_json(state),
            "is_oncology_indication": is_oncology,
            "disease_classes": disease_classes_bio,
            "omics_expression_text": _biology_expression_summary(keep_ev_bio),
            "regulatory_element_text": _biology_regulatory_element_summary(keep_ev_bio),
            "bulk_tpm": tissue_ctx["bulk_tpm"],
            "hpa_specificity": tissue_ctx["hpa_specificity"],
            "disease_tissue": tissue_ctx["disease_tissue"],
            "disease_tissue_expression_note": tissue_ctx["disease_tissue_expression_note"],
            "top_tpm_tissues": tissue_ctx["top_tpm_tissues"],
            "gtex_tissue_ranks": tissue_ctx["gtex_tissue_ranks"],
            "disease_relevant_tissues": tissue_ctx["disease_relevant_tissues"],
            "disease_cell_types": tissue_ctx["disease_cell_types"],
            "secreted": tissue_ctx["secreted"],
            "missing_inputs": _missing_inputs(state, "biology"),
            "dependency_profile": await _dependency_profile(state, is_oncology=is_oncology),
            "precedent_status": (await _drug_precedent(state)).get("status", ""),
            "direction_reading_text": direction_bio.render(),
            "genetic_direction": _genetic_direction(direction_bio),
            # Context for the prompt only: no biology axis is scored from it.
            "direction_verdict": direction_bio.verdict,
            "perturbation_reading": await _perturbation_reading(state),
            "known_drugs": [
                {k: d.get(k) for k in ("drug_name", "drug_type", "is_approved", "max_phase")}
                for d in ot.get("known_drugs") or []
                if isinstance(d, dict)
            ],
            "ot_tractability": {
                "available": bool(ot.get("tract_text")),
                "small_molecule": bool(ot.get("tractability_small_molecule")),
                "antibody": bool(ot.get("tractability_antibody")),
                "other": list(ot.get("tractability_other") or []),
            },
        }
        ck = _lens_cache_key(gene, disease, direction, "biology", spec)
        if not force and model_fp:
            cached = await _lens_cache_get(ck, "biology", model_fp)
            if cached is not None:
                logger.info("[node] biology_lens: cache HIT")
                return {"lens_verdicts": [cached], "messages": []}
        msg = _task_msg(state, "biology_lens", spec)
        try:
            result = await BiologyLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("biology_lens failed: %s", describe_exception(exc), exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["biology"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                ot_extra=_ot_extra(state),
                quality_map=state.get("source_quality", {}),
                provenance=collect_provenance(router),
            )
        logger.info("[node] biology_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def safety_lens_node(state: PipelineState) -> dict[str, Any]:
        from schemas.verdicts import LensVerdict

        model_fp = _task_fp(state, "safety_lens")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        ot = _ot_extra(state)
        keep_ev_safety = _keep_evidence(state)
        floor_signals = _genetics_floor_signals(
            keep_ev_safety, await _disease_scope(state), await _disease_ancestors(state)
        )
        disease_classes_safety = await _resolve_disease_classes(state, floor_signals)
        tissue_ctx = await _disease_tissue_context(state, keep_ev_safety)
        direction_reading = await _direction_reading(state, floor_signals)
        liabilities, _, liability_assessed = await _liability_split(state, floor_signals)
        spec: dict[str, Any] = {
            "target_gene": gene,
            "disease": disease,
            "gene_id": state.get("gene_id") or "",
            "disease_id": state.get("disease_id") or "",
            "extracted_claims": [
                c.model_dump(mode="json") for c in state.get("extracted_claims", [])
            ],
            "source_quality": state.get("source_quality", {}),
            "literature_index": await _literature_index(state),
            "decisive_claim_ids": await _decisive_claim_ids(state),
            # Toxicities of acting on the target only; pharmacogenomic rows left out
            # (services/evidence/liabilities.target_safety).
            "ot_safety_liability_count": target_safety(ot)["count"],
            "ot_safety_liability_events": target_safety(ot)["events"],
            "ot_safety_text": target_safety(ot)["text"],
            "ot_mouse_text": ot.get("mouse_text", ""),
            "ot_mouse_phenotype_labels": ot.get("mouse_phenotype_labels", []),
            "safety_structured_text": _safety_structured_summary(keep_ev_safety),
            "faers_text": _faers_safety_summary(keep_ev_safety),
            "constraint_reading": floor_signals.get("constraint_reading") or {},
            "mechanism_direction": floor_signals.get("mechanism_direction"),
            "dependency_reading": _dependency_reading_json(state),
            "bulk_tpm": tissue_ctx["bulk_tpm"],
            "hpa_specificity": tissue_ctx["hpa_specificity"],
            "disease_tissue": tissue_ctx["disease_tissue"],
            "disease_tissue_expression_note": tissue_ctx["disease_tissue_expression_note"],
            "top_tpm_tissues": tissue_ctx["top_tpm_tissues"],
            "gtex_tissue_ranks": tissue_ctx["gtex_tissue_ranks"],
            "disease_relevant_tissues": tissue_ctx["disease_relevant_tissues"],
            "disease_classes": disease_classes_safety,
            "disease_cell_types": tissue_ctx["disease_cell_types"],
            "secreted": tissue_ctx["secreted"],
            "candidate_liabilities": liabilities,
            # Whether data on the requested change were examined, whatever they showed, or
            # people were given same-direction drugs (absence is not safety).
            "liability_assessed": liability_assessed or _human_exposure(state),
            # The counted mouse finding's labels: the omission guard asks the prose to name
            # their systems, and on an activator run knockout phenotypes are not counted.
            "liability_mouse_phenotypes": (
                list(ot.get("mouse_phenotype_labels") or [])
                if any(
                    str(li.get("system") or "").startswith(MOUSE_PHENOTYPES) for li in liabilities
                )
                else []
            ),
            "drug_precedent": await _drug_precedent(state),
            "approved_label_warnings": await _approved_label_warnings(state),
            "safety_stops_elsewhere": (await _safety_stops(state))[1],
            "missing_inputs": _missing_inputs(state, "safety"),
            "dependency_profile": await _dependency_profile(
                state, is_oncology=DiseaseClass.ONCOLOGY.value in disease_classes_safety
            ),
            "human_exposure": _human_exposure(state),
            "direction_reading_text": direction_reading.render(),
            "genetic_direction": _genetic_direction(direction_reading),
        }
        ck = _lens_cache_key(
            gene, disease, direction, "safety", spec, extra=verdict_digest(), skill=NARRATIVE_SKILL
        )
        if not force and model_fp:
            cached = await _lens_cache_get(ck, "safety", model_fp)
            if cached is not None:
                logger.info("[node] safety_lens: cache HIT")
                return {"lens_verdicts": [cached], "messages": []}
        msg = _task_msg(state, "safety_lens", spec)
        try:
            result = await SafetyLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("safety_lens failed: %s", describe_exception(exc), exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["safety"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                ot_extra=_ot_extra(state),
                quality_map=state.get("source_quality", {}),
                provenance=collect_provenance(router),
            )
        logger.info("[node] safety_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def clinical_lens_node(state: PipelineState) -> dict[str, Any]:
        from schemas.verdicts import LensVerdict

        model_fp = _task_fp(state, "clinical_lens")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        keep_ev_clinical = _keep_evidence(state)
        disease_classes_clinical = await _resolve_disease_classes(
            state,
            _genetics_floor_signals(
                keep_ev_clinical, await _disease_scope(state), await _disease_ancestors(state)
            ),
        )
        programme_reading = state.get("programme_reading") or await _programme_reading(
            state, _c(state), disease_classes_clinical
        )
        spec: dict[str, Any] = {
            "target_gene": gene,
            "disease": disease,
            "gene_id": state.get("gene_id") or "",
            "disease_id": state.get("disease_id") or "",
            "extracted_claims": [
                c.model_dump(mode="json") for c in state.get("extracted_claims", [])
            ],
            "source_quality": state.get("source_quality", {}),
            "literature_index": await _literature_index(state),
            "decisive_claim_ids": await _decisive_claim_ids(state),
            "disease_classes": disease_classes_clinical,
            "trial_facts": _trial_facts_json(keep_ev_clinical, disease=disease),
            "drug_precedent": await _drug_precedent(state),
            "programme_reading": programme_reading,
        }
        ck = _lens_cache_key(gene, disease, direction, "clinical", spec)
        if not force and model_fp:
            cached = await _lens_cache_get(ck, "clinical", model_fp)
            if cached is not None:
                logger.info("[node] clinical_lens: cache HIT")
                return {
                    "lens_verdicts": [cached],
                    "programme_reading": programme_reading,
                    "messages": [],
                }
        msg = _task_msg(state, "clinical_lens", spec)
        try:
            result = await ClinicalLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("clinical_lens failed: %s", describe_exception(exc), exc_info=True)
            return {
                "lens_verdicts": [],
                "failed_lenses": ["clinical"],
                "programme_reading": programme_reading,
                "messages": [],
            }
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                quality_map=state.get("source_quality", {}),
                provenance=collect_provenance(router),
            )
        logger.info("[node] clinical_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {
            "lens_verdicts": verdicts,
            "programme_reading": programme_reading,
            "messages": [result],
        }

    async def commercial_lens_node(state: PipelineState) -> dict[str, Any]:
        from schemas.verdicts import LensVerdict

        model_fp = _task_fp(state, "commercial_lens")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        ot = _ot_extra(state)
        competition = _competition_extra(state)
        competition_claims = _competition_claims(state)
        keep_ev_commercial = _keep_evidence(state)
        disease_classes_commercial = await _resolve_disease_classes(
            state,
            _genetics_floor_signals(
                keep_ev_commercial, await _disease_scope(state), await _disease_ancestors(state)
            ),
        )
        target_patents, indication_patents = _patent_counts(
            list(state.get("patent_evidence", [])), gene
        )
        orphanet_text, orphanet_indication = orphanet_prevalence_summary(
            keep_ev_commercial, disease
        )
        spec: dict[str, Any] = {
            "target_gene": gene,
            "disease": disease,
            "gene_id": state.get("gene_id") or "",
            "disease_id": state.get("disease_id") or "",
            "extracted_claims": [
                c.model_dump(mode="json")
                for c in [*state.get("extracted_claims", []), *competition_claims]
            ],
            "source_quality": state.get("source_quality", {}),
            "decisive_claim_ids": await _decisive_claim_ids(state),
            "patent_count": target_patents,
            "indication_patent_count": indication_patents,
            "source_groups": _patent_family_groups(keep_ev_commercial),
            "target_programmes": _target_programme_lines(state, await _disease_scope(state)),
            "trial_count": len(list(state.get("trial_evidence", []))),
            "ot_known_drugs_count": ot.get("known_drugs_count", 0),
            "ot_known_drugs_approved_count": ot.get("known_drugs_approved_count", 0),
            "ot_known_drugs_phase3_count": ot.get("known_drugs_phase3_count", 0),
            "ot_known_drugs_text": ot.get("known_drugs_text", ""),
            "ot_known_drugs": ot.get("known_drugs", []),
            "fda_label_text": _fda_label_summary(
                keep_ev_commercial,
                direction=direction,
                scope=await _text_scope(state),
                **await _disease_name_sets(state),
            ),
            "gbd_prevalence_text": _gbd_prevalence_summary(keep_ev_commercial),
            "orphanet_prevalence_text": orphanet_text,
            "indication_prevalence_found": bool(
                orphanet_indication or _gbd_prevalence_summary(keep_ev_commercial)
            ),
            "disease_classes": disease_classes_commercial,
            "indication_competition_text": _indication_competition_summary(state),
            "indication_approved_drug_count": competition.get("approved_drug_count", 0),
            "indication_active_trial_count": competition.get("active_trial_count", 0),
            "indication_phase3_trial_count": competition.get("phase3_trial_count", 0),
            "indication_total_trial_count": competition.get("total_trial_count", 0),
            "indication_landscape_known": bool(competition),
            "precedent_status": (await _drug_precedent(state)).get("status", ""),
            "programme_reading": state.get("programme_reading") or await _programme_reading(state),
        }
        ck = _lens_cache_key(gene, disease, direction, "commercial", spec)
        if not force and model_fp:
            cached = await _lens_cache_get(ck, "commercial", model_fp)
            if cached is not None:
                logger.info("[node] commercial_lens: cache HIT")
                return {"lens_verdicts": [cached], "messages": []}
        msg = _task_msg(state, "commercial_lens", spec)
        try:
            result = await CommercialLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("commercial_lens failed: %s", describe_exception(exc), exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["commercial"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=[*_keep_evidence(state), *state.get("competition_evidence", [])],
                claims=[*state.get("extracted_claims", []), *competition_claims],
                ot_extra=_ot_extra(state),
                quality_map=state.get("source_quality", {}),
                provenance=collect_provenance(router),
            )
        logger.info(
            "[node] commercial_lens: %s", verdicts[0].overall_verdict if verdicts else "none"
        )
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def regulatory_lens_node(state: PipelineState) -> dict[str, Any]:
        from schemas.verdicts import LensVerdict

        model_fp = _task_fp(state, "regulatory_lens")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        keep_ev_regulatory = _keep_evidence(state)
        disease_classes_regulatory = await _resolve_disease_classes(
            state,
            _genetics_floor_signals(
                keep_ev_regulatory, await _disease_scope(state), await _disease_ancestors(state)
            ),
        )
        # FAERS is the safety lens's input (skills/regulatory_lens.md): this lens reads labels.
        faers_ids = {
            ev.evidence_id
            for ev in keep_ev_regulatory
            if ev.evidence_type == EvidenceType.REGULATORY
            and ev.source.startswith(FDA_FAERS_PREFIX)
        }
        regulatory_claims = [
            c for c in state.get("extracted_claims", []) if c.source_evidence_id not in faers_ids
        ]
        spec: dict[str, Any] = {
            "target_gene": gene,
            "disease": disease,
            "gene_id": state.get("gene_id") or "",
            "disease_id": state.get("disease_id") or "",
            "extracted_claims": [c.model_dump(mode="json") for c in regulatory_claims],
            "source_quality": state.get("source_quality", {}),
            "decisive_claim_ids": await _decisive_claim_ids(state),
            "fda_label_text": _fda_label_summary(
                keep_ev_regulatory,
                direction=direction,
                scope=await _text_scope(state),
                **await _disease_name_sets(state),
            ),
            "disease_classes": disease_classes_regulatory,
            "drug_precedent": await _drug_precedent(state),
        }
        ck = _lens_cache_key(gene, disease, direction, "regulatory", spec)
        if not force and model_fp:
            cached = await _lens_cache_get(ck, "regulatory", model_fp)
            if cached is not None:
                logger.info("[node] regulatory_lens: cache HIT")
                return {"lens_verdicts": [cached], "messages": []}
        msg = _task_msg(state, "regulatory_lens", spec)
        try:
            result = await RegulatoryLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("regulatory_lens failed: %s", describe_exception(exc), exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["regulatory"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=[ev for ev in keep_ev_regulatory if ev.evidence_id not in faers_ids],
                claims=regulatory_claims,
                quality_map=state.get("source_quality", {}),
                provenance=collect_provenance(router),
            )
        logger.info(
            "[node] regulatory_lens: %s", verdicts[0].overall_verdict if verdicts else "none"
        )
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def experiment_node(state: PipelineState) -> dict[str, Any]:
        floor_signals_exp = _genetics_floor_signals(
            _keep_evidence(state), await _disease_scope(state), await _disease_ancestors(state)
        )
        # Send only condensed summaries to keep the scoring prompt focused.
        lens_summaries = [
            {
                "lens": lv.lens,
                "overall_verdict": lv.overall_verdict,
                "confidence": lv.confidence,
                "rationale": lv.rationale,
                "narrative": lv.narrative,
            }
            for lv in state.get("lens_verdicts", [])
        ]
        liabilities = await _candidate_liabilities(state, floor_signals_exp)
        msg = _task_msg(
            state,
            "experiment",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "lens_summaries": lens_summaries,
                "genetics_floor_signals": floor_signals_exp,
                "candidate_liabilities": liabilities,
                "liability_statuses": _liability_statuses(state, len(liabilities)),
                "human_exposure": _human_exposure(state),
                "precedent_status": (await _drug_precedent(state)).get("status", ""),
                "direction_reading": (
                    await _direction_reading(state, floor_signals_exp)
                ).model_dump(mode="json"),
                "programme_strength": await _programme_strength(state),
            },
            payload=_keep_evidence(state),
        )
        result = await ExperimentAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"experiment_results": payload.get("experiment_results", []), "messages": [result]}

    async def critic_node(state: PipelineState) -> dict[str, Any]:
        extracted = [c.model_dump(mode="json") for c in state.get("extracted_claims", [])]
        lens_v = [lv.model_dump(mode="json") for lv in state.get("lens_verdicts", [])]
        msg = _task_msg(
            state,
            "critic",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "extracted_claims": extracted,
                "lens_verdicts": lens_v,
                "source_quality": state.get("source_quality", {}),
            },
            payload=_dedup_screened(state),
        )
        result = await CriticAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"critiques": payload.get("critiques", []), "messages": [result]}

    async def reconciler_node(state: PipelineState) -> dict[str, Any]:
        lens_verdicts = state.get("lens_verdicts", [])

        try:
            am = reconcile(list(lens_verdicts), run_id=state["run_id"])
            logger.info(
                "[node] reconciler: consensus=%s, conflicts=%d",
                am.consensus_verdict,
                len(am.conflicts),
            )
            return {"agreement_map": am.model_dump(mode="json")}
        except Exception as exc:
            logger.warning("reconciler failed: %s", describe_exception(exc), exc_info=True)
            return {"agreement_map": None}

    async def reviewer_node(state: PipelineState) -> dict[str, Any]:
        msg = _task_msg(
            state,
            "reviewer",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "stage_facts": _review_stage_facts(state),
            },
        )
        result = await ReviewerAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"review_gaps": payload.get("review_gaps", []), "messages": [result]}

    async def investigator_node(state: PipelineState) -> dict[str, Any]:
        # Guard-flag entries ("… lens: open check(s) …") are prose issues the lenses
        # already annotate, not evidence the investigator can fetch; with nothing else
        # to resolve it wandered off-target (PCSK9: omega-3 and psyllium trials).
        evidence_gaps = [
            a
            for g in state.get("review_gaps", []) or []
            for a in g.get("missing_aspects", []) or []
            if "open check" not in a
        ]
        conflicts = list((state.get("agreement_map") or {}).get("conflicts", []) or [])
        if not evidence_gaps and not conflicts:
            logger.info("[node] investigator: skipped — no evidence gaps or lens conflicts")
            return {
                "investigation_summary": "",
                "investigation_tools_used": [],
                "investigation_error": "",
            }
        lens_summary = "\n".join(
            f"- {lv.lens}: {lv.overall_verdict} (confidence={lv.confidence:.2f}) — {lv.rationale}"
            for lv in state.get("lens_verdicts", [])
        )
        msg = _task_msg(
            state,
            "investigator",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "direction": state.get("direction") or "unspecified",
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "trial_facts": _trial_facts_json(_keep_evidence(state), disease=state["disease"]),
                "ot_known_drugs": _ot_extra(state).get("known_drugs", []),
                "drug_precedent": await _drug_precedent(state),
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "review_gaps": state.get("review_gaps", []),
                "agreement_map": state.get("agreement_map"),
                "lens_summary": lens_summary,
                "gwas_traits_in_scope": _genetics_floor_signals(
                    _keep_evidence(state), await _disease_scope(state)
                )["gwas_traits_in_scope"],
            },
        )
        try:
            result = await InvestigatorAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("investigator failed: %s", describe_exception(exc), exc_info=True)
            return {
                "investigation_summary": "",
                "investigation_tools_used": [],
                "investigation_error": describe_exception(exc),
            }
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {
            "investigation_summary": payload.get("investigation_summary", ""),
            "investigation_tools_used": payload.get("tools_used", []),
            "investigation_error": payload.get("error", ""),
            "messages": [result],
        }

    async def report_node(state: PipelineState) -> dict[str, Any]:
        screened = _dedup_screened(state)
        # A row taken from the evidence cache belongs to the run that fetched it; writing
        # this run's verdict onto it would replace that run's. The report reads this run's
        # verdicts from the summary below instead.
        run_id = str(state["run_id"])
        await _persist_evidence(
            [ev for ev in screened if str(ev.run_id) == run_id], "report:screened-verdicts"
        )
        evidence_summary = [
            {
                "evidence_id": str(ev.evidence_id),
                "source": ev.source,
                "evidence_type": ev.evidence_type.value,
                "verdict": ev.extra.get("screening_verdict", {}).get("verdict", ""),
                "screening_rationale": ev.extra.get("screening_verdict", {}).get("rationale", ""),
            }
            for ev in screened
        ]
        counted_liabilities, opposite_liabilities, liability_assessed = await _liability_split(
            state,
            _genetics_floor_signals(
                _keep_evidence(state), await _disease_scope(state), await _disease_ancestors(state)
            ),
        )
        report_payload = {
            "lens_verdicts": [lv.model_dump(mode="json") for lv in state.get("lens_verdicts", [])],
            "agreement_map": state.get("agreement_map"),
            "experiment_results": state.get("experiment_results", []),
            "critiques": state.get("critiques", []),
            "review_gaps": state.get("review_gaps", []),
            "evidence_summary": evidence_summary,
            "investigation_summary": state.get("investigation_summary", ""),
            "investigation_error": state.get("investigation_error", ""),
            "candidate_liabilities": counted_liabilities,
            "opposite_liabilities": opposite_liabilities,
            "liability_assessed": liability_assessed or _human_exposure(state),
            "drug_precedent": await _drug_precedent(state),
            "programme_reading": state.get("programme_reading") or await _programme_reading(state),
            "disease_scope": (await _text_scope(state)).model_dump(mode="json"),
        }
        # Everything the run went without, including what failed while this node gathered
        # the payload above.
        report_payload["dropped_claims"] = state.get("dropped_claims") or []
        report_payload["degradations"] = merge_degradations(
            state.get("degradations"), [{**d, "node": "report"} for d in recorded()]
        )
        msg = _task_msg(
            state,
            "report",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
            },
            payload=report_payload,
        )
        result = await ReportAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        artifact_uri = payload.get("artifact_uri")
        full_report_uri = payload.get("full_report_uri")
        return {
            "report_uri": artifact_uri,
            "full_report_uri": full_report_uri,
            "messages": [result],
        }

    # ── Assemble graph ──────────────────────────────────────────────────────

    builder = StateGraph(PipelineState)

    node_table: list[tuple[str, Any]] = [
        ("restart_router", restart_router_node),
        ("literature", literature_node),
        ("patent", patent_node),
        ("clinical_trial", clinical_trial_node),
        ("opentargets", opentargets_node),
        ("genetics", genetics_node),
        ("omics", omics_node),
        ("functional", functional_node),
        ("druggability", druggability_node),
        ("openfda", openfda_node),
        ("gbd", gbd_node),
        ("indication_competition", indication_competition_node),
        ("screening_first", screening_first_node),
        ("knowledge_extraction", knowledge_extraction_node),
        ("screening_second", screening_second_node),
        ("claim_extraction", claim_extraction_node),
        ("source_quality", source_quality_node),
        ("hitl_gate", hitl_gate_node),
        ("genetics_lens", genetics_lens_node),
        ("biology_lens", biology_lens_node),
        ("safety_lens", safety_lens_node),
        ("clinical_lens", clinical_lens_node),
        ("commercial_lens", commercial_lens_node),
        ("regulatory_lens", regulatory_lens_node),
        ("experiment", experiment_node),
        ("critic", critic_node),
        ("reviewer", reviewer_node),
        ("reconciler", reconciler_node),
        ("investigator", investigator_node),
        ("report", report_node),
    ]
    # Every node collects the degradations recorded while it runs into the run's state
    # (core/degradations.py), whichever service or tool recorded them.
    for name, fn in node_table:
        builder.add_node(name, collecting(name, fn))

    # restart_router is the single entry point. _restart_route then either fans out
    # to all acquisition nodes (fresh run) or jumps to a single node (restart),
    # exclusively — a conditional edge replaces the route, where a Command would not.
    builder.add_edge(START, "restart_router")
    _restart_destinations: dict[Hashable, str] = {n: n for n in _ACQUISITION_NODE_NAMES}
    _restart_destinations.update({t: t for t in NODE_TO_JUMP_TARGET.values()})
    builder.add_conditional_edges("restart_router", _restart_route, _restart_destinations)
    for acq in _ACQUISITION_NODE_NAMES:
        builder.add_edge(acq, "screening_first")

    # Processing chain
    builder.add_edge("screening_first", "knowledge_extraction")
    builder.add_edge("knowledge_extraction", "screening_second")
    builder.add_edge("screening_second", "claim_extraction")
    builder.add_edge("claim_extraction", "source_quality")
    builder.add_edge("source_quality", "hitl_gate")

    # HITL → parallel lens fan-out (replaces hypothesis + competitive)
    _lenses = (
        "genetics_lens",
        "biology_lens",
        "safety_lens",
        "clinical_lens",
        "commercial_lens",
        "regulatory_lens",
    )
    for lens_node in _lenses:
        builder.add_edge("hitl_gate", lens_node)
        builder.add_edge(lens_node, "experiment")  # all lenses must complete before experiment

    # experiment → critic + reviewer + reconciler (all three parallel)
    builder.add_edge("experiment", "critic")
    builder.add_edge("experiment", "reviewer")
    builder.add_edge("experiment", "reconciler")

    # critic + reviewer + reconciler → investigator (all three must finish first)
    builder.add_edge("critic", "investigator")
    builder.add_edge("reviewer", "investigator")
    builder.add_edge("reconciler", "investigator")
    builder.add_edge("investigator", "report")
    builder.add_edge("report", END)

    return builder.compile(checkpointer=checkpointer)


async def run_pipeline(
    graph: CompiledStateGraph[PipelineState, None, PipelineState, PipelineState],
    initial_state: dict[str, Any],
    config: RunnableConfig,
) -> None:
    """Invoke the pipeline graph with a top-level Langfuse trace.

    Uses @observe() to create the root Langfuse trace, then injects the
    LangfuseCallbackHandler into every ainvoke() call so LangGraph nodes are
    automatically nested as child spans in the Langfuse UI.

    Runs to the HITL interrupt; a second invocation (auto-approving screening)
    resumes and runs the graph to completion.
    """
    target_gene = initial_state.get("target_gene", "unknown")
    disease = initial_state.get("disease", "unknown")
    direction = initial_state.get("direction") or "unspecified"
    gene_id = initial_state.get("gene_id") or ""
    disease_id = initial_state.get("disease_id") or ""
    run_id = str(initial_state.get("run_id", ""))

    trace_name = _trace_title(target_gene, gene_id, disease, disease_id)

    # Provision (or retrieve cached) a per-gene/disease Langfuse project so
    # each run appears as its own project in the org view, then swap the
    # global Langfuse client to point at that project's keys.
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    # None → only the default client from init_telemetry() exists, so @observe
    # resolves it unambiguously. A non-None key means we instantiated a second
    # (per-project) client below and must tell @observe which one to trace into,
    # or it skips tracing to avoid cross-project leakage.
    proj_pk: str | None = None
    try:
        proj_pk, proj_sk = await ensure_langfuse_project(trace_name)
        Langfuse(public_key=proj_pk, secret_key=proj_sk, host=base_url)
    except Exception as exc:
        proj_pk = None
        logger.warning(
            "[telemetry] project provisioning failed, using default project: %s",
            describe_exception(exc),
        )

    # Propagate cache control fields from initial_state into the graph invocation.
    # Callers (run_analysis.py, planner main.py) compute model_fingerprint from
    # router.select() before calling run_pipeline and seed it into initial_state.
    model_fingerprint = initial_state.get("model_fingerprint", "")
    force_refresh = initial_state.get("force_refresh", False)

    # Ensure a Run row exists before any Evidence FK inserts.
    try:
        async with get_session() as session:
            await RunRepository(session).create(
                run_id=uuid.UUID(run_id),
                target_gene=target_gene,
                disease=disease,
                user_request=f"{target_gene} | {disease} | {direction}",
                direction=direction,
                population=initial_state.get("population"),
                tissue=initial_state.get("tissue"),
                step_budget_total=initial_state.get("step_budget_remaining", 200),
                model_fingerprint=model_fingerprint,
                force_refresh=force_refresh,
            )
    except Exception as exc:
        logger.warning(
            "[pipeline] run row creation failed (may already exist): %s", describe_exception(exc)
        )

    # @observe creates the root OTel span / Langfuse trace.
    # propagate_attributes sets trace-level metadata (name, tags, run_id) in the
    # same OTel context so every child span inherits them.
    # Our custom get_tracer() spans inside agents nest automatically via OTel
    # context propagation — no LangChain callback injection needed.
    @observe(name=trace_name, as_type="agent")
    async def _invoke() -> None:
        with propagate_attributes(
            trace_name=trace_name,
            tags=["pipeline"],
            metadata={
                "run_id": run_id,
                "target_gene": target_gene,
                "gene_id": gene_id,
                "disease": disease,
                "disease_id": disease_id,
                "direction": direction,
            },
        ):
            logger.info(
                "[pipeline] acquisition + screening  run_id=%s  target=%s  disease=%s  direction=%s",
                run_id,
                target_gene,
                disease,
                direction,
            )
            await graph.ainvoke(cast("PipelineState", initial_state), config=config)

            snapshot = await graph.aget_state(config)
            if snapshot and snapshot.next:
                logger.info("[pipeline] HITL auto-approved, resuming reasoning")
                await graph.aupdate_state(config, {"hitl_approved": True, "hitl_overrides": {}})
                await graph.ainvoke(None, config=config)
                logger.info("[pipeline] resume complete")
            else:
                logger.info("[pipeline] complete (no HITL interrupt)")

    # Pass the per-project public key so @observe traces into the provisioned
    # project rather than skipping (multiple Langfuse clients live in-process).
    # langfuse_public_key is a reserved kwarg the decorator pops before calling;
    # observe()'s type signature preserves _invoke's exact (no-kwarg) signature,
    # so mypy can't see this runtime-injected parameter.
    await _invoke(langfuse_public_key=proj_pk)  # type: ignore[call-arg]

    if run_id:
        try:
            snapshot = await graph.aget_state(config)
            screened = _dedup_screened(cast("PipelineState", snapshot.values)) if snapshot else []
            verdicts = {
                str(ev.evidence_id): ev.extra.get("screening_verdict") or {} for ev in screened
            }
            async with get_session() as session:
                rows = await EvidenceRepository(session).get_for_run(uuid.UUID(run_id), verdicts)
            if rows:
                csv_uri = export_summary_csv(
                    target_gene, disease_id, direction, rows, run_id=run_id
                )
                logger.info("[pipeline] evidence CSV written: %s  (%d rows)", csv_uri, len(rows))
        except Exception as exc:
            logger.warning("[pipeline] CSV export failed: %s", describe_exception(exc))

    # Flush the per-project client explicitly; get_client() with no key returns
    # a disabled client when multiple clients exist, so its flush() is a no-op.
    get_client(public_key=proj_pk).flush() if proj_pk else get_client().flush()


async def resume_pipeline(
    graph: CompiledStateGraph[PipelineState, None, PipelineState, PipelineState],
    old_thread_id: str,
    from_node: str,
    config: RunnableConfig,
    force_refresh: bool = False,
) -> None:
    """Restart the pipeline from a specific node using state from a prior run.

    Loads the checkpoint stored under old_thread_id, builds a clean initial_state
    (upstream fields copied verbatim, downstream fields zeroed), and calls
    run_pipeline() on a new thread_id so Langfuse tracing, HITL auto-approval,
    and CSV export all run normally.

    Because the state is seeded into a brand-new thread, _append / replace_last
    reducers are not involved — the values are set directly, bypassing the
    accumulation that makes in-place aupdate_state unsuitable for clearing lists.
    """
    jump_target = NODE_TO_JUMP_TARGET.get(from_node)
    if jump_target is None:
        raise ValueError(
            f"Unknown --from-node {from_node!r}. Valid options: {sorted(NODE_TO_JUMP_TARGET)}"
        )

    old_config: RunnableConfig = {"configurable": {"thread_id": old_thread_id}}
    snapshot = await graph.aget_state(old_config)
    if not snapshot or not snapshot.values:
        raise ValueError(
            f"No checkpoint found for thread_id={old_thread_id!r}. "
            "Run a fresh analysis first and save the printed thread_id."
        )

    old_state: dict[str, Any] = dict(snapshot.values)

    # Warn if required upstream fields are empty — the restart will likely produce
    # garbage output but we don't abort so the user can override with force_refresh.
    for field in _REQUIRED_UPSTREAM.get(jump_target, []):
        if not old_state.get(field):
            logger.warning(
                "[resume_pipeline] WARNING: jumping to %r but %r is empty in the "
                "old checkpoint — output may be meaningless. "
                "Consider a wider restart (e.g. --from-node hitl_gate).",
                jump_target,
                field,
            )

    # Build fresh initial_state: copy everything, then zero downstream fields.
    new_initial_state = dict(old_state)
    # Always reset loop-safety and per-run counters so the new run gets a full budget.
    new_initial_state["loop_counters"] = {}
    new_initial_state["step_budget_remaining"] = 200
    new_initial_state["failed_sources"] = []
    new_initial_state["rerun_count"] = old_state.get("rerun_count", 0) + 1
    if force_refresh:
        new_initial_state["force_refresh"] = True
    # Apply downstream field resets for this jump target.
    new_initial_state.update(CLEAR_FROM_NODE[jump_target])

    new_thread_id = config["configurable"]["thread_id"]
    new_configurable: dict[str, Any] = {
        **config.get("configurable", {}),
        "thread_id": new_thread_id,
        "restart_from": jump_target,
    }
    new_config: RunnableConfig = cast(
        "RunnableConfig", {**config, "configurable": new_configurable}
    )
    logger.info(
        "[resume_pipeline] new_thread=%s  jump=%s  old_thread=%s  rerun=%d",
        new_thread_id,
        jump_target,
        old_thread_id,
        new_initial_state["rerun_count"],
    )
    await run_pipeline(graph, new_initial_state, new_config)
