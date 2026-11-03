# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claim checks chosen by where a sentence stands, not by the writer.

BRAF's Biology bottom line said its "activity depends on dimerization with other RAF kinases";
the writer never searched that claim, and the paper that qualifies it (class 1 mutants signal
as monomers) sat a few lines below. Every Snapshot sentence and section bottom line stating
mechanism or clinical status now needs a check of its own (template 6).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import profile_claims as claims  # noqa: E402

DRAFT = """## Snapshot

**In brief:** GENE1 encodes a kinase that phosphorylates MEK1/2 [@pmid:1]. It sits on chromosome 7 [@hgnc]. Selective inhibitors are approved for tumours with a V600E mutation [@label:drugone].

**How we know:**

{{table:how_we_know}}

- Solid: the V600E driver raises kinase activity [@pmid:2].
- Open: which therapy fits non-V600 mutants [@pmid:3].

## Biology

### Function & pathways

**Bottom line:** GENE1 is the kinase that phosphorylates MEK1/2; its activity depends on dimerization with other RAF kinases [@pmid:4]. A second sentence on its partners binds KSR [@pmid:5]. A third sentence is not a bottom line and regulates nothing new [@pmid:6].

- A bullet below the bottom line that binds something [@pmid:7].

## View of the field

1. The mechanism of GENE1 gating. 2. Another paper.

## Evidence gaps & conflicts

Not checked: OMIM and TTD (licence gates off) [@omim].
"""


def test_the_bottom_lines_and_snapshot_are_chosen_and_nothing_else() -> None:
    found = {c["sentence"]: c["kinds"] for c in claims.positional_claims(DRAFT)}
    assert "GENE1 encodes a kinase that phosphorylates MEK1/2." in found
    assert found["Selective inhibitors are approved for tumours with a V600E mutation."] == [
        "clinical"
    ]
    # a Snapshot bullet, keyed without its marker and lead-in
    assert found["the V600E driver raises kinase activity."] == ["mechanism"]
    dimer = next(s for s in found if "dimerization" in s)
    assert found[dimer] == ["mechanism"]
    assert "A second sentence on its partners binds KSR." in found  # the bottom line's second
    assert not any("third sentence" in s or "bullet below" in s for s in found)
    assert not any(
        s.startswith(("which therapy", "It sits", "Not checked", "The mechanism")) for s in found
    )


def test_the_dimerization_bottom_line_cannot_pass_unchecked() -> None:
    positional = claims.positional_claims(DRAFT)
    checks = [{"claim": "GENE1 encodes a kinase that phosphorylates MEK1/2", "terms": ["MEK"]}]
    problems = claims.position_problems(checks, positional)
    assert any("dimerization" in msg for _line, msg in problems)
    assert not any("encodes a kinase" in msg for _line, msg in problems)
    line = next(line for line, msg in problems if "dimerization" in msg)
    assert DRAFT.splitlines()[line - 1].startswith("**Bottom line:**")


def test_a_check_matches_its_sentence_without_citations_or_markup() -> None:
    positional = claims.positional_claims(DRAFT)
    dimer = next(c for c in positional if "dimerization" in c["sentence"])
    entry = {
        "claim": "RAF dimers",
        "sentence": "**Bottom line:** " + dimer["sentence"] + " [@pmid:4]",
    }
    assert not [p for p in claims.position_problems([entry], [dimer])]


def test_a_mechanism_check_names_its_variant_classes_when_the_series_has_several() -> None:
    positional = [c for c in claims.positional_claims(DRAFT) if "dimerization" in c["sentence"]]
    entry = {"sentence": positional[0]["sentence"], "claim": "x"}
    assert claims.position_problems([entry], positional, ["gain", "not typed"]) == []
    [(_line, msg)] = claims.position_problems([entry], positional, ["gain", "loss"])
    assert "variant classes" in msg
    assert (
        claims.position_problems(
            [{**entry, "classes": "all described"}], positional, ["gain", "loss"]
        )
        == []
    )
