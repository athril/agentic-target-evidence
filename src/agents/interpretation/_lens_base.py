# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared base logic for all six interpretation lens agents.

Each lens follows the same contract:
  1. Deserialise CoreClaim dicts from task_spec["extracted_claims"]
  2. Filter to this lens's relevant evidence types
  3. Call LLM with the lens-specific skill
  4. Parse LensVerdict from the response
  5. Return {"lens_verdicts": [verdict.model_dump(mode="json")]}

Concrete lens agents import `run_lens` and supply their `LENS_NAME`,
`EVIDENCE_TYPES`, and `skill_name`. They declare their own AgentContract.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import uuid
from collections.abc import Callable
from typing import Any, Literal, cast

from agents.interpretation._lens_citations import SKILL as CITATION_SKILL
from agents.interpretation._lens_citations import add_literature_support
from agents.interpretation._lens_confidence import (
    SCORING_CONFIGS,
    SCORING_VERSION,
    axis_conflict_flag,
    check_prose,
    contradicts_axes,
    prose_violations,
    reaggregate,
    score_verdict,
)
from agents.interpretation._lens_repair import SKILL as REPAIR_SKILL
from agents.interpretation._lens_repair import repair_enabled, repair_prose
from core import tokens
from core.json_utils import loads_recovering, strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from harness.skills import load_skill
from schemas.evidence import CoreClaim, DataClass, Direction, Evidence, EvidenceType
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, GuardCheck, LensVerdict, ValidationFlag
from services.evidence.claim_effect import effect_label
from services.evidence.claim_relevance import Relevance, collapse_restatements, relevance_from_spec
from services.evidence.clinical_trial_interpret import TrialFact, trial_reported_values
from services.evidence.constraint_interpret import ConstraintReading
from services.evidence.evidence_hierarchy import (
    evidence_weight,
    infer_evidence_subtype,
    llm_prior_weight,
)
from services.evidence.grounding_guards import cell_types_reported
from services.evidence.guard_core import Violation, annotate
from services.evidence.guard_rules import GUARD_RULES, GuardFacts, GuardFamily, GuardRule
from services.evidence.lens_inputs import missing_inputs_note
from services.evidence.lens_selection import LITERATURE, kind_counts, lens_slots, select_claims
from services.evidence.literature_selection import LiteratureIndex, PaperRecord, rank_literature
from services.evidence.readings import DependencyReading, build_dependency_reading

LensName = Literal["genetics", "biology", "safety", "clinical", "commercial", "regulatory"]

# Single source of truth for which *structured* evidence types each lens reasons
# over. Both the lens agents (to filter the claims they consume) and the lens report
# writer (to cross-reference the kept source evidence) read from this map.
#
# Note: free-text literature types (ARTICLE/ABSTRACT/BOOK/CONFERENCE) are deliberately
# absent here — they carry no native sub-type and route by per-claim `topics` tags
# instead (see `claim_matches_lens`). Biology is therefore topic-routed for literature
# just like the other three literature-consuming lenses; it keeps only its structured
# types below.
LENS_EVIDENCE_TYPES: dict[LensName, tuple[EvidenceType, ...]] = {
    "genetics": (EvidenceType.GENETICS, EvidenceType.CONSTRAINT),
    "biology": (
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.DRUGGABILITY,
        EvidenceType.OMICS,  # tissue/anatomical expression — mechanism & disease-tissue overlap
        EvidenceType.EXPRESSION,
        EvidenceType.REGULATORY_ELEMENT,  # cis-regulatory assay coverage at the locus (ENCODE)
    ),
    # No GENETICS: disease association (GWAS, curations) is the genetics lens's evidence;
    # given it, the safety lens led its rationale with it ("supported by genetic
    # association with the disease"), which skill Rule 19 forbids.
    "safety": (
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.CONSTRAINT,
        EvidenceType.REGULATORY,  # FAERS signal + black-box / contraindications
        EvidenceType.FUNCTIONAL_GENOMICS,  # DepMap essentiality + IMPC viability/lethality
    ),
    "clinical": (EvidenceType.CLINICAL_TRIAL,),
    "commercial": (
        EvidenceType.PATENT,
        EvidenceType.REGULATORY,  # FDA-approved drug landscape / gene-in-MoA
        EvidenceType.COMPETITION,  # indication drug/trial landscape, any mechanism
        EvidenceType.CLINICAL_TRIAL,  # target-directed clinical programs = competitors
    ),
    "regulatory": (EvidenceType.REGULATORY,),
}

# Which drug records each lens reads, by their relation to the target
# (schemas.evidence.TargetRelation). A label or FAERS bundle of a drug acting on other
# proteins describes that drug, not the target: the safety lens took an indication drug's
# adverse-event reports and a contrast agent's label as the target's liabilities. The
# regulatory and commercial lenses still read the indication's approved drugs (class
# precedent, standard of care); a record related by text search only reaches no lens.
# Lenses not listed read drug records of any relation.
LENS_TARGET_RELATIONS: dict[LensName, frozenset[str]] = {
    "safety": frozenset({"acts_on_target"}),
    "regulatory": frozenset({"acts_on_target", "indication"}),
    "commercial": frozenset({"acts_on_target", "indication"}),
}

_VALID_VERDICTS = {"support", "oppose", "neutral", "insufficient_evidence"}
_UNPARSED_RATIONALE = "LLM response could not be parsed."
_REFORMAT_REQUEST = (
    "Your reply could not be parsed as one JSON object. Return the same verdict again as a "
    "single valid JSON object with the keys the instructions specify — no prose before or "
    "after it, no code fence, no comments, double-quoted keys and strings, no trailing commas."
)
logger = logging.getLogger(__name__)


def _max_claims() -> int:
    """Cap on claims fed into a single lens call, to stay within the local model's
    context window (the lens entries in task_num_ctx in config/routing.yaml, or
    LENS_NUM_CTX in .env). Configurable via
    LENS_MAX_CLAIMS; falls back to 100 if unset or unparseable.
    """
    raw = os.environ.get("LENS_MAX_CLAIMS", "100")
    try:
        return max(1, int(raw))
    except ValueError:
        return 100


# Prompt budget. Ollama keeps only the newest num_ctx tokens and drops the rest without
# an error, oldest first: a commercial-lens prompt of 33K tokens lost its skill prompt
# (the system message) and came back as "insufficient evidence". The claim list is
# shrunk, weakest claims first, until the whole prompt fits.
_OUTPUT_RESERVE_TOKENS = 3072  # the verdict JSON is ~1.5K tokens
_MIN_PROMPT_CLAIMS = 10


def estimate_tokens(text: str, task: str | None = None) -> int:
    """Prompt tokens at the local model's measured characters per token (core.tokens)."""
    return tokens.estimate_tokens(text, task=task)


def prompt_budget(provider: Any, task: str) -> int | None:
    """Input tokens a lens prompt may use, or None when nothing bounds it.

    The provider's context window less the output reserve, tightened by the
    LENS_PROMPT_BUDGET env var (tokens) when set — small models follow instructions
    better in short prompts, which is worth measuring per model.
    """
    limits: list[int] = []
    window = context_window(provider, task)
    if window is not None:
        limits.append(window - _OUTPUT_RESERVE_TOKENS)
    with contextlib.suppress(ValueError):
        env = int(os.environ.get("LENS_PROMPT_BUDGET", "0"))
        if env > 0:
            limits.append(env)
    return min(limits) if limits else None


def context_window(provider: Any, task: str) -> int | None:
    """The provider's context window for ``task``, or None when it does not say."""
    window_of = getattr(provider, "context_window", None)
    window = window_of(task) if callable(window_of) else None
    return window if isinstance(window, int) and window > 0 else None


_REPLY_MARGIN_TOKENS = 256  # the chat template and the estimate being approximate


def reply_room(provider: Any, task: str, prompt_tokens: int, cap: int) -> int:
    """``max_tokens`` for a reply: ``cap``, or less when the context window has less room
    left after the prompt (never below a minimal reply)."""
    window = context_window(provider, task)
    if window is None:
        return cap
    return max(_REPLY_MARGIN_TOKENS, min(cap, window - prompt_tokens - _REPLY_MARGIN_TOKENS))


