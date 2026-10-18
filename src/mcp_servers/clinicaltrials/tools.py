# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalTrials.gov tools using the v2 API.

API docs: https://clinicaltrials.gov/data-api/api
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from pydantic import BaseModel

from core.evidence_text import HARD_ENDPOINT
from core.exceptions import MCPToolError

_CT_BASE = "https://clinicaltrials.gov/api/v2/studies"


class ParticipationCriteria(BaseModel):
    sex: str = ""
    minimum_age: str = ""
    maximum_age: str = ""
    healthy_volunteers: str = ""
    eligibility_criteria: str = ""


class StudyPlan(BaseModel):
    study_type: str = ""
    allocation: str = ""
    intervention_model: str = ""
    intervention_model_description: str = ""
    primary_purpose: str = ""
    masking: str = ""
    number_of_arms: int | None = None


class StudyRecordDates(BaseModel):
    first_submit_date: str = ""
    first_post_date: str = ""
    last_update_submit_date: str = ""
    last_update_post_date: str = ""


class OutcomeValue(BaseModel):
    group: str  # arm title, e.g. "BI 764198 20 mg" / "Placebo"
    value: str
    lower: str = ""  # dispersion / CI bounds as reported
    upper: str = ""


class OutcomeAnalysis(BaseModel):
    groups: list[str] = []  # arm titles compared
    param_type: str = ""  # e.g. "Odds Ratio (OR)", "Relative difference"
    param_value: str = ""
    ci_lower: str = ""
    ci_upper: str = ""
    p_value: str = ""
    method: str = ""


class OutcomeResult(BaseModel):
    """One posted outcome measure (a primary one, or a secondary on a hard endpoint):
    per-arm values and the statistical analyses."""

    title: str
    kind: str = "primary"  # "primary" or "secondary"
    time_frame: str = ""
    unit: str = ""
    param_type: str = ""  # e.g. "LEAST_SQUARES_MEAN", "NUMBER"
    values: list[OutcomeValue] = []
    analyses: list[OutcomeAnalysis] = []

    def summary(self) -> str:
        """One line: the measure, each arm's value (with its reported bounds) and each
        analysis's effect estimate and p-value — the posted numbers, verbatim."""

        def _bounds(lo: str, hi: str) -> str:
            return f" ({lo} to {hi})" if lo and hi else ""

        arms = "; ".join(f"{v.group} {v.value}{_bounds(v.lower, v.upper)}" for v in self.values)
        unit = f" [{self.unit}]" if self.unit else ""
        text = f"{self.title}{unit}: {arms}" if arms else self.title
        for a in self.analyses:
            vs = " vs ".join(a.groups)
            stat = f"{a.param_type} {a.param_value}".strip()
            ci = _bounds(a.ci_lower, a.ci_upper)
            p = f", p={a.p_value}" if a.p_value else ""
            if stat or p:
                text += f"; {vs}: {stat}{ci}{p}".rstrip()
        return text


class ArmEvents(BaseModel):
    """All-cause deaths and serious adverse events in one arm, as the registry's
    adverse-events module posts them (counts only; the registry reports no test)."""

    group: str
    deaths: int | None = None
    deaths_at_risk: int | None = None
    serious: int | None = None
    serious_at_risk: int | None = None


class TrialResults(BaseModel):
    outcomes: list[OutcomeResult] = []
    arm_events: list[ArmEvents] = []


class TrialRecord(BaseModel):
    nct_id: str
    title: str
    status: str = ""
    phase: str = ""
    conditions: list[str] = []
    interventions: list[str] = []
    enrollment: int | None = None
    start_date: str = ""
    # scope mirrors Evidence.scope — "abstract" when no results are posted
    scope: str = "abstract"
    brief_summary: str = ""
    sponsor: str = ""
    design_details: str = ""
    participation_criteria: ParticipationCriteria = ParticipationCriteria()
    study_plan: StudyPlan = StudyPlan()
    record_dates: StudyRecordDates = StudyRecordDates()
    # Posted primary-outcome results and hard-endpoint secondaries (empty when none are
    # posted or the fetch failed), and per-arm deaths and serious adverse events.
    results: list[OutcomeResult] = []
    arm_events: list[ArmEvents] = []
    # Registry "otherNames" of the interventions (brand names, sponsor codes).
    intervention_aliases: list[str] = []
    # Set by search_trials_for_drugs: the drug query that returned the record, and whether
    # that query was restricted to the run's disease.
    query_drug: str = ""
    query_in_disease: bool = False


