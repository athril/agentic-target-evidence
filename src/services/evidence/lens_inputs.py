# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The structured inputs each lens needs to do its job, and which ones a run lacks.

A lens that runs without one of these still returns a verdict, reasoning from
literature alone, and nothing in the report said so: a cache bug once kept gnomAD,
ClinVar and the gene-disease curations from every genetics lens of a panel, which then
quoted constraint values that no source had supplied. Only sources that answer for
every human protein-coding gene are listed. A missing trial, label or patent is a
finding about the target, not a gap in the run.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class LensInput:
    label: str
    source_prefixes: tuple[str, ...]  # Evidence.source prefixes that supply it


_CONSTRAINT = LensInput("gnomAD constraint", ("gnomad:",))
_CLINVAR = LensInput("ClinVar variant summary", ("gnomad_clinvar:",))
_EXPRESSION = LensInput("tissue expression (GTEx/HPA)", ("gtex_v8:", "gtex_hpa:", "hpa:"))
_DEPENDENCY = LensInput(
    "cell-line dependency (DepMap/Project Score)", ("depmap:", "project_score:")
)
_TRACTABILITY = LensInput(
    "target tractability (UniProt/ChEMBL/DGIdb)", ("uniprot:", "chembl:", "dgidb:")
)

LENS_INPUTS: dict[str, tuple[LensInput, ...]] = {
    "genetics": (_CONSTRAINT, _CLINVAR),
    "biology": (_EXPRESSION, _DEPENDENCY, _TRACTABILITY),
    "safety": (_CONSTRAINT, _EXPRESSION, _DEPENDENCY),
}


def missing_lens_inputs(
    lens: str, retrieved_sources: Iterable[str], kept_sources: Iterable[str]
) -> list[str]:
    """Labels of the lens's inputs absent from what it reads, saying why each is absent."""
    retrieved, kept = list(retrieved_sources), list(kept_sources)
    missing: list[str] = []
    for need in LENS_INPUTS.get(lens, ()):
        if any(s.startswith(need.source_prefixes) for s in kept):
            continue
        if any(s.startswith(need.source_prefixes) for s in retrieved):
            missing.append(f"{need.label} (retrieved but not kept by screening)")
        else:
            missing.append(f"{need.label} (not retrieved)")
    return missing


def missing_inputs_note(missing: list[str]) -> str:
    """Prompt block telling the lens which inputs it does not have."""
    if not missing:
        return ""
    return (
        "Inputs missing from this run (deterministic): "
        + "; ".join(missing)
        + ". Do not state values, bands or counts that would come from them; where they "
        "matter, say the data were unavailable in this run.\n"
    )
