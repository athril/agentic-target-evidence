# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Perturbation outcomes in models of the disease (biology_lens/perturbations.py).

The failures: the biology lens read a target whose blockade worsened models of the
disease as mechanism support, and the question that replaced it wrote another gene's
knockout as the target's. The perturbed gene is now a grounded mention.
"""

from __future__ import annotations

from typing import Any

import pytest

import agents.interpretation.biology_lens.perturbations as pt
from mcp_servers.pubtator.tools import PubTatorAnnotation, PubTatorDocument, PubTatorPassage
from schemas.statement import TargetStatement
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.grounding import TargetIdentity, ground_paper
from services.evidence.statements import Selection, assemble

_PAPERS = {f"e{i}": {"pmid": str(i), "scope": "related"} for i in range(1, 10)}


def _st(source: str = "PMID:1", **kw: Any) -> TargetStatement:
    base: dict[str, Any] = {
        "source": source,
        "sentence": 2,
        "text": "x",
        "exposure_kind": "genetic_perturbation",
        "exposure_text": "Gene-a knockout",
        "species": "10090",
        "effect_on_target": "decrease",
        "effect_source": "text",
        "outcome_kind": "disease",
        "outcome_text": "phenotype two",
        "outcome_direction": "higher",
        "design": "preclinical",
    }
    return TargetStatement(**{**base, **kw})


def _sentence(gene_id: str, chemical: str = ""):
    text = "Knockout of Gene-a worsened phenotype two in mice."
    annotations = [
        PubTatorAnnotation(type="Gene", text="Gene-a", offset=12, length=6, identifier=gene_id),
        PubTatorAnnotation(
            type="Disease", text="phenotype two", offset=28, length=13, identifier="MESH:D1"
        ),
        PubTatorAnnotation(type="Species", text="mice", offset=45, length=4, identifier="10090"),
    ]
    if chemical:
        text = f"{chemical} worsened phenotype two in mice."
        annotations = [
            PubTatorAnnotation(type="Chemical", text=chemical, offset=0, length=len(chemical)),
            PubTatorAnnotation(
                type="Disease",
                text="phenotype two",
                offset=len(chemical) + 10,
                length=13,
                identifier="MESH:D1",
            ),
        ]
    doc = PubTatorDocument(
        pmid="1",
        passages=[PubTatorPassage(kind="abstract", offset=0, text=text, annotations=annotations)],
    )
    target = TargetIdentity(symbol="GENE-A", ncbi_id="100", ortholog_ids=["200"])
    (sentence,) = ground_paper(doc, target, drug_names=["DRUGX"]).sentences
    return sentence


def _read(sentence, kind: str) -> TargetStatement:
    selection = Selection(
        id="S1",
        exposure=1,
        exposure_kind=kind,
        change="decrease",
        outcome=2,
        outcome_direction="higher",
        design="preclinical",
    )
    return assemble(sentence, selection, drug_effects={"DRUGX": "decrease"})


def test_a_mouse_orthologs_knockout_is_the_targets():
    st = _read(_sentence("200"), "genetic_perturbation")

    (obs,) = pt.perturbation_observations([st], _PAPERS)
    assert (st.species, obs.perturbation, obs.outcome) == ("10090", "decrease", "harm")


def test_a_knockout_of_another_gene_the_target_modifies_is_not_the_targets():
    st = _read(_sentence("999"), "genetic_perturbation")

    assert st.exposure_kind == "none"
    assert pt.perturbation_observations([st], _PAPERS) == []


def test_a_tool_compound_with_no_known_target_is_unknown_and_a_known_drug_counts():
    tool = _read(_sentence("", chemical="compound-7"), "drug")
    drug = _read(_sentence("", chemical="DRUGX"), "drug")

    assert tool.exposure_kind == "none"
    assert pt.perturbation_observations([tool], _PAPERS) == []
    (obs,) = pt.perturbation_observations([drug], _PAPERS)
    assert obs.perturbation == "decrease"


def test_a_drug_in_people_is_not_a_model_perturbation():
    human = _st(exposure_kind="drug", effect_source="drug", species="9606", design="rct")

    assert pt.perturbation_observations([human], _PAPERS) == []


def test_one_vote_per_paper_from_papers_about_the_disease():
    papers = {**_PAPERS, "e5": {"pmid": "5", "scope": "none"}}
    obs = pt.perturbation_observations(
        [
            _st("PMID:1"),
            _st("PMID:1"),
            _st("PMID:5"),  # another disease
            _st("PMID:6", outcome_direction="none"),
            _st("PMID:7", effect_on_target="none"),
        ],
        papers,
    )

    assert [(o.source, o.favours) for o in obs] == [("PMID:1", "increase")]


def test_an_in_scope_outcome_counts_whatever_the_papers_scope():
    papers = {"e5": {"pmid": "5", "scope": "none"}}
    assert len(pt.perturbation_observations([_st("PMID:5", in_scope=True)], papers)) == 1


def test_a_paper_whose_statements_point_both_ways_casts_no_vote():
    raised = _st(effect_on_target="increase")

    assert pt.perturbation_observations([_st(), raised], _PAPERS) == []


def _reading(n_papers: int, direction: str = "higher") -> dict[str, Any]:
    statements = [_st(f"PMID:{i}", outcome_direction=direction) for i in range(1, n_papers + 1)]
    obs = pt.perturbation_observations(statements, _PAPERS)
    return pt.perturbation_reading("decrease", obs).model_dump(mode="json")


def test_one_paper_says_nothing_and_two_unanimous_papers_decide() -> None:
    assert _reading(1)["verdict"] == "unknown"
    assert _reading(2)["verdict"] == "contradicts"
    assert _reading(2, "lower")["verdict"] == "supports"


def _verdict(overall: str = "support") -> LensVerdict:
    # A neutral verdict reaching the rule has an uncertain axis (scoring aligns a neutral
    # verdict whose axes are all favourable).
    mechanism = None if overall == "neutral" else True
    return LensVerdict(
        run_id=__import__("uuid").uuid4(),
        trace_id="t",
        lens="biology",
        target_gene="GENE-A",
        disease="disease one",
        overall_verdict=overall,
        confidence=0.6,
        axes=[
            AxisVerdict(
                axis="mechanism_of_action", verdict=mechanism, confidence=0.6, rationale="m"
            ),
            AxisVerdict(axis="druggability", verdict=True, confidence=0.6, rationale="d"),
        ],
    )


def test_models_worsened_by_the_requested_perturbation_oppose() -> None:
    """Human genetics is the genetics lens's question: it no longer softens this."""
    out = pt.apply_perturbation_axis(_verdict(), _reading(3))

    axis = next(a for a in out.axes if a.axis == pt.AXIS)
    assert axis.verdict is False and axis.confidence > 0.3
    assert out.overall_verdict == "oppose"
    assert any(f.rule_id == "biology.perturbation_direction" for f in out.validation_flags)


@pytest.mark.parametrize(
    ("papers", "direction", "value"), [(3, "lower", True), (1, "higher", None)]
)
def test_supportive_or_unreplicated_models_leave_the_verdict(papers, direction, value) -> None:
    out = pt.apply_perturbation_axis(_verdict("neutral"), _reading(papers, direction))

    assert out.overall_verdict == "neutral"
    assert next(a for a in out.axes if a.axis == pt.AXIS).verdict is value


def test_no_reading_no_axis() -> None:
    v = _verdict()
    assert pt.apply_perturbation_axis(v, {}) is v
    assert pt.render_perturbation_block({}) == ""


def test_the_prompt_states_the_axis_and_each_papers_outcome() -> None:
    block = pt.render_perturbation_block(_reading(2))

    assert "axis perturbation_direction set by rule: false" in block
    assert "favours raising the target's activity" in block
