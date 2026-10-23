# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What one sentence says about the target, read from grounded mentions.

A ``TargetStatement`` is assembled in code (services/evidence/statements.py) from a
sentence's PubTator3 mentions (services/evidence/grounding.py) and the mentions the model
selected among them. Its entities are identifiers, never words, so two consequences
hold by construction:

* an ``observed_level`` (a level measured in people or samples) never sets
  ``effect_on_target``;
* an exposure that is another gene is not a change of the target.

``effect_on_target`` comes from data where the exposure is a variant (its consequence or
its own QTL) or a drug (Open Targets known drugs); only a genetic perturbation of the
target (a knockout, an overexpression) takes it from the sentence, as ``text``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ExposureKind = Literal["variant", "drug", "genetic_perturbation", "observed_level", "none"]
TargetChange = Literal["decrease", "increase", "none"]
EffectSource = Literal["consequence", "curated", "qtl", "drug", "text", "none"]
OutcomeKind = Literal["disease", "phenotype", "biomarker", "none"]
OutcomeDirection = Literal["higher", "lower", "none"]
# Whether the sentence reports its outcome's change as statistically significant. A "no"
# removes the outcome direction: a null result is no effect, whichever way the numbers
# lean. Most sentences do not say (38% of the typed ones did on 2026-10-22), and those
# keep their direction.
Significance = Literal["yes", "no", "not_stated"]
StudyDesign = Literal[
    "rct",
    "meta_analysis",
    "mendelian_randomisation",
    "genetic_association",
    "observational",
    "preclinical",
    "other",
]


class TargetStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str  # "PMID:123"
    sentence: int  # index of the sentence in the paper's PubTator passages
    text: str  # the sentence, verbatim
    exposure_kind: ExposureKind = "none"
    exposure_id: str = ""  # rs id or HGVS, drug key, NCBI Gene id
    exposure_text: str = ""  # the exposure mention as written
    allele: str = ""  # the allele the sentence's carriers carry (a base or a residue)
    species: str = ""  # NCBI taxon id ("9606")
    effect_on_target: TargetChange = "none"
    effect_source: EffectSource = "none"
    outcome_kind: OutcomeKind = "none"
    outcome_id: str = ""  # MeSH, HPO or chemical id
    outcome_text: str = ""  # the outcome mention as written
    outcome_direction: OutcomeDirection = "none"
    in_scope: bool = False  # the outcome is within the run's disease scope, by id
    significance: Significance = "not_stated"
    design: StudyDesign = "other"
