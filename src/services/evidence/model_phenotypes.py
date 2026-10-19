# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The phenotypes that animal models of a disease are indexed under.

Animal models are indexed under the phenotype they reproduce, rarely under the human
disease: a model of an inflammatory bowel disease is "Colitis", one of a demyelinating
disease "Encephalomyelitis, Autoimmune, Experimental". A perturbation query restricted to
the human disease's descriptor therefore misses most model studies (measured on four
development pairs: 24 vs 175, 4 vs 53, 1 vs 26 and 105 vs 347 records).

The mapping comes from the literature itself, once per disease: the records indexed under
both the disease's MeSH descriptor and "Disease Models, Animal", and the disease
descriptors (MeSH tree C) most often co-indexed with them. No list of diseases or models
is kept here; the general signs-and-symptoms branch (C23) and the disease's own
descriptor are left out.

The counting is pure (`rank_model_phenotypes`); `find_model_phenotypes` does the three
E-utilities calls (search, metadata, tree numbers), sequentially.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

logger = logging.getLogger(__name__)

ANIMAL_MODELS = "Disease Models, Animal"
_SAMPLE = 200  # records read per disease
_CANDIDATES = 30  # most frequent descriptors whose tree numbers are looked up
MIN_SHARE = 0.1  # co-indexed in at least this share of the model records
MAX_PHENOTYPES = 5
_DISEASE_TREE = "C"
_SIGNS_AND_SYMPTOMS = "C23"


class ModelPhenotype(BaseModel):
    descriptor: str
    ui: str
    share: float  # share of the disease's model records co-indexed with it


class Heading(BaseModel):
    """The part of a MeSH heading the ranking reads."""

    descriptor: str
    ui: str = ""


def candidate_descriptors(
    headings_by_record: Mapping[str, Sequence[Heading]], disease_descriptor: str
) -> list[tuple[Heading, int]]:
    """Descriptors co-indexed with the disease's model records, most frequent first, without
    the disease and the animal-model descriptor themselves (counted once per record)."""
    skip = {disease_descriptor.lower(), ANIMAL_MODELS.lower()}
    counts: Counter[str] = Counter()
    first: dict[str, Heading] = {}
    for headings in headings_by_record.values():
        seen = {h.ui or h.descriptor for h in headings if h.descriptor.lower() not in skip}
        for h in headings:
            key = h.ui or h.descriptor
            if key in seen and key not in first:
                first[key] = h
        counts.update(seen)
    return [(first[k], n) for k, n in counts.most_common(_CANDIDATES)]


def rank_model_phenotypes(
    headings_by_record: Mapping[str, Sequence[Heading]],
    trees: Mapping[str, Sequence[str]],
    disease_descriptor: str,
) -> list[ModelPhenotype]:
    """The disease descriptors (tree C, outside C23) co-indexed with at least `MIN_SHARE`
    of the disease's model records, most frequent first, at most `MAX_PHENOTYPES`."""
    total = len(headings_by_record)
    if not total:
        return []
    out: list[ModelPhenotype] = []
    for heading, n in candidate_descriptors(headings_by_record, disease_descriptor):
        numbers = trees.get(heading.ui) or []
        is_disease = any(
            t.startswith(_DISEASE_TREE) and not t.startswith(_SIGNS_AND_SYMPTOMS) for t in numbers
        )
        share = n / total
        if is_disease and share >= MIN_SHARE:
            out.append(
                ModelPhenotype(descriptor=heading.descriptor, ui=heading.ui, share=round(share, 3))
            )
    return out[:MAX_PHENOTYPES]


async def find_model_phenotypes(disease_descriptor: str) -> list[ModelPhenotype]:
    """The model phenotypes of a disease from PubMed (see module doc); [] on any failure."""
    from mcp_servers.pubmed.tools import fetch_article_metadata, mesh_tree_numbers, search_pmids

    if not disease_descriptor:
        return []
    query = f'"{disease_descriptor}"[MeSH Terms] AND "{ANIMAL_MODELS}"[MeSH Terms]'
    try:
        pmids = await search_pmids(query, max_results=_SAMPLE, sort="relevance")
        metadata = await fetch_article_metadata(pmids)
        headings = {
            pmid: [Heading(descriptor=h.descriptor, ui=h.ui) for h in m.mesh_headings]
            for pmid, m in metadata.items()
        }
        candidates = candidate_descriptors(headings, disease_descriptor)
        trees = await mesh_tree_numbers([h.ui for h, _ in candidates if h.ui])
    except Exception as exc:  # optional enrichment: the exact descriptor still works
        logger.warning("model phenotypes for %s failed: %s", disease_descriptor, exc)
        return []
    found = rank_model_phenotypes(headings, trees, disease_descriptor)
    logger.info(
        "model phenotypes of %s (%d records): %s",
        disease_descriptor,
        len(headings),
        [(p.descriptor, p.share) for p in found],
    )
    return found
