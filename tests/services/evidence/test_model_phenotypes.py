# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Model phenotypes of a disease from MeSH co-indexing (services/evidence/model_phenotypes.py).

Synthetic MeSH tables: the descriptors are placeholders, so the cases test the counting,
not any one disease.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.evidence import model_phenotypes as mp
from services.evidence.model_phenotypes import ANIMAL_MODELS, Heading, rank_model_phenotypes

_DISEASE = Heading(descriptor="Disease One", ui="D000001")
_MODEL = Heading(descriptor=ANIMAL_MODELS, ui="D004195")
_PHENOTYPE = Heading(descriptor="Phenotype Two", ui="D000002")
_SIBLING = Heading(descriptor="Disease Three", ui="D000003")
_SIGN = Heading(descriptor="Inflammation Sign", ui="D000004")
_ORGANISM = Heading(descriptor="Mice", ui="D000005")
_TREES = {
    "D000002": ["C06.405.205"],
    "D000003": ["C06.405.469"],
    "D000004": ["C23.550.470"],
    "D000005": ["B01.050.150"],
}


def _records(n: int, *extra: list[Heading]) -> dict[str, list[Heading]]:
    """``n`` model records of the disease; ``extra[i]`` adds headings to record i."""
    out = {str(i): [_DISEASE, _MODEL, _ORGANISM] for i in range(n)}
    for i, headings in enumerate(extra):
        out[str(i)] = [*out[str(i)], *headings]
    return out


def test_the_disease_descriptors_co_indexed_with_its_models_are_its_phenotypes() -> None:
    records = _records(10, *([[_PHENOTYPE, _SIGN]] * 6), [_SIBLING])

    found = rank_model_phenotypes(records, _TREES, "Disease One")

    # Organisms (tree B) and general signs (C23) are not phenotypes; a descriptor in one
    # record of ten reaches the share; the disease itself is never its own phenotype.
    assert [(p.descriptor, p.share) for p in found] == [
        ("Phenotype Two", 0.6),
        ("Disease Three", 0.1),
    ]


def test_a_rarely_co_indexed_descriptor_is_not_a_phenotype() -> None:
    records = _records(20, [_PHENOTYPE])

    assert rank_model_phenotypes(records, _TREES, "Disease One") == []


def test_a_descriptor_is_counted_once_per_record() -> None:
    records = _records(4, [_PHENOTYPE, _PHENOTYPE, _PHENOTYPE])

    [p] = rank_model_phenotypes(records, _TREES, "Disease One")
    assert p.share == 0.25


def test_no_records_no_phenotypes() -> None:
    assert rank_model_phenotypes({}, _TREES, "Disease One") == []


async def test_a_failed_lookup_is_not_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_servers.pubmed.tools as pubmed

    monkeypatch.setattr(pubmed, "search_pmids", AsyncMock(side_effect=RuntimeError("down")))

    assert await mp.find_model_phenotypes("Disease One") is None