class ConditionTrialLandscape(BaseModel):
    condition: str
    total_count: int = 0
    active_count: int = 0
    phase3_count: int = 0
    recruiting_count: int = 0
    mapping: str = "cond"  # "cond" | "none"
    source_link: str = ""
    text: str = ""


_ACTIVE_STATUSES = "RECRUITING,ACTIVE_NOT_RECRUITING,ENROLLING_BY_INVITATION,NOT_YET_RECRUITING"


async def _count_query(client: httpx.AsyncClient, condition: str, **extra: str) -> int:
    """Issue a count-only query (pageSize=1, countTotal=true) and read totalCount.

    Never pages — a condition-only query can return tens of thousands of studies,
    far more than the `_MAX_TRIALS` cap `search_trials` enforces for full records.
    """
    params: dict[str, Any] = {
        "query.cond": condition,
        "countTotal": "true",
        "pageSize": "1",
        "fields": "NCTId",
        **extra,
    }
    resp = await client.get(_CT_BASE, params=params)
    if resp.status_code != 200:
        raise MCPToolError(f"ClinicalTrials.gov API returned HTTP {resp.status_code}")
    return int(resp.json().get("totalCount", 0))


async def count_condition_trials(condition: str) -> ConditionTrialLandscape:
    """Count trials for a disease/condition, regardless of target gene or intervention.

    Target-agnostic disease-level trial landscape (contrast with `search_trials`,
    which is gene-keyed). Issues four count-only queries in parallel
    (`countTotal=true`, `pageSize=1`) instead of paging full study records, since a
    condition-only query can return far more studies than `_MAX_TRIALS` allows.
    """
    src_link = f"{_CT_BASE}?query.cond={condition}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        total, active, recruiting, phase3 = await asyncio.gather(
            _count_query(client, condition),
            _count_query(client, condition, **{"filter.overallStatus": _ACTIVE_STATUSES}),
            _count_query(client, condition, **{"filter.overallStatus": "RECRUITING"}),
            _count_query(client, condition, aggFilters="phase:3"),
        )

    if total == 0:
        return ConditionTrialLandscape(condition=condition, mapping="none", source_link=src_link)

    return ConditionTrialLandscape(
        condition=condition,
        total_count=total,
        active_count=active,
        phase3_count=phase3,
        recruiting_count=recruiting,
        mapping="cond",
        source_link=src_link,
        text=f"{condition} (ClinicalTrials.gov): {total} trials, {active} active, "
        f"{phase3} in Phase 3.",
    )


_MAX_TRIALS = 1000
_PAGE_SIZE = 200
_FIELDS = (
    "NCTId,BriefTitle,OverallStatus,Phase,"
    "Condition,InterventionName,EnrollmentCount,StartDate,HasResults,"
    "BriefSummary,DetailedDescription,LeadSponsorName,"
    "EligibilityCriteria,Sex,MinimumAge,MaximumAge,HealthyVolunteers,"
    "StudyType,DesignAllocation,DesignInterventionModel,"
    "DesignInterventionModelDescription,DesignPrimaryPurpose,"
    "DesignMasking,"
    "StudyFirstSubmitDate,StudyFirstPostDate,"
    "LastUpdateSubmitDate,LastUpdatePostDate"
)


_MAX_PRIMARY_OUTCOMES = 3
_MAX_SECONDARY_OUTCOMES = 3
# Bump when what a trial's posted results hold changes: cached trial rows of an older
# version have their results fetched again. 2: hard-endpoint secondaries, deaths per arm.
RESULTS_VERSION = 2

# Secondary outcomes worth keeping: the hard endpoints (core.evidence_text).


def _parse_outcome(om: dict[str, Any], kind: str) -> OutcomeResult:
    groups = {g.get("id", ""): g.get("title", "") for g in om.get("groups") or []}
    values: list[OutcomeValue] = []
    classes = om.get("classes") or []
    categories = (classes[0].get("categories") or []) if classes else []
    for m in (categories[0].get("measurements") or []) if categories else []:
        values.append(
            OutcomeValue(
                group=groups.get(m.get("groupId", ""), m.get("groupId", "")),
                value=str(m.get("value", "")),
                lower=str(m.get("lowerLimit", "")),
                upper=str(m.get("upperLimit", "")),
            )
        )
    analyses = [
        OutcomeAnalysis(
            groups=[groups.get(g, g) for g in a.get("groupIds") or []],
            param_type=str(a.get("paramType", "")),
            param_value=str(a.get("paramValue", "")),
            ci_lower=str(a.get("ciLowerLimit", "")),
            ci_upper=str(a.get("ciUpperLimit", "")),
            p_value=str(a.get("pValue", "")),
            method=str(a.get("statisticalMethod", "")),
        )
        for a in om.get("analyses") or []
    ]
    return OutcomeResult(
        title=str(om.get("title", "")),
        kind=kind,
        time_frame=str(om.get("timeFrame", "")),
        unit=str(om.get("unitOfMeasure", "")),
        param_type=str(om.get("paramType", "")),
        values=values,
        analyses=analyses,
    )


