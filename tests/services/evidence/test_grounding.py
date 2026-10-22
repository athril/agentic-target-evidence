# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Grounded mentions (services/evidence/grounding.py) on recorded PubTator3 abstracts.

The failures these prevent: a biomarker's level read as the target's, another gene's
variant read as the target's, and a target whose symbol is another gene's alias ("MET")
never recognised.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_servers.ontology.tools import HGNCResult
from mcp_servers.pubtator.tools import parse_biocjson
from services.evidence.grounding import (
    GroundedPaper,
    GroundedSentence,
    Mention,
    TargetIdentity,
    ground_paper,
    hgvs_alleles,
    needs_rehoming,
    select_sentences,
    split_sentences,
)

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "pubtator"
_CETP = TargetIdentity(symbol="CETP", ncbi_id="1071")
_MET = TargetIdentity(symbol="MET", ncbi_id="4233")
_KIT = TargetIdentity(symbol="KIT", ncbi_id="3815")


def _doc(pmid: str):
    (doc,) = parse_biocjson(json.loads((_FIXTURES / f"{pmid}.json").read_text()))
    return doc


def _mentions(paper: GroundedPaper, kind: str) -> list[Mention]:
    return [m for s in paper.sentences for m in s.mentions if m.kind == kind]


def test_the_cetp_promoter_and_exon_variants_are_the_targets_with_their_alleles():
    paper = ground_paper(_doc("16096813"), _CETP, scope_mesh={"D000544"})
    variants = {m.text: m for m in _mentions(paper, "variant")}

    assert variants["C-629A"].is_target and variants["I405V"].is_target
    assert (variants["C-629A"].rsid, variants["C-629A"].allele) == ("rs1800775", "A")
    assert (variants["I405V"].rsid, variants["I405V"].allele) == ("rs5882", "V")


def test_a_biomarker_is_a_chemical_never_the_target():
    paper = ground_paper(_doc("16096813"), _CETP, scope_mesh={"D000544"})
    cholesterol = [m for m in _mentions(paper, "chemical") if m.text.lower() == "cholesterol"]

    assert cholesterol and not any(m.is_target for m in cholesterol)
    assert all(m.id == "D002784" for m in cholesterol)


def test_a_disease_is_in_scope_by_mesh_id():
    paper = ground_paper(_doc("16096813"), _CETP, scope_mesh={"D000544"})
    diseases = _mentions(paper, "disease")

    assert {m.id for m in diseases} == {"D000544"}
    assert all(m.in_scope for m in diseases)
    other = ground_paper(_doc("16096813"), _CETP, scope_mesh={"D003924"})
    assert not any(m.in_scope for m in _mentions(other, "disease"))


def test_the_approved_symbol_rule_returns_met_to_the_target():
    doc = _doc("42728536")
    sltm = HGNCResult(symbol="SLTM", aliases=["Met", "FLJ13213"])

    assert needs_rehoming(doc, _MET) == {"79811"}
    without = ground_paper(doc, _MET)
    with_rule = ground_paper(doc, _MET, hgnc={"79811": sltm})
    met = [m for m in _mentions(with_rule, "gene") if m.text == "MET"]

    assert not any(m.is_target for m in _mentions(without, "gene") if m.text == "MET")
    assert met and all(m.is_target and m.id == "4233" for m in met)


def test_a_gene_whose_approved_symbol_is_the_text_is_not_rehomed():
    doc = _doc("42728536")
    owner = HGNCResult(symbol="MET", aliases=[])

    paper = ground_paper(doc, _MET, hgnc={"79811": owner})

    assert not any(m.is_target for m in _mentions(paper, "gene") if m.text == "MET")


def test_a_pdgfra_variant_in_a_gist_abstract_is_not_kits():
    paper = ground_paper(_doc("39853155"), _KIT, drug_names=["IMATINIB"])
    (d842v,) = _mentions(paper, "variant")

    assert (d842v.hgvs, d842v.gene_id, d842v.is_target) == ("p.D842V", "5156", False)
    assert any(m.is_target for m in _mentions(paper, "gene") if m.text == "KIT")
    assert {m.drug for m in _mentions(paper, "chemical")} == {"IMATINIB"}


def test_sentences_split_at_boundaries_but_not_at_abbreviations():
    text = "Levels rose (e.g. in plasma). Cases vs. controls differed. J. Smith agreed. 10 fell."

    assert [text[a:b] for a, b in split_sentences(text)] == [
        "Levels rose (e.g. in plasma).",
        "Cases vs. controls differed.",
        "J. Smith agreed.",
        "10 fell.",
    ]


def test_hgvs_alleles_in_their_own_orientation():
    assert hgvs_alleles("c.-629C>A") == ("C", "A")
    assert hgvs_alleles("p.Arg46Leu") == ("R", "L")
    assert hgvs_alleles("p.Y74*") == ("Y", "*")
    assert hgvs_alleles("rs123") is None


def _sentence(index: int, *mentions: Mention) -> GroundedSentence:
    return GroundedSentence(source="PMID:1", index=index, text="x", mentions=list(mentions))


def test_selection_needs_an_exposure_and_an_outcome_and_prefers_claimed_sentences():
    target = Mention(number=1, kind="gene", text="ABC1", is_target=True)
    other = Mention(number=1, kind="gene", text="XYZ2")
    disease = Mention(number=2, kind="disease", text="DISEASE-A")
    paper = GroundedPaper(
        source="PMID:1",
        sentences=[
            _sentence(0, target, disease),
            _sentence(1, other, disease),  # another gene: not selectable
            _sentence(2, target),  # no outcome
            _sentence(3, target, disease),
            _sentence(4, target, disease),
        ],
    )

    assert [s.index for s in select_sentences(paper, cap=2)] == [3, 4]
    assert [s.index for s in select_sentences(paper, claimed=[0], cap=2)] == [0, 4]
