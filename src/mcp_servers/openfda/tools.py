# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenFDA tools — FDA drug labels and FAERS adverse event reports.

Two public REST sources, both NON_SENSITIVE:
  - Drug Labels (/drug/label.json) → FDA-approved indications, mechanism of
    action, black-box warnings, and contraindications for drugs relevant to
    the target gene or indication.
  - Adverse Events (/drug/event.json, FAERS) → reaction counts, serious event
    rates, and death rates for drugs that modulate the target.

FAERS is a signal-generating source, not ground truth. Report counts reflect
voluntary submissions and are subject to underreporting, confounder bias, and
duplicate submissions. Downstream lenses must apply biological plausibility
checks before drawing safety conclusions from FAERS data.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_OPENFDA_BASE = "https://api.fda.gov"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_LABEL_LIMIT = 10
_REACTION_TOP_N = 25
_TEXT_TRUNCATE = 2000


async def _get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
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


def _truncate(val: str | list[Any] | None, max_chars: int = _TEXT_TRUNCATE) -> str:
    if not val:
        return ""
    text = " ".join(val) if isinstance(val, list) else str(val)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


class DrugLabelRecord(BaseModel):
    drug_name: str
    brand_names: list[str] = []
    product_type: str = ""
    indications_and_usage: str = ""
    mechanism_of_action: str = ""
    warnings: str = ""
    boxed_warning: str = ""
    contraindications: str = ""
    adverse_reactions: str = ""
    application_number: str = ""
    source_link: str = ""
    text: str = ""


class TopReaction(BaseModel):
    reaction: str
    count: int


class AdverseEventBundle(BaseModel):
    drug_name: str
    total_reports: int = 0
    serious_reports: int = 0
    death_reports: int = 0
    serious_rate: float | None = None
    death_rate: float | None = None
    top_reactions: list[TopReaction] = []
    source_link: str = ""
    text: str = ""


def _parse_labels(resp: httpx.Response) -> dict[str, DrugLabelRecord]:
    """Parse label API response into a dict keyed by lower-cased generic name."""
    records: dict[str, DrugLabelRecord] = {}
    if resp.status_code == 404:
        return records
    if resp.status_code != 200:
        raise MCPToolError(f"OpenFDA labels API returned HTTP {resp.status_code}")

    for result in (resp.json() or {}).get("results") or []:
        ot = result.get("openfda") or {}
        generic_names: list[str] = ot.get("generic_name") or []
        drug_name = generic_names[0] if generic_names else ""
        if not drug_name or drug_name.lower() in records:
            continue

        brand_names: list[str] = ot.get("brand_name") or []
        product_types: list[str] = ot.get("product_type") or []
        app_numbers: list[str] = ot.get("application_number") or []
        set_id: str = result.get("set_id") or ""

        bw = _truncate(result.get("boxed_warning"))
        moa = _truncate(result.get("mechanism_of_action"))
        ind = _truncate(result.get("indications_and_usage"))
        warn = _truncate(result.get("warnings"))
        contra = _truncate(result.get("contraindications"))
        adv_rx = _truncate(result.get("adverse_reactions"))

        parts = [f"Drug: {drug_name}."]
        if bw:
            parts.append(f"BLACK BOX: {bw[:200]}.")
        if moa:
            parts.append(f"MoA: {moa[:200]}.")
        if ind:
            parts.append(f"Indication: {ind[:200]}.")

        records[drug_name.lower()] = DrugLabelRecord(
            drug_name=drug_name,
            brand_names=brand_names[:5],
            product_type=product_types[0] if product_types else "",
            indications_and_usage=ind,
            mechanism_of_action=moa,
            warnings=warn,
            boxed_warning=bw,
            contraindications=contra,
            adverse_reactions=adv_rx,
            application_number=app_numbers[0] if app_numbers else "",
            source_link=(
                f"https://api.fda.gov/drug/label.json?search=set_id:{set_id}"
                if set_id
                else "https://api.fda.gov/drug/label.json"
            ),
            text=" ".join(parts),
        )
    return records


# Clinical/generic disease descriptor words that are too broad or non-specific
# to use alone as an FDA label search term.
_INDICATION_STOPWORDS = frozenset(
    {
        "type",
        "stage",
        "advanced",
        "metastatic",
        "locally",
        "recurrent",
        "unresectable",
        "refractory",
        "relapsed",
        "newly",
        "diagnosed",
        "primary",
        "secondary",
        "acute",
        "chronic",
        "early",
    }
)


def _broad_indication(indication: str) -> str | None:
    """Return a single-word fallback search term for clinical disease names.

    FDA labels rarely use exact clinical nomenclature (e.g. 'pancreatic neoplasm').
    Extracts the first anatomical/disease noun by skipping leading stopwords.
    Returns None when the indication is already a single word.
    """
    words = indication.lower().split()
    if len(words) <= 1:
        return None
    for word in words:
        if word not in _INDICATION_STOPWORDS and len(word) > 3:
            return word
    return words[0]