def _parse_primary_outcomes(study: dict[str, Any]) -> list[OutcomeResult]:
    """Primary outcome measures from a v2 ``resultsSection`` (first class/category)."""
    return [o for o in _parse_outcomes(study) if o.kind == "primary"]


def _parse_outcomes(study: dict[str, Any]) -> list[OutcomeResult]:
    """Primary outcome measures, then secondaries on a hard endpoint (`HARD_ENDPOINT`)."""
    module = (study.get("resultsSection") or {}).get("outcomeMeasuresModule") or {}
    primary: list[OutcomeResult] = []
    secondary: list[OutcomeResult] = []
    for om in module.get("outcomeMeasures") or []:
        if om.get("type") == "PRIMARY" and len(primary) < _MAX_PRIMARY_OUTCOMES:
            primary.append(_parse_outcome(om, "primary"))
        elif (
            om.get("type") == "SECONDARY"
            and len(secondary) < _MAX_SECONDARY_OUTCOMES
            and HARD_ENDPOINT.search(str(om.get("title", "")))
        ):
            secondary.append(_parse_outcome(om, "secondary"))
    return primary + secondary


def _count(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _parse_arm_events(study: dict[str, Any]) -> list[ArmEvents]:
    """Per-arm all-cause deaths and serious adverse events from ``adverseEventsModule``."""
    module = (study.get("resultsSection") or {}).get("adverseEventsModule") or {}
    return [
        ArmEvents(
            group=str(g.get("title", "")),
            deaths=_count(g.get("deathsNumAffected")),
            deaths_at_risk=_count(g.get("deathsNumAtRisk")),
            serious=_count(g.get("seriousNumAffected")),
            serious_at_risk=_count(g.get("seriousNumAtRisk")),
        )
        for g in module.get("eventGroups") or []
        if g.get("title")
    ]


_RESULT_FIELDS = (
    "resultsSection.outcomeMeasuresModule,resultsSection.adverseEventsModule.eventGroups"
)


async def fetch_results(client: httpx.AsyncClient, nct_id: str) -> TrialResults | None:
    """Posted outcomes and per-arm events for one study; None on any failure (so a
    caller can tell "no results" from "could not fetch")."""
    try:
        resp = await client.get(
            f"{_CT_BASE}/{nct_id}", params={"fields": _RESULT_FIELDS, "format": "json"}
        )
        if resp.status_code != 200:
            return None
        study = resp.json() or {}
        return TrialResults(outcomes=_parse_outcomes(study), arm_events=_parse_arm_events(study))
    except Exception:  # optional enrichment: any failure leaves the trial as it was
        return None


async def fetch_trial_results(client: httpx.AsyncClient, nct_id: str) -> list[OutcomeResult]:
    """Posted outcome results for one study; [] when none or on any failure.

    The search query returns only ``HasResults``; the results section is large, so it
    is fetched per study, only for studies that have posted results.
    """
    results = await fetch_results(client, nct_id)
    return results.outcomes if results else []


# Retry backoff (tests replace it with a no-op).
_backoff_sleep = asyncio.sleep

# ClinicalTrials.gov rate-limits bursts (~50 requests/min) with HTTP 429.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRIES = 4
# Drug-name queries run concurrently; keep the burst well under the rate limit.
_CONCURRENCY = asyncio.Semaphore(4)


async def _get_with_backoff(client: httpx.AsyncClient, params: dict[str, Any]) -> httpx.Response:
    """GET ``/studies`` retrying 429/5xx with exponential backoff (honours Retry-After)."""
    delay = 2.0
    async with _CONCURRENCY:
        for attempt in range(_RETRIES + 1):
            response = await client.get(_CT_BASE, params=params)
            if response.status_code not in _RETRY_STATUSES or attempt == _RETRIES:
                return response
            retry_after = response.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else delay
            await _backoff_sleep(min(wait, 30.0))
            delay *= 2
    return response  # pragma: no cover — loop always returns


async def _fetch_studies(
    client: httpx.AsyncClient, params: dict[str, Any], cap: int
) -> list[dict[str, Any]]:
    """Page through ``/studies`` until ``cap`` studies or the last page."""
    studies: list[dict[str, Any]] = []
    params = {**params, "pageSize": min(_PAGE_SIZE, cap), "format": "json", "fields": _FIELDS}
    while len(studies) < cap:
        response = await _get_with_backoff(client, params)
        if response.status_code != 200:
            raise MCPToolError(f"ClinicalTrials.gov API returned HTTP {response.status_code}")
        data = response.json()
        page = data.get("studies") or []
        studies.extend(page)
        next_token = data.get("nextPageToken")
        if not next_token or not page:
            break
        params = {**params, "pageToken": next_token}
    return studies[:cap]


async def _with_results(
    client: httpx.AsyncClient, studies: list[dict[str, Any]]
) -> dict[str, TrialResults]:
    """Posted outcomes and per-arm events for every study that has posted results."""
    with_results = [
        s.get("protocolSection", {}).get("identificationModule", {}).get("nctId", "")
        for s in studies
        if s.get("hasResults")
    ]
    fetched = await asyncio.gather(*(fetch_results(client, n) for n in with_results))
    return {n: r for n, r in zip(with_results, fetched, strict=True) if r is not None}


async def search_trials(
    gene: str,
    disease: str,
    population: str | None = None,
) -> list[TrialRecord]:
    """Search ClinicalTrials.gov for studies involving the gene and disease."""
    term_parts = [p for p in (gene, disease, population) if p]
    params: dict[str, Any] = {"query.term": " AND ".join(term_parts)}
    async with httpx.AsyncClient(timeout=30.0) as client:
        studies = await _fetch_studies(client, params, _MAX_TRIALS)
        results_by_nct = await _with_results(client, studies)
    return _to_records(studies, results_by_nct)


# Interventions that are never the investigational product of a trial.
_NON_PRODUCT_RE = re.compile(
    r"placebo|vehicle|saline|sham|standard|usual care|best supportive|control\b|"
    r"observation|no intervention|questionnaire|assessment|sampling|blood draw|"
    r"\bdiet\b|exercise|counsel",
    re.IGNORECASE,
)


# Class or generic nouns: "PCSK9 inhibitor", "statin", "chemotherapy" name a category,
# not a product, and querying them by name pulls in unrelated trials.
_CLASS_TERM_RE = re.compile(
    r"\b(?:statins?|inhibitors?|antagonists?|agonists?|blockers?|modulators?|therap(?:y|ies)|"
    r"chemotherapy|treatment|drugs?|medications?|antibod(?:y|ies)|vaccines?|supplements?|"
    r"injections?|tablets?|capsules?|infusions?|regimens?)\b",
    re.IGNORECASE,
)


_TRACER_RE = re.compile(
    r"(?:^|[\[\s(-])(?:18F|11C|123I|124I|125I|99mTc|68Ga|64Cu|89Zr|111In|15O|13N)(?:[\]\s)-]|$)"
    r"|\b(?:tracer|radioligand|radiotracer|PET|SPECT|DaTscan|ioflupane)\b",
    re.IGNORECASE,
)


# WHO INN stems (suffixes) of drug classes: a one-word name without a sponsor code is a
# drug only if it carries one. A plain substance name tested in a disease trial that names
# the gene in its rationale ("trehalose … in a mouse model carrying the GENE mutation")
# is not an investigational product acting on the target.
_INN_STEM_RE = re.compile(
    r"(?:mab|cept|kinra|siran|rsen|nersen|tinib|nib|ciclib|parib|lisib|rafenib|degib|"
    r"glutide|tide|relin|relix|pressin|tocin|sentan|sartan|pril|olol|dipine|vastatin|"
    r"gliptin|gliflozin|xaban|gatran|prazole|setron|pitant|triptan|dronate|fibrate|lutamide|"
    r"stat|vir|buvir|previr|asvir|navir|tegravir|ciclovir|azole|conazole|oxacin|cillin|"
    r"cycline|mycin|penem|afil|lukast|semide|thiazide|zepam|zolam|tidine|profen|caine|"
    r"sone|olone|pine|apine|idone|done|oxetine|tadine|zolid|platin|taxel|rubicin|trexate|"
    r"citabine|mustine|tecan|imod|limus|dopa|formin|mibe|parin|plase|ase|kin|grastim|"
    r"poetin|gene|cel|cogin|dil|kalant|lisib|tide|ast|mod|tant|cin|ine)$",
    re.IGNORECASE,
)


# A sponsor code made of capital letters around a hyphen ("ALN-PCSSC").
_LETTER_CODE_RE = re.compile(r"[A-Z]{2,6}-[A-Z]{2,8}")


def _is_product_name(name: str, gene: str = "") -> bool:
    """A specific product: a sponsor code ("BI 764198", "AZD0780") or a one-word
    non-proprietary name with an INN stem ("evolocumab"), never a class term, a plain
    substance name or the gene itself."""
    if _CLASS_TERM_RE.search(name) or (gene and gene.lower() in name.lower()):
        return False
    if _TRACER_RE.search(name):
        return False  # PET/SPECT imaging agents ("18F-DTBZ", "[18F]MNI-444")
    if any(ch.isdigit() for ch in name) or _LETTER_CODE_RE.fullmatch(name.strip()):
        return True
    return len(name.split()) == 1 and len(name) >= 5 and bool(_INN_STEM_RE.search(name))


def investigational_drugs(
    records: list[TrialRecord],
    *,
    gene: str = "",
    known: list[str] | None = None,
    limit: int = 6,
) -> list[str]:
    """Drug names that are the investigational product of gene-matched trials.

    A record's intervention counts when it is named in the trial's own title (the
    product under test, not a background therapy) and is not a placebo or care
    standard. Names are ranked by how many matched trials test them, so a drug coded
    only by sponsor ID ("BI 764198") is found even when no drug database lists it.
    """
    known_keys = [" ".join(k.lower().split()) for k in known or [] if k]
    counts: dict[str, int] = {}
    paired: dict[str, int] = {}
    display: dict[str, str] = {}
    for r in records:
        title = (r.title or "").lower()
        # A product named next to one of the target's known drugs is usually its
        # comparator or combination partner ("evolocumab vs ezetimibe"), not a drug
        # acting on the target: count those appearances separately.
        with_known = any(k in title for k in known_keys)
        for name in dict.fromkeys(r.interventions):
            key = " ".join(name.lower().split())
            if (
                len(key) < 3
                or _NON_PRODUCT_RE.search(key)
                or key not in title
                or not _is_product_name(key, gene)
            ):
                continue
            if any(key == k or key in k or k in key for k in known_keys):
                continue  # already queried as a known drug
            if with_known:
                paired[key] = paired.get(key, 0) + 1
                continue
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, name.strip())
    ranked = sorted(
        (k for k in counts if counts[k] >= paired.get(k, 0)), key=lambda k: (-counts[k], k)
    )
    return [display[k] for k in ranked[:limit]]