# Fixed prompt blocks (the direction reading, the lens's pre-computed context and the
# evidence ledger) may take at most this share of the prompt budget, so the claims keep
# the majority: a target with many drugs once pushed three lenses to the claim minimum.
FIXED_SHARE = 0.4
_LONG_LINE = 300  # a free-text line longer than this is cut to its first sentence
_ROW = re.compile(r"^(\s*)(?:[•*-]|\d+[.)]|\|)\s")
_ROW_STEPS = (8, 5, 3, 1)  # rows kept per list, tried in turn
_FIRST_SENTENCE = re.compile(r"^(\s*.{40,}?[.!?])(?=\s)")


def _cut_long_lines(block: str) -> str:
    out = []
    for line in block.split("\n"):
        if len(line) > _LONG_LINE and not _ROW.match(line):
            m = _FIRST_SENTENCE.match(line)
            head = m.group(1) if m and len(m.group(1)) < len(line) else line[:_LONG_LINE]
            line = f"{head} […]"
        out.append(line)
    return "\n".join(out)


def _cut_rows(block: str, keep: int) -> str:
    """Keep the first ``keep`` rows of each list (a row with its more-indented lines); the
    rest are replaced by a line saying how many were left out."""
    lines = block.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _ROW.match(lines[i])
        if m is None:
            out.append(lines[i])
            i += 1
            continue
        indent = len(m.group(1))
        rows = 0
        while i < len(lines):
            row = _ROW.match(lines[i])
            line_indent = len(lines[i]) - len(lines[i].lstrip())
            if row is not None and len(row.group(1)) == indent:
                rows += 1
            elif not (lines[i].strip() and line_indent > indent):
                break  # the list ended
            if rows <= keep:
                out.append(lines[i])
            i += 1
        if rows > keep:
            out.append(f"{' ' * indent}… {rows - keep} more rows not shown")
    return "\n".join(out)


def fit_fixed_blocks(
    blocks: list[str], cap: int | None, task: str | None = None
) -> tuple[list[str], list[str]]:
    """``blocks`` trimmed to at most ``cap`` estimated tokens, and what was trimmed.

    In order, until they fit: free-text lines longer than ``_LONG_LINE`` are cut to their
    first sentence; then each list keeps its first 8, 5, 3, then 1 rows — lists are written
    strongest first (drugs by stage, ledger by weight) — and says how many it left out;
    last, each block is cut to its share of the cap. The last block should be the
    evidence ledger, so its weakest rows go with the other lists' tails.
    """

    def size(bs: list[str]) -> int:
        return estimate_tokens("".join(bs), task) if any(bs) else 0

    if cap is None or size(blocks) <= cap:
        return blocks, []
    notes: list[str] = []
    blocks = [_cut_long_lines(b) for b in blocks]
    notes.append("long free text cut to its first sentence")
    for keep in _ROW_STEPS:
        if size(blocks) <= cap:
            return blocks, notes
        blocks = [_cut_rows(b, keep) for b in blocks]
        notes.append(f"lists cut to their first {keep} rows")
    if size(blocks) > cap:
        mark = " […]"
        total = sum(len(b) for b in blocks) or 1
        room = max(0.0, cap * tokens.get_estimator().ratio(task=task) - len(mark) * len(blocks))
        share = min(1.0, room / total)
        blocks = [b if share >= 1.0 or not b else b[: int(len(b) * share)] + mark for b in blocks]
        notes.append("blocks cut to their share of the budget")
    return blocks, notes


def fit_claim_limit(
    fixed_tokens: int,
    budget: int | None,
    render: Callable[[int], str],
    available: int,
    task: str | None = None,
) -> int:
    """The largest claim count (≤ `_max_claims()`) whose rendered list fits the budget.

    ``render(n)`` is the claim block for the ``n`` strongest claims. Never below
    `_MIN_PROMPT_CLAIMS`: a lens without its best evidence is worse than a long prompt.
    """
    limit = _max_claims()
    top = min(limit, available)
    if budget is None or fixed_tokens + estimate_tokens(render(top), task) <= budget:
        return limit
    lo, hi = min(_MIN_PROMPT_CLAIMS, top), top
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fixed_tokens + estimate_tokens(render(mid), task) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


# A literature claim whose source never resolved a quality score (no SJR/OpenAlex
# match) falls back to the same floor as a Q4 journal/preprint.
_UNSCORED_LITERATURE_WEIGHT = 0.2

# Free-text literature types. A literature claim carries no native sub-type, so it
# routes to lenses by its per-claim ``topics`` tags rather than by ``evidence_type`` —
# for every lens, including biology.
_LITERATURE_TYPES: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.ARTICLE,
        EvidenceType.ABSTRACT,
        EvidenceType.BOOK,
        EvidenceType.CONFERENCE,
    }
)


def claim_matches_lens(claim: CoreClaim, lens: LensName) -> bool:
    """Single source of truth for "does this claim belong to this lens".

    Shared by lens-input routing (`_filter_claims`) and lens-report citation
    selection (`lens_report._render`) so the two can never drift apart.

    - **Literature** claims route purely by their multi-valued `topics` tags: a claim
      reaches a lens iff that lens is named in `topics`. This holds for *every* lens,
      biology included. commercial/regulatory are not in `LensTopic`, so literature
      never reaches them. One exception: a claim about a model organism or cell system
      never reaches the genetics lens, whose causality axis is human evidence.
    - **Structured** claims route by `evidence_type` membership in `LENS_EVIDENCE_TYPES`.
    - A claim from a drug record reaches a lens only if its `target_relation` is one the
      lens reads (`LENS_TARGET_RELATIONS`).

    ``lens`` is a plain str; `topics` holds `LensTopic` (a `StrEnum`), so ``lens in
    claim.topics`` compares by value as intended.
    """
    relation = claim.target_relation
    if relation is not None and relation not in LENS_TARGET_RELATIONS.get(lens, {relation}):
        return False
    if claim.evidence_type in _LITERATURE_TYPES:
        if lens == "genetics" and _NON_HUMAN_SYSTEM.search(claim.claim_text):
            return False
        return lens in claim.topics
    return claim.evidence_type in LENS_EVIDENCE_TYPES.get(lens, ())


# Literature claims about model organisms or cell systems. The extractor tags knockout and
# knockdown findings "genetics" as well as "biology", and the genetics lens then cited
# siRNA in mice and conditional knockouts as human causality; they stay with the biology
# lens.
_NON_HUMAN_SYSTEM = re.compile(
    r"\b(?:mice|mouse|murine|rats?|rodents?|zebrafish|drosophila|c\. elegans|yeast|"
    r"primates?|monkeys?|in vitro|cell lines?|cultured cells|xenografts?|organoids?|"
    r"sirna|shrna|knock-?down)\b",
    re.IGNORECASE,
)


def _deserialise_claims(raw: list[Any]) -> list[CoreClaim]:
    claims: list[CoreClaim] = []
    for item in raw:
        if isinstance(item, CoreClaim):
            claims.append(item)
        elif isinstance(item, dict):
            with contextlib.suppress(Exception):
                claims.append(CoreClaim.model_validate(item))
    return claims


def _filter_claims(
    claims: list[CoreClaim],
    evidence_types: tuple[EvidenceType, ...],
    lens: LensName | None = None,
) -> list[CoreClaim]:
    """Select the claims a lens reasons over.

    With ``lens`` supplied, delegates to `claim_matches_lens` (structured types route
    by `evidence_type`; literature routes by `topics`). With ``lens=None``, falls back
    to a pure `evidence_type` filter over ``evidence_types`` — kept for direct
    callers/tests that only want type filtering.
    """
    if lens is None:
        return [c for c in claims if c.evidence_type in evidence_types]
    return [c for c in claims if claim_matches_lens(c, lens)]


