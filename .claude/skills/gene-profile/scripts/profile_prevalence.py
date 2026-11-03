# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""How common the gene's disease alleles are, from ClinVar classifications and gnomAD counts.

The profile reported per-variant frequencies and never added them up, so the question a
clinician asks first — how many people carry one of these alleles, and how many are affected —
went unanswered, while the gnomAD homozygote counts it did show (0, or 6) are uninformative
for a recessive disease that is usually compound heterozygous.

Summing is easy to do badly. The sum over CFTR's P/LP alleles is dominated by two intronic
poly-T/TG tract variants at two review stars, which ClinVar classifies as pathogenic but whose
effect depends on the repeat in cis: it gives a carrier rate of 1 in 9 against the ~1 in 25
of the literature. Threshold-tuning the list to fix one gene is exactly what must not happen,
so this module reports the estimate as the upper bound it is, beside the sensitivity across
review-status thresholds and the alleles that drive it. A reader can then see when one
variable-penetrance allele carries the estimate.

Pure module: Hardy-Weinberg on counts passed in, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# ClinVar classification wording that marks an allele whose effect is conditional, and which
# therefore cannot enter a prevalence sum as a disease allele. ClinVar's own vocabulary, not a
# per-gene rule.
CONDITIONAL_TERMS = (
    "low penetrance",
    "risk factor",
    "association",
    "affects",
    "drug response",
    "protective",
)
# Review-status thresholds the sensitivity row reports. ClinVar's gold stars: 1 = one submitter
# with criteria, 2 = multiple submitters agreeing, 3 = expert panel, 4 = practice guideline.
STARS = (1, 2, 3)
_DEFAULT_STARS = 1
# Inheritance wording (HPO, GenCC, ClinGen) → which statistic a frequency sum supports.
_RECESSIVE = ("autosomal recessive", "recessive")
_DOMINANT = ("autosomal dominant", "dominant")
_X_LINKED = ("x-linked",)


def is_conditional(significance: str | None) -> bool:
    """Whether a ClinVar classification marks a conditional or modifying allele."""
    text = (significance or "").lower()
    return any(term in text for term in CONDITIONAL_TERMS)


def allele_sum(variants: Iterable[Mapping[str, Any]], min_stars: int = _DEFAULT_STARS) -> float:
    """Summed allele frequency of the disease alleles at or above a review-status threshold."""
    return sum(
        float(v["af"])
        for v in variants
        if v.get("af") is not None
        and int(v.get("gold_stars") or 0) >= min_stars
        and not is_conditional(v.get("clinical_significance"))
    )


def statistic(q: float, inheritance: Sequence[str]) -> list[tuple[str, str]]:
    """The frequencies a summed allele frequency supports under the gene's inheritance.

    Which statistic is meaningful depends on the mode: carriers and affected births differ by
    orders of magnitude for a recessive gene, a dominant gene has no carrier state to report,
    and an X-linked gene's affected group is hemizygous males. One formula for every gene would
    be wrong for most of them, so an unrecorded or mixed mode reports the sum alone.
    """
    if q <= 0:
        return []
    modes = [m.lower() for m in inheritance if m]
    autosomal = [m for m in modes if "x-linked" not in m]
    found: list[str] = []
    if any("x-linked" in m for m in modes):
        found.append("x-linked")
    if any(w in m for m in autosomal for w in _RECESSIVE):
        found.append("autosomal recessive")
    if any(w in m for m in autosomal for w in _DOMINANT):
        found.append("autosomal dominant")
    rows: list[tuple[str, str]] = []
    # Every recorded mode gets its own rows. HPO lists each mode any of the gene's diseases
    # uses, so BRCA1, EGFR and SCN5A all record dominant and recessive: picking one by
    # priority gave SCN5A a recessive carrier rate and an expected biallelic rate of 1 in 6
    # million, for a gene whose disease is dominant.
    if "x-linked" in found:
        rows += [
            ("Hemizygous males (X-linked)", _one_in(q)),
            ("Heterozygous females (X-linked)", _one_in(2 * q * (1 - q))),
        ]
    if "autosomal recessive" in found:
        rows += [
            ("Carriers of one allele (autosomal recessive)", _one_in(2 * q * (1 - q))),
            ("Expected biallelic (autosomal recessive)", _one_in(q * q)),
        ]
    if "autosomal dominant" in found:
        rows.append(("Carriers of one allele (autosomal dominant)", _one_in(2 * q * (1 - q))))
    if len(found) > 1:
        rows.append(
            (
                "More than one mode recorded",
                "the records list " + ", ".join(found) + " across this gene's diseases, so "
                "which row applies depends on which disease is meant; they differ by orders "
                "of magnitude",
            )
        )
    if not rows:
        rows.append(
            (
                "Statistic not chosen",
                "the records do not give a single mode of inheritance, and carriers, affected "
                "births and hemizygotes differ by orders of magnitude between modes",
            )
        )
    return rows


def _one_in(p: float) -> str:
    if p <= 0:
        return "—"
    return f"{p:.2e} (about 1 in {1 / p:,.0f})"


def prevalence(
    variants: Sequence[Mapping[str, Any]],
    inheritance: Sequence[str],
    *,
    min_stars: int = _DEFAULT_STARS,
) -> dict[str, Any]:
    """The frequency estimate with everything needed to judge it.

    ``variants`` are ClinVar P/LP records with gnomAD counts (``af``, ``gold_stars``,
    ``clinical_significance``, ``hgvsp``/``hgvsc``).
    """
    plp = [v for v in variants if not is_conditional(v.get("clinical_significance"))]
    conditional = len(variants) - len(plp)
    scored = [v for v in plp if v.get("af") is not None]
    kept = [v for v in scored if int(v.get("gold_stars") or 0) >= min_stars]
    q = allele_sum(variants, min_stars)
    top = sorted(kept, key=lambda v: -float(v["af"]))[:5]
    return {
        "q": q,
        "min_stars": min_stars,
        "rows": statistic(q, inheritance),
        "sensitivity": [(s, allele_sum(variants, s)) for s in STARS],
        "top": [
            {
                "label": str(v.get("hgvsp") or v.get("hgvsc") or v.get("variant_id") or ""),
                "af": float(v["af"]),
                "share": float(v["af"]) / q if q > 0 else 0.0,
                "stars": int(v.get("gold_stars") or 0),
                "consequence": str(v.get("major_consequence") or ""),
            }
            for v in top
        ],
        "variants_total": len(variants),
        "variants_with_frequency": len(scored),
        "variants_counted": len(kept),
        "conditional_excluded": conditional,
        "inheritance": list(inheritance),
    }
