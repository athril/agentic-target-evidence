# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Where a secreted protein acts: beside the disease tissue, the tissues that make it.

A secreted protein (a cytokine, a circulating enzyme or hormone) acts where its
receptors or substrates are, often far from where it is made. Low expression in the
disease tissue then says nothing against its mechanism (the biology lens's expression
caveat read it as a gap), and its on-target effects follow its circulating level, so
the tissues that produce it are exposed to its modulation too (the safety lens's tissue
question looked only at the disease tissue and bulk rank). UniProt's subcellular
location says which proteins are secreted.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

_SECRETED = ("secreted", "extracellular space")
_PRODUCERS = 3


def is_secreted(locations: Iterable[str]) -> bool:
    """UniProt places the protein outside the cell."""
    return any(term in str(loc).lower() for loc in locations for term in _SECRETED)


def site_of_action_note(
    gene: str, disease_tissue: str, producing_tissues: Sequence[str], *, lens: str
) -> str:
    """Prompt block for a secreted target (``lens`` is "biology" or "safety")."""
    producers = ", ".join(producing_tissues[:_PRODUCERS]) or "the tissues that express it"
    where = disease_tissue or "the disease tissue"
    head = (
        f"Site of action (pre-computed): {gene} is a secreted protein (UniProt). It acts "
        f"where its receptors or substrates are; the tissues that make most of it are "
        f"{producers}."
    )
    if lens == "safety":
        return (
            f"{head} Its modulation changes its level in circulation: count both {where} and "
            f"the producing tissues ({producers}) as tissues where on-target effects can occur."
        )
    return (
        f"{head} Its expression in {where} is not needed for its mechanism there: do not "
        f"read low expression in {where} as evidence against the mechanism."
    )
