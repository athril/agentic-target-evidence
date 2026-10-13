# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""LLM claims must be supported by their source text.

Two failure modes seen in real runs:

* copying the prompt's worked example — a phase 2 FSGS abstract became "BI 764198 …
  showed a 37% objective response rate in previously treated NSCLC patients", and PCSK9
  trial records became "PCSK9 inhibition with AMG-510 showed 37% ORR", when the example
  was a concrete KRAS/NSCLC result. The example is now placeholders (GENE-A / DRUG-A /
  DISEASE-A) the leak check recognises;
* over-rejection — "evolocumab reduced first events by 25%" from a source stating
  "hazard ratio, 0.75" was dropped as an ungrounded number, so outcome-trial headlines
  never reached the clinical lens. A percentage derived from a source ratio is grounded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.evidence.claim_extraction import example_words, ungrounded_reason

_EXAMPLE = example_words(Path("skills/claim_extraction.md").read_text())
_SOURCE = (
    "TRPC6 inhibition for the treatment of focal segmental glomerulosclerosis: a randomised, "
    "placebo-controlled, phase II trial of BI 764198. Sixty-seven participants were "
    "randomised; two dose arms ... UPCR fell by 38.44% with 20 mg ... well tolerated."
)


def test_example_identifiers_are_its_placeholders():
    assert {"gene-a", "drug-a", "disease-a"} <= _EXAMPLE
    assert "patients" not in _EXAMPLE


def test_example_carries_no_real_drug_or_disease():
    skill = Path("skills/claim_extraction.md").read_text().lower()
    for leaked_before in ("amg-510", "nsclc", "kras", "objective response"):
        assert leaked_before not in skill


@pytest.mark.parametrize(
    "claim,reason",
    [
        (
            "BI 764198, a TRPC6 inhibitor, showed a 37% objective response rate in FSGS.",
            "number 37 not in source",
        ),
        (
            "The GENE-A inhibitor BI 764198 reduced the primary endpoint in FSGS.",
            "example identifier not in source: gene-a",
        ),
        (
            "DRUG-A reduced proteinuria in DISEASE-A patients.",
            "example identifier not in source: disease-a, drug-a",
        ),
    ],
)
def test_copied_or_invented_claims_are_rejected(claim, reason):
    assert ungrounded_reason(claim, _SOURCE, _EXAMPLE) == reason


@pytest.mark.parametrize(
    "claim",
    [
        "BI 764198, a TRPC6 inhibitor, showed proteinuria response in FSGS patients.",
        "BI 764198 20 mg reduced UPCR by 38.4% in a Phase 2 trial.",  # roman + precision
        "The trial compared 2 dose arms of BI 764198.",  # spelled-out number
        "TRPC6 was first linked to FSGS in 2005.",  # years are not checked
    ],
)
def test_supported_claims_are_kept(claim):
    assert ungrounded_reason(claim, _SOURCE, _EXAMPLE) == ""


def test_example_identifier_present_in_the_source_is_fine():
    source = "Variants in GENE-A occur in DISEASE-A cohorts."
    assert ungrounded_reason("GENE-A variants occur in DISEASE-A.", source, _EXAMPLE) == ""


# Ratio-derived percentages, across therapeutic areas.
@pytest.mark.parametrize(
    "source,claim",
    [
        (  # cardiovascular outcome trial (hazard ratio → relative reduction)
            "A 3-point MACE event occurred in 336 patients (hazard ratio, 0.75; 95% CI 0.65 "
            "to 0.86).",
            "Evolocumab reduced first major cardiovascular events by 25% (HR 0.75).",
        ),
        (  # oncology (HR for progression or death)
            "Median PFS 11.1 vs 5.6 months; hazard ratio for progression or death 0.46.",
            "Osimertinib lowered the risk of progression or death by 54%.",
        ),
        (  # genetic epidemiology (odds ratio → relative increase)
            "an odds ratio for type 2 diabetes of 1.29 (1.11 to 1.50)",
            "LDL-lowering PCSK9 variants raised type 2 diabetes risk by 29% (OR 1.29).",
        ),
        (  # relative risk written as percent of comparator
            "relative risk 0.62 versus placebo",
            "Risk on treatment was 62 percent of that on placebo.",
        ),
    ],
)
def test_ratio_derived_percentages_are_grounded(source, claim):
    assert ungrounded_reason(claim, source) == ""


