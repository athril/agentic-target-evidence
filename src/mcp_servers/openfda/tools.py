# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenFDA tools — FDA drug labels and FAERS adverse event reports.

Three public REST sources, all NON_SENSITIVE:
  - Drug Labels (/drug/label.json) → FDA-approved indications, mechanism of
    action, black-box warnings, and contraindications for drugs relevant to
    the target gene or indication.
  - Drugs@FDA (/drug/drugsfda.json) → the application behind each US approval:
    number, sponsor, brand names and approval date.
  - Adverse Events (/drug/event.json, FAERS) → reaction counts, serious event
    rates, and death rates for drugs that modulate the target.

FAERS is a signal-generating source, not ground truth. Report counts reflect
voluntary submissions and are subject to underreporting, confounder bias, and
duplicate submissions. Downstream lenses must apply biological plausibility
checks before drawing safety conclusions from FAERS data.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel

from core.evidence_text import disease_name_variants, indicates_term, label_indications
from core.exceptions import MCPToolError

_OPENFDA_BASE = "https://api.fda.gov"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_LABEL_LIMIT = 10
_REACTION_TOP_N = 25
_TEXT_TRUNCATE = 2000
# Warnings and Precautions run to several subsections; the later ones (e.g. a disease the
# drug can worsen) were cut at 2000 characters.
_WARNINGS_TRUNCATE = 8000


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


class FdaApproval(BaseModel):
    drug_name: str
    application_number: str = ""
    sponsor: str = ""
    brand_names: list[str] = []
    ingredients: list[str] = []
    route: str = ""
    approval_date: str = ""  # ISO, the original application's approval
    discontinued: bool = False  # every product of the application
    source_link: str = ""


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


# A label indicated for a diagnostic or imaging use matches an indication search without
# treating the disease: CETP × coronary artery disease read gadobutrol, an MRI contrast
# agent, as a drug "approved for coronary artery disease".
_DIAGNOSTIC_USE = re.compile(
    r"\b(?:diagnostic|contrast\s+(?:agent|medium|enhancement)|magnetic\s+resonance|MRI\b"
    r"|imaging|radiograph\w*|scintigraph\w*|ultrasound|echocardiograph\w*|visuali[sz]\w*"
    r"|myocardial\s+perfusion|PET\b|SPECT\b)",
    re.IGNORECASE,
)


def treats_indication(indication_text: str) -> bool:
    """A label indicated to treat or prevent, not to image or diagnose."""
    return not _DIAGNOSTIC_USE.search(indication_text or "")


def _originator(record: DrugLabelRecord) -> bool:
    return record.application_number.upper().startswith(("NDA", "BLA"))


def _better(new: DrugLabelRecord, old: DrugLabelRecord | None) -> bool:
    """Prefer the originator's label (NDA/BLA) over a generic's (ANDA) for one drug: BACE1
    × Alzheimer disease read the donepezil ANDA label as the indication's only drug."""
    return old is None or (_originator(new) and not _originator(old))


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
_SECTION_HEADER = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:description|mechanism of action|clinical pharmacology"
    r"|indications? (?:and|&) usage)\s*:?\s*",
    re.IGNORECASE,
)
# Label sections that name the drug in prose, most direct first.
_NAMING_SECTIONS = (
    "description",
    "mechanism_of_action",
    "clinical_pharmacology",
    "indications_and_usage",
)
_NAMING_WORDS = 60  # words read from the start of each section


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _ingredient_phrases(product: list[str]) -> set[tuple[str, ...]]:
    """Phrases of the product data followed at once by themselves or by their leading
    words: an active ingredient and its moiety ("evolocumab evolocumab", "metoprolol
    tartrate metoprolol"). A phrase that is itself a doubled phrase is not one."""
    out: set[tuple[str, ...]] = set()
    for i in range(len(product)):
        for n in (1, 2, 3, 4):
            phrase = tuple(product[i : i + n])
            if len(phrase) < n:
                break
            half = n // 2
            if n % 2 == 0 and phrase[:half] == phrase[half:]:
                continue
            if any(tuple(product[i + n : i + n + m]) == phrase[:m] for m in range(n, 0, -1)):
                out.add(phrase)
    return out