async def search_trials_for_drugs(
    drugs: list[str],
    disease: str,
    *,
    per_drug_disease: int = 15,
    per_drug_any: int = 5,
) -> list[TrialRecord]:
    """Interventional trials of the target's drugs that a gene-keyword search misses.

    Trial records name the drug ("alirocumab", "BI 764198"), rarely the gene, so the
    landmark outcome trials of a target are often invisible to ``search_trials``. For
    each drug this fetches the largest interventional trials in the disease, plus the
    largest in any indication (other indications carry the target's safety record).
    Largest-first ranking puts outcome trials ahead of small pharmacology studies.
    """
    if not drugs:
        return []
    base = {
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "sort": "EnrollmentCount:desc",
    }
    queries: list[tuple[dict[str, Any], int, str, bool]] = []
    for drug in dict.fromkeys(d for d in drugs if d):
        if disease:
            queries.append(
                ({**base, "query.intr": drug, "query.term": disease}, per_drug_disease, drug, True)
            )
        queries.append(({**base, "query.intr": drug}, per_drug_any, drug, False))
    async with httpx.AsyncClient(timeout=30.0) as client:

        async def _one(params: dict[str, Any], cap: int) -> list[dict[str, Any]]:
            try:
                return await _fetch_studies(client, params, cap)
            except (MCPToolError, httpx.HTTPError):
                return []  # one failed drug query never drops the others

        pages = await asyncio.gather(*(_one(p, c) for p, c, _, _ in queries))
        origin: dict[str, tuple[str, bool]] = {}
        studies: list[dict[str, Any]] = []
        for page, (_, _, drug, in_disease) in zip(pages, queries, strict=True):
            for s in page:
                nct = s.get("protocolSection", {}).get("identificationModule", {}).get("nctId", "")
                if nct and nct not in origin:
                    origin[nct] = (drug, in_disease)
                    studies.append(s)
        results_by_nct = await _with_results(client, studies)
    return [
        r.model_copy(
            update={"query_drug": origin[r.nct_id][0], "query_in_disease": origin[r.nct_id][1]}
        )
        for r in _to_records(studies, results_by_nct)
    ]


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def drug_named_in(record: TrialRecord, drug: str) -> bool:
    """Whether the trial record itself names ``drug`` (interventions, their registry
    aliases, or the title), ignoring case, spaces and hyphens ("BIIB-094" = "BIIB094").

    The registry's intervention search also matches by its own synonym table and by loose
    term overlap, so a drug query can return trials of unrelated products (a varicella
    vaccine for a sponsor-coded antisense drug)."""
    key = _alnum(drug)
    if len(key) < 3:
        return False
    text = _alnum(" ".join([*record.interventions, *record.intervention_aliases, record.title]))
    return key in text