# Version of what the lens code does around the model call — the prompt blocks an agent
# builds from task_spec and the deterministic rules applied to the verdict. Neither is in
# the input digest's skill or spec, so a change to them alone would replay verdicts cached
# under the old code. Bump it whenever a lens's prompt-building code, input selection or
# post-LLM rules change. 2: direction scoping, verdict-from-axes, target-level competition,
# label facts. 3: slots per evidence kind (config/lens_inputs.yaml). 4: literature ranked
# by paper (services/evidence/literature_selection.py), with a study note per claim.
# 5: posted deaths and serious events per arm in the safety lens's human-harm facts.
# 6: commercial competitive axis and verdict computed from programmes and readings.
# 7: prose stating a verdict other than the final one is aligned after the rules.
# 8: the genetics mechanism block uses the disease's curated mechanism when there is one.
# 9: clinical axes and verdict computed from the programmes in the indication.
# 10: the clinical lens is shown the programme table and the axes fixed by rule.
# 11: a study in an animal model of one of the disease's model phenotypes is related.
# 12: the biology lens's perturbation_direction axis, computed from models of the disease.
# 13: biology druggability by rule and the modality of each known drug in the prompt.
# 14: a secreted target's site of action in the biology and safety prompts.
# 15: genetics causality by rule (curated or somatic record, or replicated human genetics).
# 16: genetics support only when the human-genetics direction is not contradicted.
# 17: biology verdict from its axes; common essentiality left to the safety lens.
# 18: regulatory insufficient_evidence is neutral when label claims were shown.
# 19: the disease scope's related names include the cross-ontology parents.
# 20: fixed prompt blocks trimmed to their share of the budget; replies bounded by the window.
# 21: restated literature findings shown once; literature ranked by the lens's own signal.
# 22: decisive claims pinned in their kind's slots; the rest listed by id.
# 23: claim effects typed from drug data and two fixed-choice extractor fields.
# 24: liabilities counted by the change that causes them; an unassessed change is not safe.
# 25: GWAS–QTL colocalisation read as a direction of effect.
# 26: faithfulness verdicts only flag claims; literature harms are not liabilities.
# 27: commercial and regulatory no longer score the direction of effect.
# 28: literature claims typed from the statement of their grounded sentence.
# 29: genetics literature read from variant statements, the variant's effect by lookup.
# 30: biology perturbations from statements; its verdict no longer reads human genetics.
# 31: clinical links publications to trials by NCT id and reads no text-typed effects.
# 32: curated safety events take their direction, one finding per change; assay and
#     pathway rows, and rows naming no change, are listed, not counted.
# 33: the safety verdict computed from findings without questions; one mouse finding per
#     change; safety stops by Open Targets category, in this indication only.
# 34: the regulatory verdict computed from its own axes.
LENS_RULES_VERSION = "34"

# Effect sources typed from a sentence's words rather than data: a genetic perturbation
# the sentence states, and the extractor's former fixed-choice fields.
_TEXT_TYPED = frozenset({"text", "claim"})

# task_spec keys the input digest projects rather than hashing verbatim: claims carry
# per-run UUIDs, and the source-quality map and the literature index are keyed by
# evidence UUID.
_DIGEST_PROJECTED_KEYS = frozenset({"extracted_claims", "source_quality", "literature_index"})


def as_literature_index(value: Any) -> LiteratureIndex | None:
    """The task spec's literature index as a model (None when absent or malformed)."""
    if isinstance(value, LiteratureIndex):
        return value
    if not isinstance(value, dict) or not value:
        return None
    try:
        return LiteratureIndex.model_validate(value)
    except ValueError:
        logger.warning("literature index in task_spec did not validate; ranking without it")
        return None


def _project_index(value: Any) -> dict[str, Any]:
    """The literature index without its per-run UUID keys, for the input digest."""
    index = as_literature_index(value)
    if index is None:
        return {}
    return {
        "papers": sorted(json.dumps(r.model_dump(), sort_keys=True) for r in index.papers.values()),
        "terms": [index.target_terms, index.drug_terms, index.disease_terms],
    }


