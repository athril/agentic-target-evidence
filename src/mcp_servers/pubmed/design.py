# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Study design of a PubMed article from its indexing: publication types first (assigned
by the publisher and NLM), then MeSH study-design and model headings.

A pure lookup over the record, no text mining: an article indexed with neither gives
``"not indexed"`` (recent papers are MeSH-indexed weeks after they appear) or
``"other primary research"``. The species an article studied comes from its MeSH check
tags (Humans, Mice, Rats, …), which NLM assigns to every indexed article.
"""

from __future__ import annotations

from collections.abc import Iterable

# (design, publication types that set it, MeSH descriptors that set it), most specific first.
DESIGNS: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (
    (
        "meta-analysis",
        frozenset({"Meta-Analysis", "Systematic Review"}),
        frozenset(),
    ),
    (
        "randomized controlled trial",
        frozenset({"Randomized Controlled Trial", "Equivalence Trial", "Pragmatic Clinical Trial"}),
        frozenset({"Randomized Controlled Trials as Topic"}),
    ),
    (
        "clinical trial",
        frozenset(
            {
                "Clinical Trial",
                "Clinical Trial, Phase I",
                "Clinical Trial, Phase II",
                "Clinical Trial, Phase III",
                "Clinical Trial, Phase IV",
                "Controlled Clinical Trial",
            }
        ),
        frozenset(),
    ),  # fmt: skip
    (
        "genetic association",
        frozenset(),
        frozenset(
            {
                "Genome-Wide Association Study",
                "Genetic Association Studies",
                "Mendelian Randomization Analysis",
                "Exome Sequencing",
                "Whole Exome Sequencing",
                "Pedigree",
                "Genetic Linkage",
            }
        ),
    ),  # fmt: skip
    (
        "cohort",
        frozenset({"Observational Study"}),
        frozenset(
            {
                "Cohort Studies",
                "Prospective Studies",
                "Longitudinal Studies",
                "Follow-Up Studies",
                "Retrospective Studies",
            }
        ),
    ),  # fmt: skip
    ("case-control", frozenset(), frozenset({"Case-Control Studies"})),
    ("cross-sectional", frozenset(), frozenset({"Cross-Sectional Studies"})),
    ("case report", frozenset({"Case Reports"}), frozenset()),
    (
        "structure",
        frozenset(),
        frozenset(
            {
                "Crystallography, X-Ray",
                "Cryoelectron Microscopy",
                "Protein Conformation",
                "Models, Molecular",
                "Molecular Dynamics Simulation",
            }
        ),
    ),  # fmt: skip
    (
        "animal model",
        frozenset(),
        frozenset(
            {
                "Disease Models, Animal",
                "Mice, Knockout",
                "Mice, Transgenic",
                "Gene Knockout Techniques",
            }
        ),
    ),  # fmt: skip
    (
        "cells / in vitro",
        frozenset(),
        frozenset(
            {
                "Cells, Cultured",
                "Cell Line",
                "Cell Line, Tumor",
                "HEK293 Cells",
                "HeLa Cells",
                "In Vitro Techniques",
                "Induced Pluripotent Stem Cells",
                "Organoids",
            }
        ),
    ),  # fmt: skip
    ("review", frozenset({"Review"}), frozenset()),
)

# MeSH check tags and model headings → species.
SPECIES: dict[str, str] = {
    "Humans": "human",
    "Mice": "mouse",
    "Rats": "rat",
    "Zebrafish": "zebrafish",
    "Drosophila melanogaster": "fruit fly",
    "Caenorhabditis elegans": "C. elegans",
    "Saccharomyces cerevisiae": "budding yeast",
    "Swine": "pig",
    "Dogs": "dog",
    "Macaca mulatta": "rhesus macaque",
    "Rabbits": "rabbit",
    "Xenopus laevis": "Xenopus laevis",
    "Cricetinae": "hamster",
}


def study_design(
    publication_types: Iterable[str], mesh_descriptors: Iterable[str], *, indexed: bool = True
) -> str:
    """The article's design: the first ``DESIGNS`` row its publication types or MeSH
    headings match; "not indexed" when it has neither MeSH nor a specific publication type."""
    types, mesh = set(publication_types), set(mesh_descriptors)
    for design, by_type, by_mesh in DESIGNS:
        if types & by_type or mesh & by_mesh:
            return design
    return "other primary research" if indexed else "not indexed"


def mesh_species(mesh_descriptors: Iterable[str]) -> list[str]:
    """The species an indexed article studied, from its MeSH check tags."""
    return sorted({SPECIES[d] for d in mesh_descriptors if d in SPECIES})
