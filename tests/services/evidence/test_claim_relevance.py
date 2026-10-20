# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Restatements shown once and literature ranked by the lens's own signal
(services/evidence/claim_relevance.py, wired in _lens_base._shown_claims).

The failure they prevent: on well-studied pairs a lens saw a few percent of its claims,
and many of the slots went to papers repeating one finding.
"""

from __future__ import annotations

import json

from agents.interpretation._lens_base import _claims_to_json, _shown_claims
from services.evidence.claim_relevance import (
    Relevance,
    collapse_restatements,
    relevance_from_spec,
)
from services.evidence.literature_selection import rank_literature
from tests.services.evidence.test_literature_selection import _claim, _index, _paper

_FINDING = "GENE-A knockdown reduced the marker by 40% in disease a mice"


def _papers(n: int, title: str = "GENE-A in disease a") -> list:
    return [_paper(f"{title} {i}", f"GENE-A was studied in disease a ({i}).") for i in range(n)]


def test_ten_papers_stating_one_finding_collapse_to_one_with_nine_agreeing():
    papers = _papers(10)
    claims = [
        _claim(p, _FINDING.replace("40%", f"{40 + i}%"), topics=["biology"])
        for i, p in enumerate(papers)
    ]
    groups = collapse_restatements(claims, Relevance(gene_terms=["GENE-A"]))
    assert [len(g) for g in groups] == [10]
    assert groups[0][0] is claims[0]  # the highest-ranked member represents the group


def test_two_different_findings_never_collapse():
    a, b = _papers(2)
    claims = [
        _claim(a, _FINDING, topics=["biology"]),
        _claim(b, "GENE-A overexpression increased fibrosis in disease a rats", topics=["biology"]),
    ]
    assert len(collapse_restatements(claims, Relevance(gene_terms=["GENE-A"]))) == 2


def test_restatements_within_one_paper_are_not_collapsed():
    [paper] = _papers(1)
    claims = [_claim(paper, _FINDING, topics=["biology"]) for _ in range(3)]
    assert len(collapse_restatements(claims, Relevance())) == 3


def test_typed_allele_effects_collapse_by_change_risk_and_scope():
    papers = _papers(4)
    claims = [
        _claim(p, f"Variant rs{i} lowered risk", topics=["genetics"]) for i, p in enumerate(papers)
    ]
    keys = {
        str(claims[0].evidence_id): "decrease|benefit|in_scope",
        str(claims[1].evidence_id): "decrease|benefit|in_scope",
        str(claims[2].evidence_id): "decrease|benefit|other",
        str(claims[3].evidence_id): "increase|benefit|in_scope",
    }
    groups = collapse_restatements(claims, Relevance(restatement_keys=keys))
    assert [len(g) for g in groups] == [2, 1, 1]


def test_every_member_stays_citable_and_the_rendered_claim_says_how_many_agree():
    papers = _papers(5)
    claims = [_claim(p, _FINDING, topics=["biology"]) for p in papers]
    index = _index(papers)
    relevance = Relevance(gene_terms=["GENE-A"])
    shown = _shown_claims(claims, lens="biology", papers=index, relevance=relevance)
    assert {c.evidence_id for c in shown} == {c.evidence_id for c in claims}
    rendered = json.loads(
        _claims_to_json(claims, lens="biology", papers=index, relevance=relevance)
    )
    [item] = rendered
    assert item["agree"] == "+4 papers agree"
    assert len(item["agreeing_claim_ids"]) == 4


def test_the_lens_signal_ranks_after_scope_and_before_design():
    """Same evidence class: the signal lifts a paper over its scope peers, never over a
    paper in better scope."""
    plain, signalled = (
        _paper(t, f"{t}.")
        for t in (
            "GENE-A deletion protects mice from disease a",
            "Loss of GENE-A spares hamsters from disease a",
        )
    )
    other = _paper("GENE-A in another condition", "GENE-A in another condition.")
    papers = [plain, signalled, other]
    claims = [_claim(p, f"{p.extra['title']} finding", topics=["biology"]) for p in papers]
    index = _index(papers)
    ids = [str(p.evidence_id) for p in papers]
    without = [str(c.source_evidence_id) for c in rank_literature(claims, index, "biology")]
    with_signal = [
        str(c.source_evidence_id)
        for c in rank_literature(claims, index, "biology", {ids[1], ids[2]})
    ]
    assert without == ids
    assert with_signal == [ids[1], ids[0], ids[2]]


def test_signals_come_from_the_runs_readings():
    papers = _papers(2)
    claims = [_claim(p, _FINDING, topics=["genetics"]) for p in papers]
    cid = str(claims[0].evidence_id)
    spec = {
        "target_gene": "GENE-A",
        "direction_reading": {
            "observations": [
                {
                    "evidence": "genetics_literature",
                    "source": cid,
                    "perturbation": "decrease",
                    "outcome": "benefit",
                    "in_scope": True,
                }
            ]
        },
    }
    rel = relevance_from_spec("genetics", spec, claims, None)
    assert rel.signal_papers == {str(papers[0].evidence_id)}
    assert rel.restatement_keys == {cid: "decrease|benefit|in_scope"}
    assert relevance_from_spec("commercial", spec, claims, None).signal_papers == set()