def _scoring_config_digest() -> str:
    h = hashlib.sha256()
    for path in SCORING_CONFIGS:
        with contextlib.suppress(OSError), open(path, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def lens_input_digest(
    lens: LensName, spec: dict[str, Any], skill_text: str, extra: str = ""
) -> str:
    """SHA-256 over everything a lens reads, stable across runs with the same evidence.

    ``extra`` is any further prompt text or version the lens uses, so a verdict from a
    different path never shares a cache entry: the safety lens passes the version of the
    rules that compute its verdict.

    Covers the claims routed to this lens (by content, not UUID, with their source
    quality), every other task_spec field (extra-context text, counts, readings) and
    the lens skill prompt. Used in the lens cache key so a cached verdict is only
    reused when the lens would see the same input, e.g. not after a screening fix
    starts letting FDA labels through, or after the prompt changes.
    """
    claims = _filter_claims(
        _deserialise_claims(spec.get("extracted_claims") or []), LENS_EVIDENCE_TYPES[lens], lens
    )
    quality_map = spec.get("source_quality") or {}
    projected_claims = sorted(
        json.dumps(
            {
                "evidence_type": c.evidence_type.value,
                "claim_text": c.claim_text,
                "effect": effect_label(c),
                "confidence": c.confidence,
                "quality": quality_map.get(str(c.source_evidence_id))
                if c.source_evidence_id
                else None,
            },
            sort_keys=True,
            default=str,
        )
        for c in claims
    )
    context = {k: v for k, v in spec.items() if k not in _DIGEST_PROJECTED_KEYS}
    payload = json.dumps(
        {
            "skill": hashlib.sha256(skill_text.encode()).hexdigest(),
            "rules": LENS_RULES_VERSION,
            "scoring": SCORING_VERSION,
            "scoring_configs": _scoring_config_digest(),
            "citation_skill": hashlib.sha256(load_skill(CITATION_SKILL).encode()).hexdigest(),
            "repair_skill": hashlib.sha256(load_skill(REPAIR_SKILL).encode()).hexdigest()
            if repair_enabled()
            else "off",
            "claims": projected_claims,
            "literature_index": _project_index(spec.get("literature_index")),
            "context": context,
            "extra": extra,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _claim_weight(
    claim: CoreClaim,
    quality_map: dict[str, Any],
    disease_classes: frozenset[str],
    lens: LensName | None = None,
) -> float:
    """Evidence-strength weight on the same 0-1 scale for both literature and
    structured claims — literature uses its resolved `sjr_score` (or the
    Q4/preprint floor if unscored); structured evidence uses the disease-class-
    and lens-conditional `evidence_weight` hierarchy (config/evidence_hierarchy.yaml),
    replacing the old flat `_NON_LITERATURE_WEIGHT`.
    """
    if claim.evidence_type in _LITERATURE_TYPES:
        q = quality_map.get(str(claim.source_evidence_id)) if claim.source_evidence_id else None
        score = q.get("sjr_score") if q else None
        return score if score is not None else _UNSCORED_LITERATURE_WEIGHT
    subtype = infer_evidence_subtype(claim.evidence_type, claim.claim_text)
    return evidence_weight(claim.evidence_type, subtype, disease_classes, lens=lens)


# What a registry claim says about a trial ("PHASE3 COMPLETED clinical trial NCT…: … (n=…)",
# "NCT… (PHASE3 TERMINATED) primary outcome — …").
_TRIAL_PHASE_RE = re.compile(r"\bPHASE\s*([1-4])", re.IGNORECASE)
_TRIAL_READOUT_RE = re.compile(r"\b(?:COMPLETED|TERMINATED)\b|\bprimary outcome\b", re.IGNORECASE)
_TRIAL_SIZE_RE = re.compile(r"\(n=(\d+)\)")


def trial_information(text: str) -> float:
    """How much a trial claim can tell a lens, for ranking among trials: later phase
    (phase 4 counts as 3 — post-marketing studies are mostly small), then a finished
    trial or a posted primary outcome, then enrolment.

    Every trial weighs the same in the evidence hierarchy, so the lens saw the first
    100 of up to 1,300 trials in extraction order, and the completed outcome trials of
    an approved class were not among them.
    """
    phases = [int(p) for p in _TRIAL_PHASE_RE.findall(text or "")]
    phase = min(3, max(phases)) if phases else 0
    readout = 1.0 if _TRIAL_READOUT_RE.search(text or "") else 0.0
    sizes = [int(n) for n in _TRIAL_SIZE_RE.findall(text or "")]
    size = min(4.9, math.log10(max(sizes) + 1)) if sizes else 0.0
    return phase * 10 + readout * 5 + size


def _claim_sort_key(
    claim: CoreClaim,
    quality_map: dict[str, Any],
    disease_classes: frozenset[str] = frozenset(),
    lens: LensName | None = None,
) -> tuple[float, float, float]:
    """Rank claims best-first so truncation drops the weakest ones, not whichever
    happened to land past the `_max_claims()` cutoff in extraction order.

    Primary key: `_claim_weight` (descending). Secondary key, for trial claims: how
    informative the trial is (`trial_information`). Then claim confidence, descending.
    """
    weight = _claim_weight(claim, quality_map, disease_classes, lens)
    information = (
        trial_information(claim.claim_text)
        if claim.evidence_type == EvidenceType.CLINICAL_TRIAL
        else 0.0
    )
    return (-weight, -information, -(claim.confidence or 0.0))


def _selection(
    claims: list[CoreClaim],
    quality_map: dict[str, Any] | None = None,
    disease_classes: frozenset[str] = frozenset(),
    lens: LensName | None = None,
    limit: int | None = None,
    papers: LiteratureIndex | dict[str, Any] | None = None,
    relevance: Relevance | None = None,
) -> tuple[list[CoreClaim], dict[str, list[CoreClaim]]]:
    """The claims a lens is shown, and for each the other claims stating the same finding
    (by claim id; empty without ``relevance``). See `_shown_claims`."""
    quality_map = quality_map or {}
    budget = _max_claims() if limit is None else limit
    index = as_literature_index(papers)
    members: dict[str, list[CoreClaim]] = {}

    def strongest(items: list[CoreClaim]) -> list[CoreClaim]:
        return sorted(
            items,
            key=lambda c: (*_claim_sort_key(c, quality_map, disease_classes, lens), c.claim_text),
        )

    def rank(kind: str, items: list[CoreClaim]) -> list[CoreClaim]:
        if kind != LITERATURE or index is None or lens is None:
            return strongest(items)
        ranked = rank_literature(
            items, index, lens, relevance.signal_papers if relevance is not None else ()
        )
        if relevance is None:
            return ranked
        groups = collapse_restatements(ranked, relevance)
        members.update({str(g[0].evidence_id): g[1:] for g in groups if len(g) > 1})
        return [g[0] for g in groups]

    kinds = lens_slots(lens) if lens else ()
    if not kinds:
        return strongest(claims)[:budget], {}
    pinned = relevance.pinned if relevance is not None else set()
    shown = select_claims(claims, kinds, budget, rank, pinned)
    return shown, {
        str(c.evidence_id): members[str(c.evidence_id)]
        for c in shown
        if str(c.evidence_id) in members
    }


def _shown_claims(
    claims: list[CoreClaim],
    quality_map: dict[str, Any] | None = None,
    disease_classes: frozenset[str] = frozenset(),
    lens: LensName | None = None,
    limit: int | None = None,
    papers: LiteratureIndex | dict[str, Any] | None = None,
    relevance: Relevance | None = None,
) -> list[CoreClaim]:
    """The claims a lens LLM actually sees, at most ``limit`` (default `_max_claims()`;
    `run_lens` lowers it to fit the prompt budget), plus the claims each one represents.

    With a lens: slots per evidence kind from config/lens_inputs.yaml, each kind ranked
    on its own (services/evidence/lens_selection.py) — literature by paper when the run's
    literature index (``papers``) is given (services/evidence/literature_selection.py),
    with the lens's own signal and restatements collapsed when ``relevance`` is given
    (services/evidence/claim_relevance.py). Without a lens: strongest first on the
    evidence-weight scale. Shared by the prompt builder and the confidence scorer, so a
    cited id is scored only if the LLM was shown it (a restatement's members are shown by
    id beside it).
    """
    shown, members = _selection(
        claims, quality_map, disease_classes, lens, limit, papers, relevance
    )
    return [*shown, *(m for ms in members.values() for m in ms)]


def shown_input_counts(shown: list[CoreClaim], lens: LensName) -> dict[str, int]:
    """How many shown claims each evidence kind of the lens contributed."""
    return dict(kind_counts(shown, lens_slots(lens)))


def _study_note(record: PaperRecord | None, lens: LensName | None) -> str:
    """What kind of evidence a literature claim's paper is: design, year, human/animal."""
    if record is None:
        return ""
    design = record.design.get(lens or "") if lens else None
    parts = [design.family.replace("_", " ")] if design and design.family != "other" else []
    if record.review and "review" not in parts:
        parts.append("review")
    if record.year:
        parts.append(str(record.year))
    if record.human is not None:
        parts.append("human" if record.human else "animal or cell model")
    return ", ".join(parts)


def _claims_to_json(
    claims: list[CoreClaim],
    quality_map: dict[str, Any] | None = None,
    disease_classes: frozenset[str] = frozenset(),
    lens: LensName | None = None,
    limit: int | None = None,
    papers: LiteratureIndex | dict[str, Any] | None = None,
    relevance: Relevance | None = None,
) -> str:
    quality_map = quality_map or {}
    index = as_literature_index(papers)
    items: list[dict[str, Any]] = []
    shown, members = _selection(claims, quality_map, disease_classes, lens, limit, index, relevance)
    for c in shown:
        item: dict[str, Any] = {
            "claim_id": str(c.evidence_id),
            "evidence_type": c.evidence_type.value,
            "claim_text": c.claim_text[:200],
            "confidence": c.confidence,
        }
        # The claim's typed effect on the target, read for the model; the derived
        # therapeutic direction is not shown, so the model reads which way the target
        # changed and what followed instead of a bare inhibit/activate tag. Clinical reads
        # registry facts and linked results, never an effect typed from a sentence's words.
        effect = effect_label(c)
        if effect and not (lens == "clinical" and c.effect_source in _TEXT_TYPED):
            item["effect"] = effect
        same = members.get(str(c.evidence_id)) or []
        if same:
            item["agree"] = f"+{len(same)} papers agree"
            item["agreeing_claim_ids"] = [str(m.evidence_id) for m in same]
        if index is not None and c.evidence_type in _LITERATURE_TYPES:
            note = _study_note(index.papers.get(str(c.source_evidence_id)), lens)
            if note:
                item["study"] = note
        q = quality_map.get(str(c.source_evidence_id)) if c.source_evidence_id else None
        if q:
            item["quality"] = {
                "score": q.get("sjr_score"),
                "quartile": q.get("sjr_quartile"),
                "predatory": q.get("predatory_flag"),
                "preprint": q.get("preprint_flag"),
            }
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def _build_evidence_ledger(
    claims: list[CoreClaim],
    quality_map: dict[str, Any],
    disease_classes: frozenset[str],
    lens: LensName | None = None,
) -> str:
    """Render the evidence-strength ledger injected alongside the claims JSON.

    Groups claims by (evidence_type, subtype) bucket and reports the claim count
    and max deterministic weight per bucket, descending by weight, so the LLM sees
    at a glance which evidence categories are strongest before it reasons over the
    individual claims. Closes with the `llm_prior_weight` floor rule so uncited
    model prior knowledge cannot be used to inflate confidence. Returns "" for an
    empty claim list (mirrors the other guards' no-op-when-nothing-to-say shape).
    """
    if not claims:
        return ""

    buckets: dict[tuple[str, str | None], list[float]] = {}
    for c in claims:
        weight = _claim_weight(c, quality_map, disease_classes, lens)
        subtype = (
            None
            if c.evidence_type in _LITERATURE_TYPES
            else infer_evidence_subtype(c.evidence_type, c.claim_text)
        )
        buckets.setdefault((c.evidence_type.value, subtype), []).append(weight)

    rows = [
        (max(weights), f"{etype}/{subtype}" if subtype else etype, len(weights))
        for (etype, subtype), weights in buckets.items()
    ]
    rows.sort(key=lambda r: -r[0])

    lines = [f"- {label}: {count} claim(s), weight {weight:.2f}" for weight, label, count in rows]
    header = (
        "Evidence-strength ledger (deterministic weights, 0-1 scale; "
        "use to calibrate confidence, strongest first):"
    )
    footer = (
        f"Uncited prior knowledge not backed by a claim above carries weight "
        f"{llm_prior_weight():.2f} and must not by itself raise verdict confidence."
    )
    return header + "\n" + "\n".join(lines) + "\n" + footer


def _direction_enum(direction: str) -> Direction:
    return (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )


def _insufficient_verdict(
    lens: LensName,
    target_gene: str,
    disease: str,
    direction_enum: Direction,
    run_id: uuid.UUID,
    trace_id: str,
    *,
    rationale: str = _UNPARSED_RATIONALE,
    narrative: str = "",
) -> LensVerdict:
    return LensVerdict(
        run_id=run_id,
        trace_id=trace_id,
        lens=lens,
        target_gene=target_gene,
        disease=disease,
        direction=direction_enum,
        overall_verdict="insufficient_evidence",
        confidence=0.0,
        axes=[],
        rationale=rationale,
        narrative=narrative,
    )


def _unparsed(verdict: LensVerdict) -> bool:
    return verdict.overall_verdict == "insufficient_evidence" and (
        verdict.rationale == _UNPARSED_RATIONALE
    )


# The model's own deliberation copied into an output field: CFTR regulatory's
# label_safety rationale ran ~600 words of "Wait, let's re-read … the instruction says …
# I will go with 'oppose'". Such a field keeps the sentences before the first marker.
_REASONING_MARKER = re.compile(
    r"\b(?:wait|hmm)\s*[,.:!]"
    r"|\blet'?s\s+(?:re-?read|re-?evaluate|reconsider|look\s+at\s+the\s+instruction|check\s+the\s+instruction)"
    r"|\bper\s+the\s+instructions?\b"
    r"|\bthe\s+instructions?\s+(?:says?|also\s+says|is\s+strict|asks|for\s+the\s+\w+\s+axis)\b"
    r"|\bI\s+will\s+go\s+with\b|\bthe\s+(?:prompt|guide)\s+says\b",
    re.IGNORECASE,
)
_LEAK_SENTENCE = re.compile(r"(?<=[.!?])\s+")
REASONING_LEAK_RULE = "reasoning_leak"


def strip_leaked_reasoning(text: str) -> tuple[str, bool]:
    """``text`` without deliberation the model leaked into it: the sentences before the
    sentence holding the first reasoning marker. (text, whether anything was cut)."""
    if not text or not _REASONING_MARKER.search(text):
        return text, False
    kept: list[str] = []
    for sentence in _LEAK_SENTENCE.split(text.strip()):
        if _REASONING_MARKER.search(sentence):
            break
        kept.append(sentence)
    return " ".join(kept).strip(), True


def _strip_reasoning(verdict: LensVerdict) -> LensVerdict:
    """Cut leaked deliberation from every prose field, with one flag naming the fields."""
    cut: list[str] = []

    def clean(text: str, where: str) -> str:
        out, leaked = strip_leaked_reasoning(text)
        if leaked:
            cut.append(where)
        return out

    axes = [a.model_copy(update={"rationale": clean(a.rationale, a.axis)}) for a in verdict.axes]
    rationale = clean(verdict.rationale, "rationale")
    narrative = clean(verdict.narrative, "narrative")
    if not cut:
        return verdict
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="medium",
        rule_id=REASONING_LEAK_RULE,
        claim_excerpt="",
        message=(
            f"The model's deliberation leaked into {', '.join(cut)}; the text from the first "
            "reasoning sentence on was removed."
        ),
    )
    return verdict.model_copy(
        update={
            "axes": axes,
            "rationale": rationale,
            "narrative": narrative,
            "validation_flags": [*verdict.validation_flags, flag],
        }
    )


def _parse_verdict(
    raw: str,
    lens: LensName,
    target_gene: str,
    disease: str,
    direction: str,
    run_id: uuid.UUID,
    trace_id: str,
) -> LensVerdict:
    direction_enum = _direction_enum(direction)
    try:
        # loads_recovering: tolerates two recurring local-model formatting faults
        # that would otherwise discard an otherwise-valid verdict as "could not be
        # parsed" — (1) literal newlines inside multi-paragraph prose fields (e.g.
        # the biology lens's mandated 2-4 paragraph `narrative`), via strict=False;
        # and (2) a prematurely-closed root object whose remaining keys (e.g. `axes`)
        # spill out as siblings, by splicing out the stray closing brace.
        data = loads_recovering(strip_json_fence(raw))
        if isinstance(data, dict):
            ov = data.get("overall_verdict", "insufficient_evidence")
            if ov not in _VALID_VERDICTS:
                ov = "insufficient_evidence"
            axes = [
                AxisVerdict(
                    axis=ax.get("axis", ""),
                    verdict=ax.get("verdict"),
                    confidence=float(max(0.0, min(1.0, ax.get("confidence", 0.0)))),
                    rationale=ax.get("rationale", ""),
                    supporting_claim_ids=[str(x) for x in (ax.get("supporting_claim_ids") or [])],
                )
                for ax in (data.get("axes") or [])
            ]
            return _strip_reasoning(
                LensVerdict(
                    run_id=run_id,
                    trace_id=trace_id,
                    lens=lens,
                    target_gene=target_gene,
                    disease=disease,
                    direction=direction_enum,
                    overall_verdict=ov,
                    confidence=float(max(0.0, min(1.0, data.get("confidence", 0.0)))),
                    axes=axes,
                    rationale=data.get("rationale", ""),
                    narrative=data.get("narrative", ""),
                )
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return _insufficient_verdict(lens, target_gene, disease, direction_enum, run_id, trace_id)


async def run_lens(
    msg: AgentMessage,
    ctx: RunContext,
    *,
    lens: LensName,
    evidence_types: tuple[EvidenceType, ...],
    skill_name: str,
    extra_context: str = "",
    guard_empty: bool = False,
    has_fallback_evidence: bool = False,
    empty_evidence_note: str = "",
    guards: tuple[GuardFacts, tuple[GuardFamily, ...]] | None = None,
    adjust_verdict: Callable[[LensVerdict, list[CoreClaim]], LensVerdict] | None = None,
) -> AgentMessage:
    """Execute the lens reasoning pipeline and return an AgentMessage.

    After the LLM verdict: the lens's own deterministic axis rules (``adjust_verdict``,
    given the claims the lens was shown) → literature citation step → evidence-computed confidence →
    one bounded repair of prose the checks flag (`_lens_repair.py`) → the checks
    (numeric grounding, fact checks and, when given, the lens's ``guards`` families)
    annotate and flag the final prose.
    """
    spec = msg.task_spec or {}
    target_gene = spec.get("target_gene", "unknown")
    disease = spec.get("disease", "unknown")
    direction = spec.get("direction") or "unspecified"
    disease_classes = frozenset(spec.get("disease_classes") or ())

    all_claims = _deserialise_claims(spec.get("extracted_claims") or [])
    relevant = _filter_claims(all_claims, evidence_types, lens)
    quality_map = spec.get("source_quality") or {}

    if guard_empty and not relevant and not has_fallback_evidence:
        verdict = _insufficient_verdict(
            lens,
            target_gene,
            disease,
            _direction_enum(direction),
            msg.run_id,
            msg.trace_id,
            rationale=(
                "No evidence of this lens's type passed screening; "
                "verdict reflects the evidence gap, not a negative finding."
            ),
            narrative=empty_evidence_note,
        )
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"lens_verdicts": [verdict.model_dump(mode="json")]},
            trace_id=msg.trace_id,
        )

    skill_text = ctx.load_skill(skill_name)
    # classify() only reads `.classification` (defined on the shared CoreClaim base) off
    # each list item, so a list of CoreClaim is safe here at runtime even though the
    # signature is typed for the narrower Evidence subtype.
    classification = (
        classify(cast("list[Evidence]", relevant)) if relevant else DataClass.NON_SENSITIVE
    )
    provider, _model = ctx.select_model(classification, f"{lens}_lens")

    ledger = (
        _build_evidence_ledger(relevant, quality_map, disease_classes, lens) if relevant else ""
    )
    task = f"{lens}_lens"
    budget = prompt_budget(provider, task)
    # The fixed blocks get at most FIXED_SHARE of the budget; the claims keep the rest.
    (direction_text, context_shown, ledger_shown), trimmed = fit_fixed_blocks(
        [_block(spec.get("direction_reading_text")), extra_context, ledger],
        int(FIXED_SHARE * budget) if budget is not None else None,
        task,
    )
    if trimmed:
        logger.info("%s_lens: fixed prompt blocks over budget — %s", lens, "; ".join(trimmed))
    ledger_block = f"{ledger_shown}\n\n" if ledger_shown else ""
    head = (
        f"Gene: {target_gene}\n"
        f"Disease: {disease}\n"
        f"Therapeutic direction: {direction}\n"
        f"{missing_inputs_note(list(spec.get('missing_inputs') or []))}"
        f"{direction_text}"
        f"{context_shown}"
        f"\n{ledger_block}"
    )
    tail = f"\n\nReturn the {lens} lens verdict JSON object."

    papers = as_literature_index(spec.get("literature_index"))
    relevance = relevance_from_spec(lens, spec, relevant, papers)

    def _render(n: int) -> str:
        return (
            _claims_to_json(relevant, quality_map, disease_classes, lens, n, papers, relevance)
            if relevant
            else "[]"
        )

    claim_limit = fit_claim_limit(
        estimate_tokens(skill_text + head + tail, task), budget, _render, len(relevant), task
    )

    def _unshown_decisive(limit: int) -> list[str]:
        if not relevance.pinned:
            return []
        shown_ids = {
            str(c.evidence_id)
            for c in _shown_claims(
                relevant, quality_map, disease_classes, lens, limit, papers, relevance
            )
        }
        return sorted(relevance.pinned - shown_ids)

    def _prompt(limit: int) -> tuple[str, int]:
        unshown = _unshown_decisive(limit)
        listed = (
            f"\nDecisive records not shown for space (approval in the indication, boxed "
            f"warning, stopped late-phase trial or curated Mendelian record), by claim id: "
            f"{', '.join(unshown)}"
            if unshown
            else ""
        )
        content = f"{head}Relevant claims ({len(relevant)}):\n{_render(limit)}{listed}{tail}"
        return content, estimate_tokens(skill_text + content, task)

    user_content, prompt_tokens = _prompt(claim_limit)
    if claim_limit < min(_max_claims(), len(relevant)):
        logger.info(
            "%s_lens: showing the %d strongest of %d claims to fit the prompt budget (%s tokens)",
            lens,
            claim_limit,
            len(relevant),
            budget,
        )
    if budget is not None and prompt_tokens > budget:
        logger.warning(
            "%s_lens: prompt ~%d tokens exceeds the budget of %d even with %d claims; "
            "the model may not see its instructions",
            lens,
            prompt_tokens,
            budget,
            claim_limit,
        )

    def _request(messages: list[dict[str, Any]], tokens_in: int) -> CompletionRequest:
        return CompletionRequest(
            messages=messages,
            system=skill_text,
            classification=classification,
            task=task,
            model_override=_model,
            # The verdict JSON is about 1.5K tokens; never more than the window has left.
            max_tokens=reply_room(provider, task, tokens_in, _OUTPUT_RESERVE_TOKENS),
        )

    completion = await provider.complete(
        _request([{"role": "user", "content": user_content}], prompt_tokens)
    )
    # Fill guard: a prompt plus reply that reached the window lost its oldest tokens (the
    # instructions) or its reply's end. Re-fit once with fewer claims.
    window = context_window(provider, task)
    window_filled = bool(
        window and (completion.input_tokens or 0) + (completion.output_tokens or 0) >= window
    )
    if window_filled:
        shown_before = min(claim_limit, len(relevant))
        claim_limit = max(min(_MIN_PROMPT_CLAIMS, shown_before), int(shown_before * 0.8))
        user_content, prompt_tokens = _prompt(claim_limit)
        logger.warning(
            "%s_lens: prompt and reply filled the %s-token window — retrying with %d claims",
            lens,
            window,
            claim_limit,
        )
        completion = await provider.complete(
            _request([{"role": "user", "content": user_content}], prompt_tokens)
        )

    def _parse(raw: str) -> LensVerdict:
        return _parse_verdict(
            raw,
            lens=lens,
            target_gene=target_gene,
            disease=disease,
            direction=direction,
            run_id=msg.run_id,
            trace_id=msg.trace_id,
        )

    verdict = _parse(completion.content)
    if _unparsed(verdict) and (completion.content or "").strip():
        # One bounded retry: the model reasoned, then broke the JSON (a 7B slip the
        # recovering parser cannot mend). Without it the whole lens was lost.
        logger.warning(
            "%s_lens: unparseable reply (%d chars: %r…) — asking once for valid JSON",
            lens,
            len(completion.content),
            completion.content[:200],
        )
        retry = await provider.complete(
            _request(
                [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": completion.content},
                    {"role": "user", "content": _REFORMAT_REQUEST},
                ],
                prompt_tokens + estimate_tokens(completion.content or "", task),
            )
        )
        verdict = _parse(retry.content)
        if _unparsed(verdict):
            logger.warning(
                "%s_lens: retry also unparseable (%r…)", lens, (retry.content or "")[:200]
            )
    shown = _shown_claims(
        relevant, quality_map, disease_classes, lens, claim_limit, papers, relevance
    )
    verdict = await finish_verdict(
        verdict,
        shown,
        spec=spec,
        extra_context=extra_context,
        ctx=ctx,
        classification=classification,
        lens=lens,
        guards=guards,
        adjust_verdict=adjust_verdict,
    )
    update: dict[str, Any] = {
        "shown_inputs": shown_input_counts(shown, lens),
        "routed_inputs": shown_input_counts(relevant, lens),
    }
    flags = list(verdict.validation_flags)
    unshown = _unshown_decisive(claim_limit)
    if unshown:
        flags.append(
            ValidationFlag(
                lens=lens,
                severity="medium",
                rule_id="coverage.decisive_not_shown",
                claim_excerpt="",
                message=(
                    f"{len(unshown)} decisive claim(s) routed to the lens did not fit its "
                    "slots and were listed by id only."
                ),
            )
        )
        update["validation_flags"] = flags
    if window_filled:
        update["validation_flags"] = [
            *flags,
            ValidationFlag(
                lens=lens,
                severity="low",
                rule_id="prompt.window_filled",
                claim_excerpt="",
                message=(
                    f"The first call's prompt and reply filled the {window}-token context "
                    f"window; the lens was re-asked with {claim_limit} claims."
                ),
            ),
        ]
    verdict = verdict.model_copy(update=update)

    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=msg.run_id,
        from_agent=msg.to_agent,
        to_agent=msg.from_agent,
        intent="result",
        payload={"lens_verdicts": [verdict.model_dump(mode="json")]},
        trace_id=msg.trace_id,
    )