def _unharmonised_drug_name(result: dict[str, Any]) -> str:
    """The active ingredient of a label openFDA has not harmonised, or "".

    Such a label has an empty ``openfda`` block, so no generic name: the Repatha
    (evolocumab), Lipfendra (enlicitide) and Wegovy (semaglutide) labels were dropped.
    The label's product data lists each active ingredient followed by its moiety, but
    without delimiters, so brand, generic name and excipients cannot be told apart
    from it alone. The name is the first phrase the label's prose (description,
    mechanism, …) uses that the product data lists as an ingredient: the two fields must
    agree, and a label they do not agree on gets no name rather than a guessed one.
    On 704 harmonised prescription labels read this way, 95% got their generic name
    (salt aside), 3% a wrong one and 2% none; a combination product gets its first
    ingredient, and a proprietary name that repeats as the generic name stays the name
    (Lipfendra).
    """

    def first(key: str) -> str:
        value = result.get(key)
        return str((value[0] if value else "") if isinstance(value, list) else value or "")

    ingredients = _ingredient_phrases(_words(first("spl_product_data_elements")))
    if not ingredients:
        return ""
    for key in _NAMING_SECTIONS:
        words = _words(_SECTION_HEADER.sub("", first(key)))[:_NAMING_WORDS]
        for i in range(len(words)):
            for n in (4, 3, 2, 1):
                phrase = tuple(words[i : i + n])
                if len(phrase) == n and phrase in ingredients:
                    return " ".join(phrase).upper()
    return ""


