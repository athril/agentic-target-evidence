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