async def finish_verdict(
    verdict: LensVerdict,
    shown: list[CoreClaim],
    *,
    spec: dict[str, Any],
    extra_context: str,
    ctx: RunContext,
    classification: DataClass,
    lens: LensName,
    guards: tuple[GuardFacts, tuple[GuardFamily, ...]] | None = None,
    adjust_verdict: Callable[[LensVerdict, list[CoreClaim]], LensVerdict] | None = None,
) -> LensVerdict:
    """What every lens does to a verdict once it exists, however it was produced.

    The lens's own deterministic axis rules (``adjust_verdict``, given the claims the
    lens was shown) → the literature citation step → evidence-computed confidence → one
    bounded repair of prose the checks flag (`_lens_repair.py`) → the checks (numeric
    grounding, fact checks and, when given, the ``guards`` families) annotate and flag
    the final prose. Shared by the single-call path (`run_lens`) and the safety lens, so
    a verdict computed in code meets exactly the same checks.

    ``shown`` must be the claims the lens (or its questions) displayed: a cited id is
    scored only if it was shown.
    """
    quality_map = spec.get("source_quality") or {}
    disease_classes = frozenset(spec.get("disease_classes") or ())
    target_gene = spec.get("target_gene", "unknown")
    disease = spec.get("disease", "unknown")
    if adjust_verdict is not None:
        verdict = adjust_verdict(verdict, shown)
    # The literature behind each axis verdict, picked in a narrow follow-up call.
    verdict = await add_literature_support(verdict, shown, ctx, classification, f"{lens}_lens")
    # Confidence is computed from the supporting evidence, not taken from the LLM.
    verdict = score_verdict(
        verdict,
        shown,
        lambda c: _claim_weight(c, quality_map, disease_classes, lens),
        source_groups=spec.get("source_groups"),
        context=extra_context,
        run_checks=False,
    )

    def _find(text: str) -> list[Violation]:
        found = prose_violations(text, shown, extra_context, target_gene, disease)
        if guards is not None:
            found += guard_violations(text, guards[0], guards[1])
        return found

    if repair_enabled():
        verdict = await repair_prose(
            verdict,
            find=_find,
            pool=shown,
            context=extra_context,
            ctx=ctx,
            classification=classification,
            task=f"{lens}_lens",
        )
    verdict = check_prose(verdict, shown, extra_context)
    if guards is not None:
        verdict = apply_guards(verdict, guards[0], guards[1], lens=lens)
    return flag_axis_rationale_conflicts(align_verdict_statements(verdict))