def _parse_labels(
    resp: httpx.Response, keep: Callable[[str], bool] | None = None
) -> dict[str, DrugLabelRecord]:
    """Parse label API response into a dict keyed by lower-cased generic name.

    ``keep``, when given, is applied to each label's full (untruncated) indication text;
    labels it rejects are skipped.
    """
    records: dict[str, DrugLabelRecord] = {}
    if resp.status_code == 404:
        return records
    if resp.status_code != 200:
        raise MCPToolError(f"OpenFDA labels API returned HTTP {resp.status_code}")

    for result in (resp.json() or {}).get("results") or []:
        ot = result.get("openfda") or {}
        generic_names: list[str] = ot.get("generic_name") or []
        brand_names: list[str] = ot.get("brand_name") or []
        drug_name = generic_names[0] if generic_names else ""
        if not drug_name:
            drug_name = _unharmonised_drug_name(result)
        if not drug_name:
            continue
        prior = records.get(drug_name.lower())
        if prior is not None and _originator(prior):
            continue
        if keep is not None and not keep(_truncate(result.get("indications_and_usage"), 10**6)):
            continue

        product_types: list[str] = ot.get("product_type") or []
        app_numbers: list[str] = ot.get("application_number") or []
        set_id: str = result.get("set_id") or ""

        bw = _truncate(result.get("boxed_warning"))
        moa = _truncate(result.get("mechanism_of_action"))
        ind = _truncate(result.get("indications_and_usage"))
        # PLR labels (most current ones) carry Warnings and Precautions in
        # `warnings_and_cautions`; `warnings` is the pre-PLR section. Reading only the
        # latter left every modern label's warnings empty.
        warn = _truncate(
            result.get("warnings_and_cautions") or result.get("warnings"), _WARNINGS_TRUNCATE
        )
        contra = _truncate(result.get("contraindications"))
        adv_rx = _truncate(result.get("adverse_reactions"))

        parts = [f"Drug: {drug_name}."]
        if bw:
            parts.append(f"BLACK BOX: {bw[:200]}.")
        if moa:
            parts.append(f"MoA: {moa[:200]}.")
        indications = label_indications(ind)
        if indications:
            # Every indication: a fixed-length prefix kept only the first one.
            parts.append("Indications: " + "; ".join(indications) + ".")

        record = DrugLabelRecord(
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
        if _better(record, prior):
            records[drug_name.lower()] = record
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


def indication_phrases(indication: str) -> list[str]:
    """The indication plus the possessive eponym form FDA labels use.

    The bare "Parkinson disease" finds no label, and the single-word fallback "parkinson"
    then matches only "Wolff-Parkinson-White syndrome" — so Parkinson disease's
    competitors came back as adenosine and verapamil.
    """
    return disease_name_variants(indication) or [indication]


def _phrase_query(phrases: list[str]) -> str:
    q = " OR ".join(f'indications_and_usage:"{p}"' for p in phrases)
    return f"({q})" if len(phrases) > 1 else q


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


_MAX_SYNONYMS = 6


def _search_phrases(indication: str, synonyms: list[str]) -> list[str]:
    """The indication's phrases, then its exact synonyms' (the ontology's: "nonalcoholic
    steatohepatitis" for "non-alcoholic steatohepatitis", the form approved labels use)."""
    out = list(indication_phrases(indication))
    for name in synonyms[:_MAX_SYNONYMS]:
        for phrase in indication_phrases(name):
            if phrase.lower() not in {p.lower() for p in out}:
                out.append(phrase)
    return out


async def search_drug_labels(
    gene_symbol: str, indication: str, synonyms: list[str] | None = None
) -> list[DrugLabelRecord]:
    """Search FDA drug labels by gene symbol (in mechanism of action) and indication.

    Runs up to three searches:
      1. Gene symbol in mechanism_of_action
      2. Full indication string in indications_and_usage
      3. First anatomical/disease word (fallback for clinical nomenclature like
         'pancreatic neoplasm' which doesn't appear verbatim in label text) —
         only when (2) finds nothing. A single word over-matches: "focal" from
         "focal segmental glomerulosclerosis" pulls in anti-epileptics for
         "focal seizures" alongside the labels that name FSGS.
    Results are deduplicated by generic name.
    """
    broad = _broad_indication(indication)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        moa_resp, phrase_resp = await asyncio.gather(
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
                    "search": _phrase_query(_search_phrases(indication, synonyms or [])),
                    "limit": str(_LABEL_LIMIT),
                },
            ),
        )
        responses = [moa_resp, phrase_resp]
        if broad and not _parse_labels(phrase_resp, treats_indication):
            responses.append(
                await _get(
                    client,
                    f"{_OPENFDA_BASE}/drug/label.json",
                    params={
                        "search": f'indications_and_usage:"{broad}"',
                        "limit": str(_LABEL_LIMIT),
                    },
                )
            )

    records: dict[str, DrugLabelRecord] = {}
    for i, resp in enumerate(responses):
        # The mechanism search finds the target's own drugs, whatever their use; the
        # indication searches keep only labels that treat it.
        for k, v in _parse_labels(resp, None if i == 0 else treats_indication).items():
            if _better(v, records.get(k)):
                records[k] = v

    return list(records.values())


