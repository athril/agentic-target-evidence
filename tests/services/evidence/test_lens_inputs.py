# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Required structured inputs per lens (services/evidence/lens_inputs.py)."""

from __future__ import annotations

from services.evidence.lens_inputs import LENS_INPUTS, missing_inputs_note, missing_lens_inputs


def test_present_inputs_are_not_reported():
    sources = ["gnomad:ENSG1", "gnomad_clinvar:ENSG1", "opentargets:ENSG1:MONDO_1"]
    assert missing_lens_inputs("genetics", sources, sources) == []


def test_absent_input_says_it_was_not_retrieved():
    only_ot = ["opentargets:ENSG1:MONDO_1"]
    assert missing_lens_inputs("genetics", only_ot, only_ot) == [
        "gnomAD constraint (not retrieved)",
        "ClinVar variant summary (not retrieved)",
    ]


def test_screened_out_input_is_told_apart_from_a_missing_one():
    retrieved = ["gtex_v8:ENSG1", "depmap:ENSG1", "uniprot:P1"]
    kept = ["depmap:ENSG1", "uniprot:P1"]
    assert missing_lens_inputs("biology", retrieved, kept) == [
        "tissue expression (GTEx/HPA) (retrieved but not kept by screening)"
    ]


def test_any_listed_source_satisfies_an_input():
    """Project Score stands in for DepMap when depmap.org is unreachable."""
    sources = ["gnomad:ENSG1", "hpa:ENSG1", "project_score:ENSG1"]
    assert missing_lens_inputs("safety", sources, sources) == []


def test_lenses_whose_absences_are_findings_declare_no_inputs():
    """No trial, label or patent is a finding about the target, not a gap in the run."""
    assert set(LENS_INPUTS) == {"genetics", "biology", "safety"}
    assert missing_lens_inputs("clinical", [], []) == []


def test_note_is_empty_without_missing_inputs():
    assert missing_inputs_note([]) == ""
    assert "gnomAD constraint" in missing_inputs_note(["gnomAD constraint (not retrieved)"])
