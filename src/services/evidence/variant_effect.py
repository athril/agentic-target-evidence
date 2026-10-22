# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A variant's effect on the target, looked up, never read from a sentence.

A statement names a variant of the target by identifier (its rs id, or its HGVS when it
has none) and the allele the sentence's carriers carry (services/evidence/grounding.py).
Its effect on the target is, in order:

1. its consequence: a truncating change (a stop or a frameshift in the tmVar HGVS) lowers
   the target;
2. its curated direction: Open Targets rows for the target that annotate the variant as
   loss or gain of function (``directionOnTarget``), when they agree;
3. its own QTL: the variant's beta in the target's expression and protein QTL credible
   sets (Open Targets), the tissue signs netted as in the colocalisation reading. A QTL
   measures amount, not activity, so it is not read for a variant that changes the
   target's protein (missense, in-frame, start or stop lost; Open Targets' Sequence
   Ontology consequences): TYK2 P1104A, a protective variant that impairs the kinase,
   raises TYK2 expression. Nor is a pQTL whose lead variant is a missense change of the
   target: an affinity assay can lose its epitope on the variant protein, which reads as
   less protein.

Data describe the alternate allele, so the sentence's allele is matched first: a residue
against the target's protein change at the same position (transcripts number residues
differently), a base against the forward-strand alleles or their
complement (a paper may write the gene's strand). A palindromic pair (A/T, C/G) cannot be
oriented and is skipped. Carriers of the reference allele have the opposite effect. A QTL
measures amount, not activity; a variant whose own QTL is weak but which tags another
variant gives no lookup and stays unknown.

"The same variant" is the same identifier (``variant_key``): tmVar gives "R46L",
"p.Arg46Leu" and "rs11591147" one rs id, so the registry is a grouping by key.

Pure module: the Open Targets data are fetched by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from schemas.statement import EffectSource, TargetChange, TargetStatement
from services.evidence.grounding import Mention, hgvs_alleles, residue
from services.evidence.statements import truncating

AlleleRole = Literal["alt", "ref"]
_COMPLEMENT = {"A": "T", "T": "A", "C": "G", "G": "C"}
_PALINDROMIC = ({"A", "T"}, {"C", "G"})
_AMOUNT_QTL_TYPES = frozenset({"eqtl", "pqtl"})
# Sequence Ontology consequences that change the target's protein: its activity, not only
# its amount, changes, so a QTL says nothing of the direction.
_PROTEIN_ALTERING = frozenset(
    {
        "missense_variant",
        "inframe_insertion",
        "inframe_deletion",
        "protein_altering_variant",
        "start_lost",
        "stop_lost",
    }
)


def variant_key(statement: TargetStatement) -> str:
    """The variant's identity: its rs id, or its HGVS when it has none."""
    return statement.exposure_id.lower()


def group_by_variant(statements: Iterable[TargetStatement]) -> dict[str, list[TargetStatement]]:
    """Variant statements grouped by ``variant_key``."""
    out: dict[str, list[TargetStatement]] = {}
    for st in statements:
        if st.exposure_kind == "variant" and st.exposure_id:
            out.setdefault(variant_key(st), []).append(st)
    return out


def _protein_change(change: str) -> tuple[str, str, str] | None:
    """(reference, position, variant) of a change written "R46L"."""
    c = (change or "").strip()
    if len(c) < 3 or not c[0].isalpha() or not c[1:-1].isdigit():
        return None
    return residue(c[0]), c[1:-1], residue(c[-1])


def _hgvs_position(hgvs: str) -> str:
    m = re.match(r"^p\.\(?[A-Z](?:[a-z]{2})?(\d+)", (hgvs or "").strip())
    return m.group(1) if m else ""


def allele_role(allele: str, hgvs: str, data: Any) -> AlleleRole | None:
    """Whether the sentence's carriers carry the alternate or the reference allele of the
    Open Targets variant ``data``; None when it cannot be oriented."""
    a = (allele or "").strip()
    if not a:
        return None
    alleles = hgvs_alleles(hgvs)
    if alleles is not None and (hgvs or "").startswith("p."):
        change = _protein_change(str(getattr(data, "amino_acid_change", "") or ""))
        if change is None or change[1] != _hgvs_position(hgvs):
            return None
        ref, _, alt = change
        return "alt" if a == alt else "ref" if a == ref else None
    ref, alt = str(getattr(data, "ref", "") or ""), str(getattr(data, "alt", "") or "")
    if len(ref) != 1 or len(alt) != 1 or len(a) != 1:
        return None
    if {ref, alt} in _PALINDROMIC:
        return None
    for base in (a, _COMPLEMENT.get(a, "")):
        if base == alt:
            return "alt"
        if base == ref:
            return "ref"
    return None


def _flip(change: TargetChange) -> TargetChange:
    return {"decrease": "increase", "increase": "decrease"}.get(change, "none")  # type: ignore[return-value]


def curated_change(data: Any) -> TargetChange | None:
    """The alternate allele's change from curated rows, when they agree."""
    found: dict[str, TargetChange] = {"LoF": "decrease", "GoF": "increase"}
    changes = {found[d] for d in getattr(data, "curated", []) or [] if d in found}
    return changes.pop() if len(changes) == 1 else None


def qtl_change(data: Any) -> TargetChange | None:
    """The alternate allele's change of the target's amount from its own QTL betas, the
    signs netted across tissues; none for a variant that changes the target's protein, and
    a pQTL led by a missense change of the target is left out."""
    if _PROTEIN_ALTERING & set(getattr(data, "consequences", []) or []):
        return None
    signs = [
        1.0 if q.beta > 0 else -1.0
        for q in getattr(data, "qtls", []) or []
        if q.study_type in _AMOUNT_QTL_TYPES
        and q.beta
        and not (q.study_type == "pqtl" and q.lead_missense_in_target)
    ]
    if not signs or not sum(signs):
        return None
    return "increase" if sum(signs) > 0 else "decrease"


def effect_of(
    mention: Mention, variants: Sequence[Any] = ()
) -> tuple[TargetChange, EffectSource] | None:
    """The change the carriers of ``mention``'s allele have in the target, and its source
    (module docstring); ``variants`` are the Open Targets records of its rs id."""
    if truncating(mention.hgvs):
        return "decrease", "consequence"
    for data in variants:
        role = allele_role(mention.allele, mention.hgvs, data)
        if role is None:
            continue
        for change, source in ((curated_change(data), "curated"), (qtl_change(data), "qtl")):
            if change is not None:
                return (change if role == "alt" else _flip(change)), source  # type: ignore[return-value]
    return None


def lookup(
    variants: Mapping[str, Sequence[Any]],
) -> Any:
    """A ``statements.assemble`` variant-effect function over Open Targets records by rs
    id."""

    def find(mention: Mention) -> tuple[TargetChange, EffectSource] | None:
        return effect_of(mention, variants.get(mention.rsid, ()))

    return find