# An axis rationale that affirms its question, or denies it. Denials are read first so
# "does not support" is not taken for "supports".
_DENIES = re.compile(
    r"\b(?:no|not|never|without|lack(?:s|ing)?|absence|absent|insufficient|limited|"
    r"weak|unclear|uncertain|inconclusive|fails?|failed)\b",
    re.IGNORECASE,
)
_AFFIRMS = re.compile(
    r"\b(?:strong(?:ly)?|robust|clear(?:ly)?|convincing|supports?|supported|confirms?|"
    r"confirmed|establish(?:es|ed)?|demonstrat(?:es|ed)|validat(?:es|ed)|favourable|favorable)\b",
    re.IGNORECASE,
)


def _rationale_stance(rationale: str) -> str | None:
    """ "affirms", "denies", or None (mixed or neither), sentence by sentence."""
    affirms = denies = False
    for sentence in _SENTENCE_SPLIT.split(rationale or ""):
        if _DENIES.search(sentence):
            denies = True
        elif _AFFIRMS.search(sentence):
            affirms = True
    if affirms == denies:
        return None
    return "affirms" if affirms else "denies"


# Axes that ask for positive evidence ("is there evidence of causality?"). Harm-phrased
# axes (toxicity, label safety, constraint, de-risking, IP) are favourable when the
# rationale reports an absence ("no boxed warning"), so negation does not deny them.
_EVIDENCE_AXES = frozenset(
    {
        "causality",
        "mechanism_of_action",
        "clinical_validation",
        "clinical_precedent",
        "approval_precedent",
        "competitive_opportunity",
        "market_size",
        "druggability",
        "developability",
    }
)


