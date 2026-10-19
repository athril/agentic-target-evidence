# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Site of action of a secreted target (services/evidence/site_of_action.py)."""

from __future__ import annotations

import pytest

from agents.interpretation.safety_lens.questions import tissue_facts
from services.evidence.site_of_action import is_secreted, site_of_action_note


@pytest.mark.parametrize(
    ("locations", "secreted"),
    [
        (["Secreted"], True),
        (["Secreted, extracellular space"], True),
        (["Cell membrane", "Endosome"], False),
        ([], False),
    ],
)
def test_uniprot_locations_say_which_proteins_are_secreted(locations, secreted) -> None:
    assert is_secreted(locations) is secreted


def test_the_biology_note_stops_low_local_expression_counting_against_mechanism() -> None:
    note = site_of_action_note("GENE-A", "Colon", ["Liver", "Spleen"], lens="biology")

    assert "GENE-A is a secreted protein" in note and "Liver, Spleen" in note
    assert "do not read low expression in Colon as evidence against the mechanism" in note


def test_the_safety_tissue_facts_count_the_producing_tissues_as_exposed() -> None:
    spec = {
        "target_gene": "GENE-A",
        "disease_tissue": "Colon",
        "top_tpm_tissues": ["Liver"],
        "secreted": True,
    }

    facts, named = tissue_facts(spec, caveat="")

    assert "count both Colon and the producing tissues (Liver)" in facts
    assert "Liver" in named
    assert "secreted" not in tissue_facts({**spec, "secreted": False}, caveat="")[0]
