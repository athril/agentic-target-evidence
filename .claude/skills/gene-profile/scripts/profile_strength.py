# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Evidence strength per cited paper, from its records: study design (PubMed publication
types and MeSH), species (PubTator), last author, and n when the abstract states it.

Deterministic, no LLM. Each field says where it came from; a field the records do not give
is "not stated", never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def paper_metadata(meta: Any) -> dict[str, Any]:
    """The indexing fields kept per paper from a PubMed ``ArticleMetadata``: publication
    types, MeSH headings with the major-topic flag, whether MeSH indexing has happened, and
    every author (esummary keeps six, so the last author would be lost)."""
    if meta is None:
        return {"publication_types": [], "mesh": [], "indexed": False, "authors": []}
    return {
        "publication_types": list(meta.publication_types),
        "mesh": [
            {"descriptor": h.descriptor, "ui": h.ui, "major": h.major} for h in meta.mesh_headings
        ],
        "indexed": bool(meta.indexed),
        "authors": list(getattr(meta, "authors", None) or []),
    }


# ── per paper ──────────────────────────────────────────────────────────────────────

# "n = 1,234", "N=56", "1,234 patients": the sample size an abstract states.
_N_RE = re.compile(
    r"\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)"
    r"|\b(\d{1,3}(?:,\d{3})+|\d+)\s+(?:[a-zA-Z-]+\s+){0,2}?"
    r"(?:patients|participants|subjects|individuals|cases|controls|carriers|people|adults|"
    r"children|women|men|families|volunteers|mice|rats|animals)\b"
)
_HUMAN_DESIGNS = {
    "meta-analysis", "genetic association", "cohort", "case-control", "cross-sectional",
    "case report",
}  # fmt: skip
_TRIAL_DESIGNS = {"randomized controlled trial", "clinical trial"}
_CELL_DESIGNS = {"cells / in vitro", "structure"}
MODEL_SPECIES = {
    "mouse", "rat", "zebrafish", "fruit fly", "C. elegans", "budding yeast", "fission yeast",
    "pig", "dog", "rhesus macaque", "crab-eating macaque", "rabbit", "Xenopus laevis",
    "Xenopus tropicalis", "hamster", "golden hamster", "Chinese hamster", "chicken", "sheep",
    "cattle",
}  # fmt: skip


def stated_n(abstract: str) -> int | None:
    """The largest sample size the abstract states, or None ("n not stated")."""
    found = [int((a or b).replace(",", "")) for a, b in _N_RE.findall(abstract or "")]
    return max(found) if found else None


def paper_strength(
    pmid: str, paper: Mapping[str, Any], pubtator: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Design (publication types and MeSH, via the PubMed connector's table), species
    (PubTator organisms, else MeSH check tags), last author and stated n of one paper."""
    from mcp_servers.pubmed.design import mesh_species, study_design

    mesh = [h.get("descriptor", "") for h in paper.get("mesh") or []]
    design = study_design(
        paper.get("publication_types") or [], mesh, indexed=bool(paper.get("indexed"))
    )
    organisms = list((pubtator or {}).get("organisms") or [])
    species = sorted(set(organisms) & ({"human"} | MODEL_SPECIES)) or mesh_species(mesh)
    authors = paper.get("authors") or []
    return {
        "pmid": pmid,
        "design": design,
        "species": species,
        "species_source": "PubTator" if organisms else ("MeSH" if species else ""),
        "last_author": authors[-1] if authors else "",
        "n": stated_n(paper.get("abstract") or ""),
    }


# ── evidence kinds ──────────────────────────────────────────────────────────────────

KINDS = ("human", "animal", "cells", "pharmacology")
KIND_TITLES = {
    "human": "Human (genetics, cohorts, tissues)",
    "animal": "Animal models",
    "cells": "Cells & biochemistry",
    "pharmacology": "Pharmacology & trials",
}
# Database records by the kind of evidence they hold.
RECORD_KINDS: dict[str, str] = {
    **dict.fromkeys(
        ("gnomad_constraint", "gnomad_lof", "gnomad_clinvar", "clinvar", "gwas", "ot_coloc",
         "ot_l2g", "gencc", "clingen", "omim", "orphanet_associations", "hpo", "allelic_series",
         "gtex", "hpa", "expression_atlas", "ot_associations", "ot_association_disease",
         "spoke_anatomy", "genebass", "somatic", "variant_frequencies", "stated_effects"),
        "human",
    ),
    **dict.fromkeys(("ot_mouse", "impc", "orthologs"), "animal"),
    **dict.fromkeys(
        ("uniprot_extra", "uniprot_profile", "alphafold", "string", "reactome", "depmap",
         "project_score", "ncbi_gene", "biogrid_orcs"),
        "cells",
    ),
    **dict.fromkeys(
        ("chembl", "ot_known_drugs", "drug_landscape", "ot_tractability", "dgidb_interactions", "dgidb_categories",
         "ct_gene", "ct_drugs", "ct_gene_disease", "openfda_labels", "openfda_indication",
         "faers", "pubmed_drug_safety", "ot_safety"),
        "pharmacology",
    ),
}  # fmt: skip


def paper_kind(s: Mapping[str, Any]) -> str:
    """The kind of evidence a paper is, from its design then its species; "" for a review
    or an article neither classifies (not counted in a column)."""
    design, species = s.get("design", ""), set(s.get("species") or [])
    if design in _TRIAL_DESIGNS:
        return "pharmacology"
    if design in _HUMAN_DESIGNS:
        return "human"
    if design == "animal model":
        return "animal"
    if design == "cells / in vitro" and species & MODEL_SPECIES:
        return "animal"  # cell lines tagged beside a model organism: the paper worked in it
    if design in _CELL_DESIGNS:
        return "cells"
    if design == "review":
        return ""
    if species & MODEL_SPECIES and "human" not in species:
        return "animal"
    if "human" in species:
        return "human"
    return ""


def key_kind(key: str, strengths: Mapping[str, Mapping[str, Any]]) -> str:
    if key.startswith("pmid:"):
        s = strengths.get(key.removeprefix("pmid:"))
        return paper_kind(s) if s else ""
    if key.startswith("nct:"):
        return "pharmacology"
    return RECORD_KINDS.get(key, "")