def flag_axis_rationale_conflicts(verdict: LensVerdict) -> LensVerdict:
    """Flag axes whose rationale affirms the axis while the value is null or false, or
    denies it while true. The value is not changed from the text: with verdicts computed
    from axes, a null causality beside "strong evidence of causality" decided a verdict,
    and the flag measures how often that happens."""
    flags: list[ValidationFlag] = []
    for axis in verdict.axes:
        if axis.axis not in _EVIDENCE_AXES:
            continue
        stance = _rationale_stance(axis.rationale)
        if (stance == "affirms" and axis.verdict is not True) or (
            stance == "denies" and axis.verdict is True
        ):
            flags.append(
                ValidationFlag(
                    lens=verdict.lens,
                    severity="low",
                    rule_id="axis_rationale_conflict",
                    claim_excerpt=axis.rationale[:300],
                    message=f"Axis {axis.axis} is {axis.verdict} but its rationale {stance} it.",
                )
            )
    if not flags:
        return verdict
    return verdict.model_copy(update={"validation_flags": [*verdict.validation_flags, *flags]})


# A sentence that states the lens's overall verdict: "The overall verdict is support",
# "verdict: oppose", "this lens opposes".
_VERDICT_STATEMENT = re.compile(
    r"\b(?:overall\s+)?verdict\s+(?:is|was|of|=|:)\s*[\"'“]?"
    r"(support|oppose|neutral|insufficient(?:[\s_]evidence)?)\b"
    r"|\b(?:this|the)\s+(?:\w+\s+)?lens\s+(supports|opposes|is\s+neutral)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _stated_verdict(sentence: str) -> str | None:
    m = _VERDICT_STATEMENT.search(sentence)
    if not m:
        return None
    word = (m.group(1) or m.group(2) or "").lower()
    if word.startswith("insufficient"):
        return "insufficient_evidence"
    if word.startswith("support"):
        return "support"
    if word.startswith("oppos"):
        return "oppose"
    return "neutral"


def align_verdict_statements(verdict: LensVerdict) -> LensVerdict:
    """Replace prose sentences that state an overall verdict other than the final one.

    The narrative is written before the deterministic rules run, so a rule that changes
    the verdict (axes computed in code, precedent, a common-essential gene) left prose
    saying "The overall verdict is support" beside a neutral verdict. Each such sentence
    becomes a plain statement of the final verdict, with a flag keeping the original.
    """
    final = verdict.overall_verdict
    replacement = f"The overall verdict is {final.replace('_', ' ')}."
    flags: list[ValidationFlag] = []
    texts: dict[str, str] = {}
    for field in ("rationale", "narrative"):
        text = getattr(verdict, field) or ""
        changed = False
        paragraphs = []
        for paragraph in text.split("\n"):
            sentences = _SENTENCE_SPLIT.split(paragraph)
            for i, sentence in enumerate(sentences):
                stated = _stated_verdict(sentence)
                if stated is not None and stated != final:
                    flags.append(
                        ValidationFlag(
                            lens=verdict.lens,
                            severity="low",
                            rule_id="narrative.verdict_aligned",
                            claim_excerpt=sentence[:300],
                            message=f"Sentence stated verdict {stated}; the final verdict is {final}.",
                        )
                    )
                    sentences[i] = replacement
                    changed = True
            paragraphs.append(" ".join(sentences))
        if changed:
            texts[field] = "\n".join(paragraphs)
    if not flags:
        return verdict
    return verdict.model_copy(
        update={**texts, "validation_flags": [*verdict.validation_flags, *flags]}
    )


def refresh_derived(before: LensVerdict, after: LensVerdict) -> LensVerdict:
    """``after`` with what was derived from the verdict brought in line with it, after a
    deterministic rule changed the verdict or its axes.

    The flags, the prose and the "contradicts every axis" check were computed on the
    model's verdict. A rule that set the verdict from the axes left a stale
    `verdict_axis_conflict` flag beside a verdict that now agrees with every axis, and a
    model summary arguing the verdict it no longer has. The conflict flag is recomputed
    on the final verdict. Sentences stating another verdict are aligned. When the verdict
    changed, the model's own verdict is kept in ``model_verdict`` so the reports can say
    whose reading the summary is.
    """
    flags = [f for f in after.validation_flags if f.rule_id != "verdict_axis_conflict"]
    if contradicts_axes(after):
        flags.append(axis_conflict_flag(after))
    model_verdict = after.model_verdict or before.model_verdict
    if model_verdict is None and after.overall_verdict != before.overall_verdict:
        model_verdict = before.overall_verdict
    if model_verdict == after.overall_verdict:
        model_verdict = None
    return align_verdict_statements(
        after.model_copy(update={"validation_flags": flags, "model_verdict": model_verdict})
    )


def map_lens_verdict(
    result: AgentMessage, fn: Callable[[LensVerdict], LensVerdict]
) -> AgentMessage:
    """Apply a deterministic post-LLM rule to the verdict a lens AgentMessage carries,
    then refresh what was derived from the verdict (`refresh_derived`)."""
    if not isinstance(result.payload, dict):
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result
    before = LensVerdict.model_validate(verdicts[0])
    after = fn(before)
    if after is before:
        return result
    after = refresh_derived(before, after)
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=result.run_id,
        from_agent=result.from_agent,
        to_agent=result.to_agent,
        intent=result.intent,
        payload={"lens_verdicts": [after.model_dump(mode="json")]},
        trace_id=result.trace_id,
    )


def _block(text: Any) -> str:
    """An optional pre-rendered prompt block, newline-terminated, or ""."""
    text = str(text or "").strip()
    return f"{text}\n" if text else ""


