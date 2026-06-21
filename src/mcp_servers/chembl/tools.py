# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ChEMBL tools — chemistry (drug mechanisms, clinical candidates, potency).

Public REST source, NON_SENSITIVE: drug mechanisms, clinical candidates,
potency distribution, and activity/assay-type breakdown (ligandability
signal) for a ChEMBL target id. The target id is resolved upstream via
``mcp_servers/uniprot`` (UniProt carries the ChEMBL cross-reference), so
this module never resolves a gene symbol to a target on its own.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import Counter

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0

# pChEMBL thresholds: 6 = IC50 ≤ 1 µM, 7 = ≤ 100 nM, 8 = ≤ 10 nM
_ACTIVE_THRESHOLD = 6.0
_POTENT_THRESHOLD = 7.0
_HIGH_POTENCY_THRESHOLD = 8.0

# Maximum activities to pull for potency statistics (sample)
_POTENCY_SAMPLE_LIMIT = 1000
# Maximum activities to scan for clinical candidates
_CLINICAL_SAMPLE_LIMIT = 100


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with retries on transient transport errors."""
    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.get(url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(delay)
                delay *= 2
    raise MCPToolError(
        f"Request to {url} failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


class ClinicalCandidate(BaseModel):
    molecule_chembl_id: str
    pref_name: str = ""
    max_phase: float


class ChemistryBundle(BaseModel):
    gene_symbol: str
    chembl_target_id: str = ""
    # Annotated drug mechanisms (narrow ChEMBL definition)
    num_mechanisms: int = 0
    max_phase: float | None = None  # highest clinical phase across known modulators
    action_types: list[str] = []  # e.g. INHIBITOR, AGONIST, ANTAGONIST
    mechanisms_of_action: list[str] = []
    # Total bioactivity count (ligandability proxy)
    num_bioactivities: int = 0
    # Clinical-stage molecules active against this target
    clinical_candidates: list[ClinicalCandidate] = []
    num_clinical_candidates: int = 0
    # Potency distribution from quantitative assays (pChEMBL sample)
    num_quantitative: int = 0  # activities with a pChEMBL value
    num_actives: int = 0  # pChEMBL >= 6 (IC50 ≤ 1 µM)
    num_potent: int = 0  # pChEMBL >= 7 (≤ 100 nM)
    num_highly_potent: int = 0  # pChEMBL >= 8 (≤ 10 nM)
    median_pchembl: float | None = None
    # Activity and assay type distributions (from potency sample)
    activity_type_counts: dict[str, int] = {}  # {"IC50": 734, "Ki": 112, ...}
    assay_type_counts: dict[str, int] = {}  # {"B": 600, "F": 300, "A": 50}
    source_link: str = ""
    text: str = ""


def _first(d: dict, *keys: str):
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return None


async def get_chemistry(chembl_target_id: str, gene_symbol: str = "") -> ChemistryBundle:
    """Fetch ChEMBL drug-mechanism, clinical candidates, and potency signal for a target."""
    if not chembl_target_id:
        return ChemistryBundle(
            gene_symbol=gene_symbol,
            text=f"No ChEMBL target mapping for {gene_symbol or 'gene'}; chemistry signal unavailable.",
        )

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        mech_resp, total_resp, potency_resp, clinical_resp = await asyncio.gather(
            _get(
                client,
                f"{_CHEMBL_BASE}/mechanism.json",
                params={"target_chembl_id": chembl_target_id, "limit": "100"},
            ),
            _get(
                client,
                f"{_CHEMBL_BASE}/activity.json",
                params={"target_chembl_id": chembl_target_id, "limit": "1"},
            ),
            _get(
                client,
                f"{_CHEMBL_BASE}/activity.json",
                params={
                    "target_chembl_id": chembl_target_id,
                    "pchembl_value__isnull": "false",
                    "limit": str(_POTENCY_SAMPLE_LIMIT),
                },
            ),
            _get(
                client,
                f"{_CHEMBL_BASE}/activity.json",
                params={
                    "target_chembl_id": chembl_target_id,
                    "molecule_max_phase__gte": "1",
                    "limit": str(_CLINICAL_SAMPLE_LIMIT),
                },
            ),
        )

    # Raise on non-5xx client errors for the mechanism call (primary signal)
    if mech_resp.status_code != 200 and mech_resp.status_code < 500:
        raise MCPToolError(
            f"ChEMBL mechanism API returned HTTP {mech_resp.status_code} for {chembl_target_id}"
        )
    if total_resp.status_code != 200 and total_resp.status_code < 500:
        raise MCPToolError(
            f"ChEMBL activity API returned HTTP {total_resp.status_code} for {chembl_target_id}"
        )

    mech_ok = mech_resp.status_code == 200
    total_ok = total_resp.status_code == 200
    potency_ok = potency_resp.status_code == 200
    clinical_ok = clinical_resp.status_code == 200

    if not mech_ok and not total_ok:
        return ChemistryBundle(
            gene_symbol=gene_symbol,
            chembl_target_id=chembl_target_id,
            text=f"ChEMBL API unavailable for {chembl_target_id} (HTTP {mech_resp.status_code}); chemistry signal unavailable.",
        )

    # --- Mechanisms ---
    mechanisms = (mech_resp.json() if mech_ok else {}).get("mechanisms") or []
    action_types: list[str] = []
    moas: list[str] = []
    max_phase: float | None = None
    for m in mechanisms:
        at = m.get("action_type")
        if at and at not in action_types:
            action_types.append(at)
        moa = m.get("mechanism_of_action")
        if moa and moa not in moas:
            moas.append(moa)
        phase = _first(m, "max_phase")
        if phase is not None:
            try:
                pf = float(phase)
                max_phase = pf if max_phase is None else max(max_phase, pf)
            except (TypeError, ValueError):
                pass

    # --- Total bioactivity count ---
    num_bioactivities = int(
        (((total_resp.json() if total_ok else {}) or {}).get("page_meta") or {}).get(
            "total_count", 0
        )
    )

    # --- Potency distribution ---
    pchembl_values: list[float] = []
    activity_type_counter: Counter[str] = Counter()
    assay_type_counter: Counter[str] = Counter()

    if potency_ok:
        for act in (potency_resp.json() or {}).get("activities") or []:
            try:
                pv = float(act["pchembl_value"])
                pchembl_values.append(pv)
            except (TypeError, ValueError, KeyError):
                pass
            std_type = act.get("standard_type")
            if std_type:
                activity_type_counter[std_type] += 1
            assay_type = act.get("assay_type")
            if assay_type:
                assay_type_counter[assay_type] += 1

    num_actives = sum(1 for v in pchembl_values if v >= _ACTIVE_THRESHOLD)
    num_potent = sum(1 for v in pchembl_values if v >= _POTENT_THRESHOLD)
    num_highly_potent = sum(1 for v in pchembl_values if v >= _HIGH_POTENCY_THRESHOLD)
    median_pchembl = statistics.median(pchembl_values) if pchembl_values else None

    # --- Clinical candidates ---
    # De-duplicate by molecule_chembl_id, track max phase per molecule.
    seen: dict[str, ClinicalCandidate] = {}
    if clinical_ok:
        for act in (clinical_resp.json() or {}).get("activities") or []:
            mol_id = act.get("molecule_chembl_id") or ""
            if not mol_id:
                continue
            try:
                phase = float(act.get("molecule_max_phase") or 0)
            except (TypeError, ValueError):
                phase = 0.0
            if mol_id not in seen or phase > seen[mol_id].max_phase:
                seen[mol_id] = ClinicalCandidate(
                    molecule_chembl_id=mol_id,
                    pref_name=act.get("molecule_pref_name") or "",
                    max_phase=phase,
                )

    # Also surface clinical-phase molecules from mechanism annotations
    for m in mechanisms:
        mol_id = m.get("molecule_chembl_id") or ""
        if not mol_id:
            continue
        try:
            phase = float(m.get("max_phase") or 0)
        except (TypeError, ValueError):
            phase = 0.0
        if phase >= 1 and (mol_id not in seen or phase > seen[mol_id].max_phase):
            seen[mol_id] = ClinicalCandidate(
                molecule_chembl_id=mol_id,
                pref_name=m.get("molecule_pref_name") or "",
                max_phase=phase,
            )

    # Update max_phase from clinical candidates if higher than mechanism-reported
    for cand in seen.values():
        if max_phase is None or cand.max_phase > max_phase:
            max_phase = cand.max_phase

    # Keep only true clinical-stage compounds (phase >= 1); the API filter is unreliable
    clinical_candidates = sorted(
        (c for c in seen.values() if c.max_phase >= 1),
        key=lambda c: -c.max_phase,
    )

    # --- Build summary text ---
    phase_text = f" Max clinical phase={max_phase}." if max_phase is not None else ""
    moa_text = f" MoA: {'; '.join(moas[:4])}." if moas else ""
    clin_text = (
        f" {len(clinical_candidates)} clinical candidate(s) (highest phase {max_phase})."
        if clinical_candidates
        else ""
    )
    potency_text = ""
    if pchembl_values:
        potency_text = (
            f" {len(pchembl_values)} quantitative measurements"
            f" (actives ≤1µM: {num_actives}, ≤100nM: {num_potent}, ≤10nM: {num_highly_potent}"
            f"; median pChEMBL {median_pchembl:.1f})."
        )

    return ChemistryBundle(
        gene_symbol=gene_symbol,
        chembl_target_id=chembl_target_id,
        num_mechanisms=len(mechanisms),
        max_phase=max_phase,
        action_types=action_types,
        mechanisms_of_action=moas,
        num_bioactivities=num_bioactivities,
        clinical_candidates=clinical_candidates,
        num_clinical_candidates=len(clinical_candidates),
        num_quantitative=len(pchembl_values),
        num_actives=num_actives,
        num_potent=num_potent,
        num_highly_potent=num_highly_potent,
        median_pchembl=median_pchembl,
        activity_type_counts=dict(activity_type_counter.most_common()),
        assay_type_counts=dict(assay_type_counter.most_common()),
        source_link=f"https://www.ebi.ac.uk/chembl/target_report_card/{chembl_target_id}/",
        text=(
            f"ChEMBL {chembl_target_id}: {len(mechanisms)} annotated drug mechanism(s), "
            f"{num_bioactivities} measured bioactivities.{phase_text}{clin_text}{potency_text}{moa_text}"
        ),
    )