async def search_labels_for_drugs(drug_names: list[str]) -> list[DrugLabelRecord]:
    """FDA labels of the named drugs (generic names), one per drug.

    Labels describe a biologic's target by its protein name ("IL-17A", "estrogen
    receptor"), not the gene symbol, so the gene-symbol MoA search in
    `search_drug_labels` missed every approved IL-17A antibody. Searching the target's
    known drugs by name finds their labels whatever the label calls the target.
    """
    names = [n.strip() for n in drug_names if n and n.strip()]
    if not names:
        return []
    # A label openFDA has not harmonised has no generic name; it is found by its own
    # product data (Repatha's labels were invisible to a generic-name search).
    query = " OR ".join(
        f'openfda.generic_name:"{n}" OR '
        f'(_missing_:openfda.generic_name AND spl_product_data_elements:"{n}")'
        for n in names
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _get(
            client,
            f"{_OPENFDA_BASE}/drug/label.json",
            params={"search": f"({query})", "limit": str(min(100, 5 * len(names)))},
        )
    return list(_parse_labels(resp).values())


def _approval_date(application: dict[str, Any]) -> str:
    """The original application's approval date as ISO, "" if FDA records none."""
    days = [
        str(s.get("submission_status_date") or "")
        for s in application.get("submissions") or []
        if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP"
    ]
    day = min((d for d in days if len(d) == 8), default="")
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if day else ""


async def search_fda_approval(drug_name: str) -> FdaApproval | None:
    """The drug's first approved originator application (NDA or BLA) in Drugs@FDA, by
    ingredient or brand name. None when it has no US approval on record.

    This is the regulator's own record of approval. Labels can lack an application number
    when openFDA has not harmonised them, and aggregators lag new approvals.
    """
    name = drug_name.strip().upper()
    search = f'products.active_ingredients.name:"{name}" OR products.brand_name:"{name}"'
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _get(
            client,
            f"{_OPENFDA_BASE}/drug/drugsfda.json",
            params={"search": search, "limit": "100"},
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise MCPToolError(f"Drugs@FDA returned HTTP {resp.status_code} for {drug_name!r}")
    approved = [
        (date, a)
        for a in (resp.json() or {}).get("results") or []
        if str(a.get("application_number") or "").upper().startswith(("NDA", "BLA"))
        and (date := _approval_date(a))
    ]
    if not approved:
        return None
    date, first = min(approved, key=lambda pair: pair[0])
    products = [p for _d, a in approved for p in a.get("products") or []]
    brands = {str(p["brand_name"]).upper() for p in products if p.get("brand_name")}
    ingredients = {
        str(i["name"]).upper()
        for p in (first.get("products") or [])
        for i in p.get("active_ingredients") or []
        if i.get("name")
    }
    routes = [str(p.get("route")) for p in first.get("products") or [] if p.get("route")]
    status = {str(p.get("marketing_status") or "") for p in products}
    return FdaApproval(
        drug_name=name,
        application_number=first["application_number"],
        sponsor=first.get("sponsor_name") or "",
        brand_names=sorted(brands)[:_LABEL_LIMIT],
        ingredients=sorted(ingredients),
        route=routes[0] if routes else "",
        approval_date=date,
        discontinued=status == {"Discontinued"},
        source_link=f"{_OPENFDA_BASE}/drug/drugsfda.json?search={search}",
    )


class IndicationDrugLandscape(BaseModel):
    indication: str
    approved_drug_count: int = 0
    drugs: list[str] = []
    moa_examples: list[str] = []
    mapping: str = "none"  # "phrase" | "broad" | "none"
    source_link: str = ""
    text: str = ""
    # Labels matching the search (openFDA meta total); `drugs` holds at most
    # _LABEL_LIMIT of them, so approved_drug_count is a floor when capped.
    label_total: int = 0
    capped: bool = False


_MOA_EXAMPLE_N = 3


async def count_indication_drugs(indication: str) -> IndicationDrugLandscape:
    """Count FDA-labeled (≈ approved) drugs for an indication, regardless of target.

    Target-agnostic by design — this is the disease-level competitive landscape,
    not the gene-level one (`search_drug_labels` covers that). Searches the full
    indication phrase first; only falls back to the single-noun `_broad_indication`
    search when the phrase search returns nothing, since the broad search risks
    over-counting adjacent indications. SPL labels represent marketed products, so
    presence in this index is treated as approved-drug evidence.
    """
    phrases = indication_phrases(indication)
    broad = _broad_indication(indication)
    phrase_params = {
        "search": (f'{_phrase_query(phrases)} AND openfda.product_type:"HUMAN PRESCRIPTION DRUG"'),
        "limit": str(_LABEL_LIMIT),
    }
    src_link = f"{_OPENFDA_BASE}/drug/label.json?search=" + phrase_params["search"]

    # A label matches the search when its indication text merely *names* the disease,
    # including to deny it ("efficacy in … has not been established; not recommended"):
    # count only labels whose text names it in a clause that does not deny or limit use.
    def _indicated(text: str) -> bool:
        return any(indicates_term(text, p) for p in phrases)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _get(client, f"{_OPENFDA_BASE}/drug/label.json", params=phrase_params)
        records = _parse_labels(resp, keep=_indicated)
        mapping = "phrase" if records else "none"

        if not records and broad:
            broad_params = {
                "search": (
                    f'indications_and_usage:"{broad}" AND '
                    'openfda.product_type:"HUMAN PRESCRIPTION DRUG"'
                ),
                "limit": str(_LABEL_LIMIT),
            }
            resp = await _get(client, f"{_OPENFDA_BASE}/drug/label.json", params=broad_params)
            term = broad
            records = _parse_labels(resp, keep=lambda text: indicates_term(text, term))
            mapping = "broad" if records else "none"

    if not records:
        return IndicationDrugLandscape(indication=indication, mapping="none", source_link=src_link)

    try:
        label_total = int(
            ((resp.json() or {}).get("meta") or {}).get("results", {}).get("total", 0)
        )
    except (ValueError, AttributeError):
        label_total = 0
    drugs = sorted(r.drug_name for r in records.values())
    # Capped by the page size, not by the indication filter: compare with the labels returned.
    capped = label_total > len((resp.json() or {}).get("results") or [])
    moa_examples = [r.mechanism_of_action[:200] for r in records.values() if r.mechanism_of_action][
        :_MOA_EXAMPLE_N
    ]

    count = f"≥{len(drugs)}" if capped else str(len(drugs))
    parts = [
        f"{indication} (FDA labels): {count} approved drug(s) ({', '.join(drugs[:5])})"
        + (f" among {label_total} matching labels" if capped else "")
        + "."
    ]
    if moa_examples:
        parts.append(f"MoA classes incl.: {'; '.join(moa_examples)}.")

    return IndicationDrugLandscape(
        indication=indication,
        approved_drug_count=len(drugs),
        drugs=drugs,
        moa_examples=moa_examples,
        mapping=mapping,
        source_link=src_link,
        text=" ".join(parts),
        label_total=label_total,
        capped=capped,
    )


def _faers_search(drug_name: str, also_named: list[str]) -> str:
    """The FAERS query for a drug. openFDA leaves some products unharmonised, so a generic
    name can match nothing: evolocumab returned 0 reports while Repatha, as reported by
    name, has over 150,000. ``also_named`` adds the reported product names."""
    generic = f'patient.drug.openfda.generic_name:"{drug_name}"'
    names = list(dict.fromkeys(n.strip().upper() for n in also_named if n and n.strip()))
    if not names:
        return generic
    reported = [f'patient.drug.medicinalproduct:"{n}"' for n in [drug_name.upper(), *names]]
    return "(" + " OR ".join([generic, *dict.fromkeys(reported)]) + ")"


async def search_adverse_events(
    drug_name: str, also_named: list[str] | None = None
) -> AdverseEventBundle:
    """Fetch FAERS adverse event summary for a drug.

    Returns total, serious, and death report counts plus top reactions by
    frequency. All four sub-queries run in parallel. ``also_named`` adds brand or other
    product names, for drugs openFDA has not harmonised.
    """
    search_term = _faers_search(drug_name, also_named or [])
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
        parts.append(f"Serious: {serious:,} ({serious_rate:.1%} of reports).")
    if death_rate is not None:
        parts.append(f"With a fatal outcome: {death:,} ({death_rate:.1%} of reports).")
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
