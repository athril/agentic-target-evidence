# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oligonucleotide programmes of the target found only in the trial registry.

Open Targets known drugs and ChEMBL miss early siRNA and antisense programmes, so a
target led by them read as drug-free in every lens: HSD17B13's RNAi trials (ALN-HSD,
AZD7503) were listed by the clinical lens, while biology said "max_phase=0", the clinical
and regulatory precedent "no drug acting on the target", safety "no drug given to people"
and commercial "no programme acts on the target".

A drug counts as an oligonucleotide that lowers the target when one of its trial records

* describes it as an oligonucleotide (an RNA-silencing or antisense term, or an INN stem
  for one: ``-siran`` siRNA, ``-rsen`` antisense); and
* aims it at the target: the silencing is "of", "against" or "targeting" the gene, or
  names the gene's mRNA ("Knockdown of HSD17B13 mRNA"), or the drug's sponsor code
  carries the gene's name ("ALN-HSD"). A record that names the gene only beside another
  drug class does not (pelacarsen, an apolipoprotein(a) antisense, "besides PCSK9
  inhibitors"); and
* does not say the oligonucleotide restores or switches the gene's splicing: that raises
  the target (CFTR splice-correcting oligonucleotides).

Procedures (sampling, biopsy) are never the drug. Once a drug qualifies, every trial
naming it counts (ALN-HSD's phase 1 record does not repeat that it is an siRNA). Each
drug becomes a known-drug row in the Open Targets shape (``mcp_servers/opentargets
KnownDrug``), one per drug and indication, so precedent, programme outcomes, exposure and
the commercial reading see it like any other drug acting on the target.

Pure module: the workflow supplies the trial records and their indication flags.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from core.evidence_text import mentions_term
from services.evidence.drug_precedent import drug_key, names_drug

_OLIGO = (
    r"small\s+interfering\s+RNAs?|siRNAs?|RNAi|RNA\s+interference|antisense"
    r"|oligonucleotides?|ASOs?|GalNAc|gene\s+silencing|silenc\w*|knock-?down"
)
_OLIGO_TERM = re.compile(rf"\b(?:{_OLIGO})\b", re.IGNORECASE)
_OLIGO_STEM = re.compile(r"(?:siran|rsen)\b", re.IGNORECASE)
# Not the investigational product (mcp_servers/clinicaltrials _NON_PRODUCT_RE's sense).
_NOT_A_DRUG = re.compile(
    r"\b(?:placebo|saline|sham|vehicle|standard\s+of\s+care|best\s+supportive\s+care"
    r"|sampling|biopsy|procedure|collection|questionnaire|assessment|observation"
    r"|blood\s+draw|swab|imaging|no\s+intervention)\b",
    re.IGNORECASE,
)
# The oligonucleotide raises or restores the gene's product.
_RAISES = re.compile(
    r"\b(?:restor\w*|splice[- ]?switch\w*|splice[- ]?correct\w*|exon[- ]?skipp\w*"
    r"|upregulat\w*|up-regulat\w*)\b",
    re.IGNORECASE,
)
REGISTRY_SOURCE = "registry"
_PHASE = re.compile(r"PHASE\s*(\d)", re.IGNORECASE)
_CODE_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
_MIN_CODE_FRAGMENT = 3


def _phase(value: str) -> float:
    found = [int(m.group(1)) for m in _PHASE.finditer(str(value or ""))]
    return float(max(found)) if found else 0.0


def _text(extra: Mapping[str, Any]) -> str:
    return " ".join(str(extra.get(k) or "") for k in ("title", "brief_summary", "design_details"))


def _aimed_at(text: str, gene: str) -> bool:
    """The record aims silencing at the gene ("knockdown of GENE mRNA", "siRNA targeting
    GENE", "GENE-targeting antisense")."""
    g = re.escape(gene)
    patterns = (
        rf"(?:{_OLIGO})\W+(?:\w+\W+){{0,4}}?(?:of|against|targeting|to|for)\W+"
        rf"(?:the\s+)?(?:\w+\W+){{0,2}}?{g}\b",
        rf"\b{g}\b\W*(?:mRNA|transcripts?|siRNAs?|antisense|ASOs?|-?targeting)",
        rf"(?:targeting|against|anti-)\W*{g}\b",
        rf"\bmRNA\s+(?:of|encoding)\s+(?:the\s+)?{g}\b",
    )
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _code_names_gene(drug: str, gene: str) -> bool:
    """The drug's sponsor code carries the gene's name or its leading part ("ALN-HSD")."""
    if mentions_term(drug, gene):
        return True
    up = gene.upper()
    return any(
        len(t) >= _MIN_CODE_FRAGMENT and t.isalpha() and up.startswith(t.upper())
        for t in _CODE_TOKEN.findall(drug)
    )


def _products(extra: Mapping[str, Any]) -> list[str]:
    return [
        str(i).strip()
        for i in extra.get("interventions") or []
        if str(i).strip() and not _NOT_A_DRUG.search(str(i))
    ]


def oligo_drug(extra: Mapping[str, Any], gene: str) -> str:
    """The oligonucleotide lowering the target that a trial record describes (its
    intervention name), or "" (module docstring)."""
    products = _products(extra)
    if not gene or not products:
        return ""
    text = _text(extra)
    if _RAISES.search(text):
        return ""
    stemmed = [i for i in products if _OLIGO_STEM.search(i)]
    if not stemmed and not _OLIGO_TERM.search(text):
        return ""
    for drug in stemmed or products:
        if _aimed_at(text, gene) or _code_names_gene(drug, gene):
            return drug
    return ""


def registry_oligo_rows(
    trials: Iterable[Mapping[str, Any]],
    *,
    gene: str,
    disease: str,
    disease_id: str,
    in_indication: Mapping[str, bool],
    known_names: Iterable[str],
) -> list[dict[str, Any]]:
    """Known-drug rows for the target's oligonucleotides found only in the registry.

    ``trials`` are trial records' ``extra`` dicts with their ``nct_id``; ``in_indication``
    says per registry id whether the trial measured the run's disease. A drug Open Targets
    already lists (``known_names``) is left to its row.
    """
    trials = list(trials)
    known = {drug_key(n) for n in known_names if n}
    drugs = {
        drug_key(d): d for t in trials if (d := oligo_drug(t, gene)) and not names_drug(d, known)
    }
    rows: dict[tuple[str, bool], dict[str, Any]] = {}
    for t in trials:
        named = next(
            (key for key in drugs if any(names_drug(p, {key}) for p in _products(t))), None
        )
        if named is None:
            continue
        nct = str(t.get("nct_id") or "")
        here = bool(in_indication.get(nct))
        stage = _phase(str(t.get("phase") or ""))
        status = str(t.get("status") or "").upper()
        row = rows.setdefault(
            (named, here),
            {
                "drug_id": "",
                "drug_name": named,
                "drug_type": "Oligonucleotide",
                "max_phase": 0.0,
                "is_approved": False,
                "mechanism_of_action": f"{gene} mRNA silencing (oligonucleotide, trial registry)",
                "action_type": "INHIBITOR",
                "disease_name": disease if here else "",
                "disease_id": disease_id if here else "",
                "trial_status": "",
                "approved_in_indication": False,
                "stopped_for_cause": [],
                "stop_reports": [],
                "registry_trials": [],
                "source": REGISTRY_SOURCE,
            },
        )
        if stage >= row["max_phase"]:
            row["max_phase"], row["trial_status"] = stage, status
        row["registry_trials"].append(nct)
        row["stop_reports"].append(
            {"trial_id": nct, "stage": stage, "status": status, "categories": [], "why": ""}
        )
    return list(rows.values())