async def search_drug_labels(gene_symbol: str, indication: str) -> list[DrugLabelRecord]:
    """Search FDA drug labels by gene symbol (in mechanism of action) and indication.

    Runs up to three parallel searches:
      1. Gene symbol in mechanism_of_action
      2. Full indication string in indications_and_usage
      3. First anatomical/disease word (fallback for clinical nomenclature like
         'pancreatic neoplasm' which doesn't appear verbatim in label text)
    Results are deduplicated by generic name.
    """
    broad = _broad_indication(indication)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        calls = [
            _get(
                client,
                f"{_OPENFDA_BASE}/drug/label.json",
                params={
                    "search": f'mechanism_of_action:"{gene_symbol}"',
                    "limit": str(_LABEL_LIMIT),
                },
            ),
            _get(
                client,
                f"{_OPENFDA_BASE}/drug/label.json",
                params={
                    "search": f'indications_and_usage:"{indication}"',
                    "limit": str(_LABEL_LIMIT),
                },
            ),
        ]
        if broad:
            calls.append(
                _get(
                    client,
                    f"{_OPENFDA_BASE}/drug/label.json",
                    params={
                        "search": f'indications_and_usage:"{broad}"',
                        "limit": str(_LABEL_LIMIT),
                    },
                )
            )
        responses = await asyncio.gather(*calls)

    records: dict[str, DrugLabelRecord] = {}
    for resp in responses:
        for k, v in _parse_labels(resp).items():
            if k not in records:
                records[k] = v

    return list(records.values())


async def search_adverse_events(drug_name: str) -> AdverseEventBundle:
    """Fetch FAERS adverse event summary for a drug.

    Returns total, serious, and death report counts plus top reactions by
    frequency. All four sub-queries run in parallel.
    """
    search_term = f'patient.drug.openfda.generic_name:"{drug_name}"'
    src_link = f"https://api.fda.gov/drug/event.json?search={search_term}"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        total_resp, serious_resp, death_resp, reaction_resp = await asyncio.gather(
            _get(
                client,
                f"{_OPENFDA_BASE}/drug/event.json",
                params={"search": search_term, "limit": "1"},
            ),
            _get(
                client,
                f"{_OPENFDA_BASE}/drug/event.json",
                params={"search": f"{search_term} AND serious:1", "limit": "1"},
            ),
            _get(
                client,
                f"{_OPENFDA_BASE}/drug/event.json",
                params={"search": f"{search_term} AND seriousnessdeath:1", "limit": "1"},
            ),
            _get(
                client,
                f"{_OPENFDA_BASE}/drug/event.json",
                params={
                    "search": search_term,
                    "count": "patient.reaction.reactionmeddrapt.exact",
                    "limit": str(_REACTION_TOP_N),
                },
            ),
        )

    if total_resp.status_code == 404:
        return AdverseEventBundle(
            drug_name=drug_name,
            source_link=src_link,
            text=f"No FAERS adverse event reports found for {drug_name!r}.",
        )
    if total_resp.status_code != 200:
        raise MCPToolError(
            f"OpenFDA FAERS API returned HTTP {total_resp.status_code} for {drug_name!r}"
        )

    def _meta_total(resp: httpx.Response) -> int:
        if resp.status_code != 200:
            return 0
        return int(((resp.json() or {}).get("meta") or {}).get("results", {}).get("total", 0))

    total = _meta_total(total_resp)
    serious = _meta_total(serious_resp)
    death = _meta_total(death_resp)

    top_reactions: list[TopReaction] = []
    if reaction_resp.status_code == 200:
        for item in (reaction_resp.json() or {}).get("results") or []:
            top_reactions.append(
                TopReaction(
                    reaction=item.get("term", ""),
                    count=int(item.get("count", 0)),
                )
            )

    serious_rate = round(serious / total, 4) if total > 0 else None
    death_rate = round(death / total, 4) if total > 0 else None

    parts = [f"FAERS: {total:,} reports for {drug_name}."]
    if serious_rate is not None:
        parts.append(f"Serious: {serious:,} ({serious_rate:.1%}).")
    if death_rate is not None:
        parts.append(f"Deaths: {death:,} ({death_rate:.1%}).")
    if top_reactions:
        top3 = ", ".join(f"{r.reaction} ({r.count:,})" for r in top_reactions[:3])
        parts.append(f"Top reactions: {top3}.")

    return AdverseEventBundle(
        drug_name=drug_name,
        total_reports=total,
        serious_reports=serious,
        death_reports=death,
        serious_rate=serious_rate,
        death_rate=death_rate,
        top_reactions=top_reactions,
        source_link=src_link,
        text=" ".join(parts),
    )