def build_guard_facts(
    spec: dict[str, Any],
    *,
    constraint_reading: dict[str, Any] | None = None,
    mendelian_grade: bool = False,
) -> GuardFacts:
    """Assemble the guard fact sheet from a lens task_spec.

    One builder for every lens: each reads the keys its workflow node populates and
    ignores the rest, so a lens that does not receive e.g. trial facts simply leaves
    that family unarmed (reported as "not evaluated").

    The dependency reading comes from ``dependency_reading`` (DepMap merged with the
    Project Score fallback by the workflow); specs carrying only the flat ``depmap_*``
    line counts are converted with the same builder.
    """
    dependency: DependencyReading | None = None
    if spec.get("dependency_reading"):
        dependency = DependencyReading.model_validate(spec["dependency_reading"])
    else:
        # Real line counts only — a fraction alone is not enough to label a reading.
        dependency = build_dependency_reading(
            {
                "num_dependent_lines": spec.get("depmap_num_dependent_lines"),
                "total_lines": spec.get("depmap_total_lines"),
                "gene_effect_mean": spec.get("depmap_mean_chronos"),
                "is_common_essential": spec.get("depmap_is_common_essential", False),
            },
            None,
        )
    trial_facts = [TrialFact.model_validate(f) for f in spec.get("trial_facts") or []]
    return GuardFacts(
        gene=spec.get("target_gene") or "",
        disease=spec.get("disease") or "",
        constraint=ConstraintReading.model_validate(constraint_reading)
        if constraint_reading
        else None,
        mendelian_grade=mendelian_grade,
        gwas_traits_in_scope=int(
            (spec.get("floor_signals") or {}).get("gwas_traits_in_scope") or 0
        ),
        trial_facts=trial_facts,
        trial_values=trial_reported_values(
            trial_facts,
            [
                (str(c.get("source_evidence_id") or ""), str(c.get("claim_text") or ""))
                for c in spec.get("extracted_claims") or []
            ],
        )
        if trial_facts
        else None,
        known_drugs_count=spec.get("ot_known_drugs_count") or 0,
        approved_count=spec.get("ot_known_drugs_approved_count") or 0,
        indication_approved_drug_count=spec.get("indication_approved_drug_count") or 0,
        indication_active_trial_count=spec.get("indication_active_trial_count") or 0,
        indication_prevalence_found=bool(spec.get("indication_prevalence_found", True)),
        top_tissues=spec.get("top_tpm_tissues") or [],
        disease_relevant_tissues=spec.get("disease_relevant_tissues") or [],
        dependency=dependency,
        is_oncology=bool(spec.get("is_oncology_indication", False)),
        mouse_phenotypes=(
            list(spec["ot_mouse_phenotype_labels"])
            if spec.get("ot_mouse_phenotype_labels") is not None
            else None
        ),
        liability_phenotypes=(
            list(spec["liability_mouse_phenotypes"])
            if spec.get("liability_mouse_phenotypes") is not None
            else None
        ),
        tissue_ranks=dict(spec.get("gtex_tissue_ranks") or {}),
        hpa_specificity=str(spec.get("hpa_specificity") or ""),
        drug_approval=known_drug_approval(spec.get("ot_known_drugs") or []),
        genetic_favours_decrease=_genetic_side(spec, "decrease"),
        genetic_favours_increase=_genetic_side(spec, "increase"),
        cell_types=list(spec.get("disease_cell_types") or []),
        cell_types_reported=cell_types_reported(
            list(spec.get("disease_cell_types") or []),
            [str(c.get("claim_text") or "") for c in spec.get("extracted_claims") or []],
        )
        if spec.get("disease_cell_types")
        else None,
    )


def _genetic_side(spec: dict[str, Any], side: str) -> float | None:
    """The human-genetics direction reading's strength favouring ``side`` (lowering:
    "decrease", raising: "increase"), from the spec's ``genetic_direction``; None when
    the workflow gave the lens no reading."""
    reading = spec.get("genetic_direction")
    if not isinstance(reading, dict) or reading.get("requested") not in ("decrease", "increase"):
        return None
    for_side = reading["requested"] == side
    return float(reading.get("support" if for_side else "against") or 0.0)


def known_drug_approval(drugs: list[dict[str, Any]]) -> dict[str, bool]:
    """Drug name → approved in any row (Open Targets lists a drug once per indication)."""
    out: dict[str, bool] = {}
    for d in drugs:
        name = str(d.get("drug_name") or "").strip().lower()
        if name:
            out[name] = out.get(name, False) or bool(d.get("is_approved"))
    return out


def guard_violations(
    text: str, facts: GuardFacts, families: tuple[GuardFamily, ...]
) -> list[Violation]:
    """Every violation the given guard families find in ``text`` (no side effects)."""
    found: list[Violation] = []
    for family in families:
        rule = GUARD_RULES[family]
        if text and rule.is_armed(facts):
            found.extend(rule.check(text, facts))
    return found


# Confidence multiplier for an axis whose supporting prose a guard flagged (same as a
# failed fact check: the axis rests on an omitted or false premise).
GUARD_AXIS_PENALTY = 0.6


def apply_guards(
    verdict: LensVerdict,
    facts: GuardFacts,
    families: tuple[GuardFamily, ...],
    *,
    lens: LensName,
) -> LensVerdict:
    """Post-LLM safety net shared by every lens: run guard families over the verdict.

    For each family in ``families``, checks the rationale, narrative and every axis
    rationale against ``facts``, appends the violation notes (annotate — never
    silently rewrite), records one ValidationFlag per fired family for Langfuse/HITL
    audit, and records a GuardCheck — passed, flagged, or not evaluated when the
    family's inputs are missing — so the report shows which checks actually ran.
    Terminology-only findings (right meaning, misused term) are low severity.
    (Sentences a guard flags may first be rewritten by the lens repair step —
    `_lens_repair.py` — which records the original; the guards then run on the result.)
    """
    rationale, narrative, axes = verdict.rationale, verdict.narrative, list(verdict.axes)
    flags = list(verdict.validation_flags)
    checks = list(verdict.guard_checks)
    flagged: list[GuardFamily] = []

    for family in families:
        rule = GUARD_RULES[family]
        if not rule.is_armed(facts):
            checks.append(
                GuardCheck(family=family, status="not_evaluated", detail=rule.missing_inputs)
            )
            continue

        fired: list[Violation] = []

        def _guard(text: str, rule: GuardRule = rule, fired: list[Violation] = fired) -> str:
            found = rule.check(text, facts) if text else []
            fired.extend(found)
            return annotate(text, found)

        rationale = _guard(rationale)
        narrative = _guard(narrative)
        axes = [ax.model_copy(update={"rationale": _guard(ax.rationale)}) for ax in axes]

        if not fired:
            checks.append(GuardCheck(family=family, status="passed"))
            continue
        flagged.append(family)
        rule_ids = ", ".join(sorted({v.rule_id for v in fired}))
        checks.append(GuardCheck(family=family, status="flagged", detail=rule_ids))
        flags.append(
            ValidationFlag(
                lens=lens,
                severity="low" if all(v.kind == "terminology" for v in fired) else "medium",
                rule_id=rule.flag_rule_id,
                claim_excerpt=fired[0].excerpt[:300],
                message=rule.flag_message,
            )
        )

    penalised = {axis for family in flagged for axis in GUARD_RULES[family].penalised_axes}
    if penalised:
        axes = [
            ax.model_copy(update={"confidence": round(ax.confidence * GUARD_AXIS_PENALTY, 3)})
            if ax.axis in penalised and ax.confidence is not None
            else ax
            for ax in axes
        ]
    updated = verdict.model_copy(
        update={
            "rationale": rationale,
            "narrative": narrative,
            "axes": axes,
            "validation_flags": flags,
            "guard_checks": checks,
        }
    )
    return reaggregate(updated) if penalised & {a.axis for a in axes} else updated


def apply_guards_to_result(
    result: AgentMessage,
    facts: GuardFacts,
    families: tuple[GuardFamily, ...],
    *,
    lens: LensName,
) -> AgentMessage:
    """`apply_guards` over the verdict carried by a lens AgentMessage."""
    if not isinstance(result.payload, dict):
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result
    updated = apply_guards(LensVerdict.model_validate(verdicts[0]), facts, families, lens=lens)
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=result.run_id,
        from_agent=result.from_agent,
        to_agent=result.to_agent,
        intent=result.intent,
        payload={"lens_verdicts": [updated.model_dump(mode="json")]},
        trace_id=result.trace_id,
    )