def _to_records(
    studies: list[dict[str, Any]], results_by_nct: dict[str, TrialResults]
) -> list[TrialRecord]:
    """Parse v2 ``studies`` into TrialRecords."""
    records: list[TrialRecord] = []

    for study in studies:
        proto = study.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design_mod = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        arms_mod = proto.get("armsInterventionsModule", {})
        desc_mod = proto.get("descriptionModule", {})
        elig_mod = proto.get("eligibilityModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        design_info = design_mod.get("designInfo", {})
        masking_info = design_info.get("maskingInfo", {})

        has_results = bool(study.get("hasResults", False))
        scope = "full_text" if has_results else "abstract"

        interventions = [iv.get("name", "") for iv in arms_mod.get("interventions", [])]
        aliases = [
            str(a)
            for iv in arms_mod.get("interventions", [])
            for a in iv.get("otherNames") or []
            if a
        ]

        phases = design_mod.get("phases") or []
        records.append(
            TrialRecord(
                nct_id=id_mod.get("nctId", ""),
                title=id_mod.get("briefTitle", ""),
                status=status_mod.get("overallStatus", ""),
                phase=phases[0] if phases else "",
                conditions=cond_mod.get("conditions", []),
                interventions=interventions,
                enrollment=design_mod.get("enrollmentInfo", {}).get("count"),
                start_date=status_mod.get("startDateStruct", {}).get("date", ""),
                scope=scope,
                brief_summary=desc_mod.get("briefSummary", ""),
                sponsor=sponsor_mod.get("leadSponsor", {}).get("name", ""),
                design_details=desc_mod.get("detailedDescription", ""),
                participation_criteria=ParticipationCriteria(
                    sex=elig_mod.get("sex", ""),
                    minimum_age=elig_mod.get("minimumAge", ""),
                    maximum_age=elig_mod.get("maximumAge", ""),
                    healthy_volunteers=str(elig_mod.get("healthyVolunteers", "")),
                    eligibility_criteria=elig_mod.get("eligibilityCriteria", ""),
                ),
                study_plan=StudyPlan(
                    study_type=design_mod.get("studyType", ""),
                    allocation=design_info.get("allocation", ""),
                    intervention_model=design_info.get("interventionModel", ""),
                    intervention_model_description=design_info.get(
                        "interventionModelDescription", ""
                    ),
                    primary_purpose=design_info.get("primaryPurpose", ""),
                    masking=masking_info.get("masking", ""),
                    number_of_arms=design_mod.get("numberOfArms"),
                ),
                record_dates=StudyRecordDates(
                    first_submit_date=status_mod.get("studyFirstSubmitDate", ""),
                    first_post_date=status_mod.get("studyFirstPostDateStruct", {}).get("date", ""),
                    last_update_submit_date=status_mod.get("lastUpdateSubmitDate", ""),
                    last_update_post_date=status_mod.get("lastUpdatePostDateStruct", {}).get(
                        "date", ""
                    ),
                ),
                results=results_by_nct.get(id_mod.get("nctId", ""), TrialResults()).outcomes,
                arm_events=results_by_nct.get(id_mod.get("nctId", ""), TrialResults()).arm_events,
                intervention_aliases=aliases,
            )
        )
    return records