@pytest.mark.parametrize(
    "source,claim,reason",
    [
        (  # a derived value is only allowed as a percentage, not as a bare count
            "hazard ratio 0.75",
            "25 patients had events.",
            "number 25 not in source",
        ),
        (  # integers in the source are counts, not ratios
            "Of 3 trials, 2 were positive.",
            "Treatment cut events by 66%.",
            "number 66 not in source",
        ),
        (  # unrelated invented percentage
            "hazard ratio 0.85",
            "Alirocumab reduced events by 41%.",
            "number 41 not in source",
        ),
    ],
)
def test_derivation_does_not_open_a_loophole(source, claim, reason):
    assert ungrounded_reason(claim, source) == reason


# ---------------------------------------------------------------------------
# Perturbed entity: a claim of loss of the target must match the source
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from services.evidence.claim_extraction import perturbation_mismatch  # noqa: E402

_INVERSION_SOURCE = (
    "Here, we show that knockout of the GENE-A-counteracting phosphatase OTHER1 causes a "
    "gene-dose-dependent disruption of axonal transport."
)


@pytest.mark.parametrize(
    ("claim", "source", "dropped"),
    [
        # the source knocked out another gene that GENE-A only modifies
        ("GENE-A knockout disrupts axonal transport.", _INVERSION_SOURCE, True),
        (
            "Knockdown of GENE-A reduced growth.",
            "Knockdown of the GENE-A substrate OTHER2 reduced growth.",
            True,
        ),
        # no perturbation of the gene in the source at all
        (
            "GENE-A knockout reduced infarct size in mice.",
            "Plasma GENE-A levels correlate with outcome in a patient cohort.",
            True,
        ),
        # the source perturbs the gene itself, in other words
        (
            "GENE-A-deficient mice show kidney changes.",
            "Gene-a−/− mice develop kidney pathology.",
            False,
        ),
        ("Mice lacking GENE-A have lower LDL.", "Genetic deletion of GENE-A lowered LDL.", False),
        (
            "Loss of GENE-A impairs autophagy.",
            "GENE-A-knockout cells show impaired autophagy.",
            False,
        ),
        (
            "Knock-down of GENE-A modulates tubules.",
            "ASO-mediated knock-down of mutant GENE-A modulates tubules.",
            False,
        ),
        # loss of an ortholog the source names for the gene
        (
            "GENE-A loss leads to vesicle accumulation in flies.",
            "Here we report that the loss of Gena, an ortholog of human GENE-A, leads to vesicle accumulation.",
            False,
        ),
        (
            "Knockdown of GENE-A in glia worsened degeneration.",
            "Knockdown of Gena, the fly homolog of GENE-A, worsened degeneration.",
            False,
        ),
        # an ortholog of another gene does not count
        (
            "GENE-A loss leads to vesicle accumulation.",
            "The loss of Othr, an ortholog of human OTHER1, leads to vesicle accumulation near GENE-A.",
            True,
        ),
        # naturally occurring loss-of-function variants are not an experimental loss
        (
            "Carriers of rare GENE-A loss-of-function variants have lower LDL-C.",
            "Variants in the GENE-A gene with predicted loss-of-function lowered LDL-C.",
            False,
        ),
        # not a loss-of-gene claim
        ("GENE-A inhibition reduced aggregation.", _INVERSION_SOURCE, False),
        (
            "Patients with or without variations in the GENE-A gene were enrolled.",
            "Open to patients with presence or absence of variations in the GENE-A gene.",
            False,
        ),
        (
            "The study enrolls patients with presence or absence of variations in the GENE-A gene.",
            "Patients of any genotype.",
            False,
        ),
    ],
)
def test_perturbation_mismatch(claim: str, source: str, dropped: bool) -> None:
    assert perturbation_mismatch(claim, source, "GENE-A") is dropped


def test_ungrounded_reason_names_the_lost_gene() -> None:
    assert (
        ungrounded_reason(
            "GENE-A knockout disrupts axonal transport.", _INVERSION_SOURCE, gene="GENE-A"
        )
        == "loss of GENE-A not in source"
    )
