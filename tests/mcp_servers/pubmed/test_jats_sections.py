# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The PMC body by section, and the pass-2 screening excerpt built from it."""

from __future__ import annotations

import re

import pytest

from agents.screening.screening.excerpt import build_excerpt, cut_to_mentions
from mcp_servers.pubmed.tools import _parse_jats_body, _parse_jats_sections

_JATS = b"""<?xml version="1.0"?>
<pmc-articleset><article><body>
  <p>A loose opening paragraph.</p>
  <sec sec-type="intro"><title>Opening</title><p>Earlier work on GENE9.</p></sec>
  <sec><title>Materials and Methods</title><p>We knocked out GENE9 in mice.</p>
    <sec><title>Animals</title><p>Mice were housed in cages.</p></sec>
  </sec>
  <sec><title>Results and discussion</title>
    <p>GENE9 loss lowered the marker.</p>
    <fig><label>Fig 1</label><caption><title>GENE9 knockout.</title><p>Bars show means.</p></caption>
      <graphic/></fig>
    <table-wrap><caption><p>Table of cohorts.</p></caption><table><tr><td>row noise</td></tr></table></table-wrap>
  </sec>
  <sec><title>Concluding remarks</title><p>GENE9 is a candidate.</p></sec>
  <sec><title>Acknowledgements</title><p>We thank the funders.</p></sec>
  <ref-list><ref><p>Citation noise</p></ref></ref-list>
</body></article></pmc-articleset>"""


def test_sections_take_their_kind_from_sec_type_then_title_then_parent():
    sections = _parse_jats_sections(_JATS)
    assert [(s.kind, s.title) for s in sections] == [
        ("other", ""),
        ("introduction", "Opening"),  # sec-type="intro", title says nothing
        ("methods", "Materials and Methods"),
        ("methods", "Animals"),  # inherited from the enclosing section
        ("results", "Results and discussion"),  # combined section counts as results
        ("conclusions", "Concluding remarks"),
        ("other", "Acknowledgements"),
    ]


def test_captions_stay_with_their_section_and_table_content_is_dropped():
    results = next(s for s in _parse_jats_sections(_JATS) if s.kind == "results")
    assert results.paragraphs == ["GENE9 loss lowered the marker."]
    assert results.captions == ["GENE9 knockout. Bars show means.", "Table of cohorts."]
    flat = _parse_jats_body(_JATS)
    assert "row noise" not in flat and "Citation noise" not in flat
    assert "Bars show means." not in flat  # the flat body stays prose only


def test_a_body_without_sections_is_one_other_section():
    xml = b"<article><body><p>One.</p><p>Two.</p></body></article>"
    [only] = _parse_jats_sections(xml)
    assert (only.kind, only.paragraphs) == ("other", ["One.", "Two."])
    assert _parse_jats_sections(b"<article><front/></article>") == []


# --- excerpt ----------------------------------------------------------------------------

_TARGET = re.compile(r"(?<![A-Za-z0-9])GENE9(?![A-Za-z0-9])")


def _mentions(text: str) -> int:
    return len(_TARGET.findall(text))


def _names_disease(text: str) -> bool:
    return "disease one" in text


def _sec(kind: str, *paragraphs: str, captions: tuple[str, ...] = ()) -> dict:
    return {"kind": kind, "title": kind, "paragraphs": list(paragraphs), "captions": list(captions)}


def _excerpt(sections: list[dict], max_chars: int = 8000) -> str:
    return build_excerpt(
        sections, mentions=_mentions, names_disease=_names_disease, max_chars=max_chars
    )


_PAPER = [
    _sec("introduction", "GENE9 was first linked to disease one long ago."),
    _sec("methods", "GENE9 was silenced with siRNA."),
    _sec(
        "results",
        "Cells grew. GENE9 silencing lowered the marker. Viability held. Nothing else changed.",
        "In disease one samples GENE9 was raised.",
        captions=("GENE9 staining in tissue.",),
    ),
    _sec("discussion", "First discussion point about GENE9.", "A later point.", "Final words."),
    _sec("conclusions", "The study supports GENE9 as a target."),
]


def test_excerpt_order_count_line_then_conclusions_then_ranked_snippets():
    lines = _excerpt(_PAPER).splitlines()
    assert (
        lines[0]
        == "Target named: results 3, introduction 1, methods 1, discussion 1, conclusions 1."
    )
    assert lines[1] == "[conclusions] The study supports GENE9 as a target."
    labels = [line.split("]", 1)[0] + "]" for line in lines[2:]]
    assert labels == [
        "[results]",
        "[results]",
        "[figure/table caption]",
        "[discussion]",
        "[methods]",
    ]
    # Within results, the paragraph naming the disease comes first.
    assert lines[2] == "[results] In disease one samples GENE9 was raised."


def test_excerpt_leaves_out_the_introduction():
    assert "first linked" not in _excerpt(_PAPER)


def test_excerpt_cuts_a_paragraph_to_the_mention_and_one_sentence_either_side():
    assert "[results] Cells grew. GENE9 silencing lowered the marker. Viability held. …" in (
        _excerpt(_PAPER)
    )
    assert cut_to_mentions("A. B. C. GENE9 here. D. E.", _mentions) == "… C. GENE9 here. D. …"


def test_without_conclusions_the_last_two_discussion_paragraphs_stand_in():
    paper = [s for s in _PAPER if s["kind"] != "conclusions"]
    lines = _excerpt(paper).splitlines()
    assert lines[1:3] == ["[end of discussion] A later point.", "[end of discussion] Final words."]


def test_a_target_named_only_in_the_introduction_is_reported_as_such():
    paper = [
        _sec("introduction", "GENE9 has been studied."),
        _sec("results", "We measured another protein."),
    ]
    assert _excerpt(paper).splitlines()[0] == (
        "Target named: introduction 1 (only in the introduction)."
    )
    assert _excerpt([_sec("results", "Nothing relevant.")]).startswith(
        "Target named: nowhere in the full text."
    )


@pytest.mark.parametrize("max_chars", [30, 120, 300, 8000])
def test_excerpt_never_exceeds_its_limit(max_chars):
    long_paper = [*_PAPER, _sec("results", *(f"GENE9 result {i}. " * 40 for i in range(50)))]
    assert len(_excerpt(long_paper, max_chars)) <= max_chars


def test_a_body_without_sections_gives_target_paragraphs_in_order_and_the_last_two():
    flat = "\n\n".join(["Intro text.", "GENE9 one.", "Middle.", "GENE9 two.", "Tail A.", "Tail B."])
    out = build_excerpt(
        [], mentions=_mentions, names_disease=_names_disease, max_chars=8000, flat_text=flat
    ).splitlines()
    assert out == [
        "Target named: 2 times (the full text has no sections).",
        "[paragraph] GENE9 one.",
        "[paragraph] GENE9 two.",
        "[end of text] Tail A.",
        "[end of text] Tail B.",
    ]
