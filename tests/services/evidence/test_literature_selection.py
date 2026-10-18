# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Literature ranked by paper (services/evidence/literature_selection.py).

Synthetic papers and diseases ("disease a", gene GENE-A, drug drugamab); each test names
the failure it prevents. None depends on a real gene, disease or drug.
"""

from __future__ import annotations

import itertools
import random
import uuid
from datetime import UTC, datetime
from typing import Any

from agents.interpretation._lens_base import _shown_claims
from schemas.evidence import CoreClaim, DataClass, Direction, Evidence, EvidenceType, Provenance
from services.evidence.literature_selection import (
    build_literature_index,
    evidence_class,
    impact_bucket,
    rank_literature,
)

_RUN = uuid.uuid4()
_PMIDS = itertools.count(10_000_000)
_PROV = Provenance(
    agent_name="literature", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
)


def _paper(
    title: str,
    abstract: str = "",
    *,
    types: list[str] | None = None,
    mesh: list[str] | None = None,
    qualifiers: dict[str, list[str]] | None = None,
    year: int = 2020,
    role: str = "",
    retracted: bool = False,
) -> Evidence:
    headings = [
        {
            "descriptor": d.removesuffix("*"),
            "qualifiers": (qualifiers or {}).get(d, []),
            "major": d.endswith("*"),  # "Disease A*" marks a major topic
        }
        for d in mesh or []
    ]
    extra: dict[str, Any] = {
        "pmid": str(next(_PMIDS)),
        "title": title,
        "abstract": abstract,
        "pub_year": year,
        "publication_types": types or ["Journal Article"],
        "mesh_headings": headings,
        "retracted": retracted,
    }
    if role:
        extra["retrieval_role"] = role
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="GENE-A",
        disease="disease a",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source=f"PMID:{extra['pmid']}",
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=_PROV,
        extra=extra,
    )


def _claim(
    paper: Evidence,
    text: str,
    *,
    topics: list[str],
    confidence: float = 0.8,
    direction: Direction = Direction.UNSPECIFIED,
) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=paper.evidence_id,
        run_id=_RUN,
        gene="GENE-A",
        disease="disease a",
        evidence_type=EvidenceType.ARTICLE,
        claim_text=text,
        confidence=confidence,
        direction=direction,
        topics=topics,
        provenance=_PROV,
        classification=DataClass.NON_SENSITIVE,
    )


def _index(papers: list[Evidence], quality: dict[str, Any] | None = None, **kw: Any):
    kw.setdefault("disease_terms", ["disease a"])
    kw.setdefault("target_terms", ["GENE-A"])
    kw.setdefault("drug_terms", ["drugamab"])
    return build_literature_index(papers, quality or {}, **kw)


def _order(papers: list[Evidence], lens: str, index=None, per_paper: int = 1) -> list[str]:
    claims = [
        _claim(p, f"{p.extra['title']} claim {i}", topics=[lens])
        for p in papers
        for i in range(per_paper)
    ]
    ranked = rank_literature(claims, index or _index(papers), lens)
    seen: list[str] = []
    for c in ranked:
        title = next(p.extra["title"] for p in papers if p.evidence_id == c.source_evidence_id)
        if title not in seen:
            seen.append(title)
    return seen


def test_retractions_errata_editorials_and_predatory_journals_are_never_shown() -> None:
    keep = _paper(
        "A randomized trial of drugamab in disease a", types=["Randomized Controlled Trial"]
    )
    retracted = _paper("Retracted trial in disease a", retracted=True)
    erratum = _paper("Erratum: disease a", types=["Published Erratum"])
    editorial = _paper("Editorial on disease a", types=["Editorial"])
    predatory = _paper("A study of disease a")
    quality = {str(predatory.evidence_id): {"predatory_flag": True}}
    papers = [keep, retracted, erratum, editorial, predatory]

    order = _order(papers, "clinical", _index(papers, quality))

    assert order == [keep.extra["title"]]


def test_an_in_scope_phase_3_trial_outranks_a_review_and_a_docking_study() -> None:
    rct = _paper(
        "Drugamab in disease a: a phase 3 randomized placebo-controlled trial",
        types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
    )
    review = _paper("Treatments for disease a: a review", types=["Review"])
    docking = _paper("Molecular docking of GENE-A inhibitors for disease a")

    assert _order([docking, review, rct], "clinical")[0] == rct.extra["title"]


def test_design_levels_follow_the_evidence_hierarchy_per_lens() -> None:
    mr = _paper("Mendelian randomization of GENE-A and disease a", mesh=["Humans"])
    carriers = _paper("Loss-of-function carriers of GENE-A and risk of disease a", mesh=["Humans"])
    gwas = _paper("A genome-wide association study of disease a", mesh=["Humans"])
    candidate = _paper("A GENE-A polymorphism in disease a", "rs12345 genotype", mesh=["Humans"])
    knockout = _paper(
        "GENE-A knockout mice are protected in a model of disease a",
        mesh=["Disease Models, Animal", "Mice, Knockout"],
    )
    docking = _paper("In silico docking against GENE-A")
    index = _index([mr, carriers, gwas, candidate, knockout, docking])

    def level(p: Evidence, lens: str) -> int:
        return index.papers[str(p.evidence_id)].design[lens].level

    assert level(mr, "genetics") == level(carriers, "genetics") == 3
    assert level(gwas, "genetics") == 2
    assert level(candidate, "genetics") == 1
    assert level(knockout, "biology") == 3
    assert level(docking, "biology") == 0
    assert level(knockout, "genetics") == 0  # an animal study is not human genetics


def test_scope_from_text_mesh_parents_and_target_level_retrieval() -> None:
    named = _paper("GENE-A in disease a")
    own_descriptor = _paper("GENE-A findings", mesh=["Disease A Descriptor*"])
    parent = _paper("GENE-A in broader disorder")
    target_level = _paper("GENE-A safety in other settings", role="target_safety")
    unrelated = _paper("GENE-A in disease z")
    papers = [named, own_descriptor, parent, target_level, unrelated]

    as_disease = _index(
        papers,
        parent_terms=["broader disorder"],
        mesh_descriptor="Disease A Descriptor",
        mesh_is_disease=True,
    )
    broader = _index(papers, mesh_descriptor="Disease A Descriptor", mesh_is_disease=False)

    def scope(index, p: Evidence) -> str:
        return index.papers[str(p.evidence_id)].scope

    assert scope(as_disease, named) == "in_scope"
    assert scope(as_disease, own_descriptor) == "in_scope"
    # The disease maps to a broader descriptor: a MeSH match alone only makes it related.
    assert scope(broader, own_descriptor) == "related"
    assert scope(as_disease, parent) == "related"
    assert scope(as_disease, target_level) == "target_level"
    assert scope(as_disease, unrelated) == "none"


def test_safety_weighs_harm_data_from_any_population_alike() -> None:
    assert evidence_class("none", 3, "safety") == evidence_class("in_scope", 3, "safety") == 0
    assert evidence_class("none", 3, "genetics") == 3
    assert evidence_class("related", 3, "clinical") == 1


def test_a_paper_too_new_for_a_citation_ratio_is_not_ranked_below_a_low_impact_one() -> None:
    assert impact_bucket(None) == impact_bucket(1.0) == 1
    assert impact_bucket(0.2) == 0
    assert impact_bucket(25.0) == 2
    cited = _paper("Cohort study one of disease a", mesh=["Humans", "Cohort Studies"])
    new = _paper("Cohort study two of disease a", mesh=["Humans", "Cohort Studies"])
    uncited = _paper("Cohort study three of disease a", mesh=["Humans", "Cohort Studies"])
    quality = {
        str(cited.evidence_id): {"article_metrics": {"rcr": 12.0}},
        str(uncited.evidence_id): {"article_metrics": {"rcr": 0.1}},
    }
    papers = [uncited, new, cited]

    order = _order(papers, "clinical", _index(papers, quality))

    assert order == [cited.extra["title"], new.extra["title"], uncited.extra["title"]]


def test_one_paper_per_study_comes_before_a_second_paper_of_the_same_study() -> None:
    main = _paper("The ALPHA trial of drugamab in disease a", types=["Randomized Controlled Trial"])
    secondary = _paper(
        "Secondary analysis of the ALPHA trial in disease a", types=["Randomized Controlled Trial"]
    )
    other = _paper("Drugamab versus placebo in disease a", types=["Randomized Controlled Trial"])

    order = _order([secondary, main, other], "clinical")

    # Same evidence class: the other study comes before the ALPHA trial's second paper.
    assert other.extra["title"] in order[:2]
    assert order[2] in {main.extra["title"], secondary.extra["title"]}


_WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
]


def test_reviews_never_take_more_than_a_quarter_of_a_class_while_primary_papers_remain() -> None:
    # Same evidence class for genetics (in scope, level 1): reviews and candidate-gene papers.
    reviews = [
        _paper(f"{w}-{i} survey", "a review of disease a", types=["Review"])
        for i, w in enumerate(_WORDS[:10])
    ]
    primary = [
        _paper(f"{w}-{i} report", "rs1234 genotype of GENE-A in disease a", mesh=["Humans"])
        for i, w in enumerate(_WORDS[10:16])
    ]
    order = _order(reviews + primary, "genetics")

    review_titles = {p.extra["title"] for p in reviews}
    first_eight = order[:8]
    assert sum(t in review_titles for t in first_eight) <= 2  # a quarter of eight


def test_a_strong_paper_of_an_already_placed_study_still_beats_a_background_paper() -> None:
    main = _paper("The ALPHA trial of drugamab in disease a", types=["Randomized Controlled Trial"])
    follow_up = _paper(
        "Follow-up of the ALPHA trial of drugamab in disease a",
        types=["Randomized Controlled Trial"],
    )
    background = _paper("An editorial-style overview of disease a", types=["Review"])

    order = _order([background, follow_up, main], "clinical")

    assert order[-1] == background.extra["title"]


def test_counter_evidence_gets_slots_when_the_papers_lean_one_way() -> None:
    lean = [_paper(f"GENE-A trial {i} in disease a", types=["Clinical Trial"]) for i in range(8)]
    other = _paper("A weaker report on GENE-A in disease a", mesh=["Cohort Studies"])
    claims = [
        _claim(p, f"lowering GENE-A helps {i}", topics=["clinical"], direction=Direction.INHIBIT)
        for i, p in enumerate(lean)
    ] + [_claim(other, "raising GENE-A helps", topics=["clinical"], direction=Direction.ACTIVATE)]
    index = _index([*lean, other])

    ranked = rank_literature(claims, index, "clinical")
    papers_in_order = list(dict.fromkeys(str(c.source_evidence_id) for c in ranked))

    # Without the floor the weaker opposing paper would come last (ninth).
    assert papers_in_order.index(str(other.evidence_id)) < 8


def test_within_a_paper_claims_naming_the_target_in_its_own_case_come_first() -> None:
    paper = _paper("GENE-A in disease a")
    vague = _claim(paper, "the pathway matters in disease a", topics=["biology"], confidence=0.99)
    lowercase = _claim(paper, "gene-a like terms in prose", topics=["biology"], confidence=0.99)
    named = _claim(paper, "GENE-A loss lowered risk (OR 0.6)", topics=["biology"], confidence=0.5)

    ranked = rank_literature([vague, lowercase, named], _index([paper]), "biology")

    assert ranked[0] is named
    # A symbol written in lower case (a common word for some symbols) does not count.
    assert ranked[1] is vague


def test_the_order_does_not_depend_on_input_order() -> None:
    papers = [
        _paper(f"Study {i} of GENE-A in disease a", types=["Clinical Trial"], year=2000 + i)
        for i in range(30)
    ]
    claims = [
        _claim(p, f"claim {j} of {p.extra['title']}", topics=["clinical"])
        for p in papers
        for j in range(2)
    ]
    shuffled = claims[:]
    random.Random(3).shuffle(shuffled)
    index = _index(papers)

    first = rank_literature(claims, index, "clinical")
    second = rank_literature(shuffled, index, "clinical")

    assert [c.evidence_id for c in first] == [c.evidence_id for c in second]


def test_a_disease_without_names_or_mesh_still_ranks_by_design() -> None:
    rct = _paper("A randomized trial of drugamab", types=["Randomized Controlled Trial"])
    review = _paper("An overview", types=["Review"])
    index = build_literature_index([review, rct], {}, disease_terms=[])

    assert {r.scope for r in index.papers.values()} == {"none"}
    assert _order([review, rct], "clinical", index)[0] == rct.extra["title"]


def test_without_an_index_literature_keeps_a_deterministic_confidence_order() -> None:
    paper = _paper("x")
    a = _claim(paper, "b claim", topics=["biology"], confidence=0.5)
    b = _claim(paper, "a claim", topics=["biology"], confidence=0.9)

    assert rank_literature([a, b], None, "biology") == [b, a]


def test_the_lens_view_shows_the_best_papers_first_and_one_claim_each_before_seconds() -> None:
    strong = [
        _paper(
            f"Phase 3 randomized trial {i} of drugamab in disease a",
            types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
        )
        for i in range(30)
    ]
    weak = [_paper(f"Narrative review {i} of disease a", types=["Review"]) for i in range(60)]
    papers = strong + weak
    claims = [
        _claim(p, f"{p.extra['title']} finding {j}", topics=["clinical"])
        for p in papers
        for j in range(3)
    ]
    index = _index(papers)

    shown = _shown_claims(claims, {}, frozenset(), "clinical", 40, index.model_dump(mode="json"))

    strong_ids = {p.evidence_id for p in strong}
    shown_papers = [c.source_evidence_id for c in shown]
    assert len(set(shown_papers)) == len(shown_papers)  # one claim per paper first
    assert sum(pid in strong_ids for pid in shown_papers) >= 28


def test_a_passing_mention_of_the_disease_makes_a_paper_related_not_in_scope() -> None:
    passing = _paper(
        "Drugamab in disease z",
        "Drugamab improved disease z. Two patients developed disease a during follow-up.",
    )
    topical = _paper(
        "Outcomes of drugamab",
        "We treated disease a with drugamab. Disease a activity fell by half.",
    )
    titled = _paper("Drugamab in disease a", "Results are reported.")
    index = _index([passing, topical, titled])

    assert index.papers[str(passing.evidence_id)].scope == "related"
    assert index.papers[str(topical.evidence_id)].scope == "in_scope"
    assert index.papers[str(titled.evidence_id)].scope == "in_scope"


def test_a_trial_that_only_measured_the_target_as_a_biomarker_is_background_for_clinical() -> None:
    biomarker = _paper(
        "Exercise training in disease a: a phase 3 randomized trial",
        "Serum GENE-A levels fell after exercise.",
        types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
    )
    drug_trial = _paper(
        "Drugamab in disease a",
        "A cohort of patients treated with drugamab.",
        mesh=["Cohort Studies", "Humans"],
    )
    directed = _paper(
        "Anti-GENE-A antibody therapy in disease a: an open-label study",
        types=["Clinical Trial"],
    )
    index = _index([biomarker, drug_trial, directed])

    assert index.papers[str(biomarker.evidence_id)].target_intervention is False
    assert index.papers[str(directed.evidence_id)].target_intervention is True
    order = _order([biomarker, drug_trial, directed], "clinical", index)
    assert order[-1] == biomarker.extra["title"]


def test_carriers_of_a_common_polymorphism_are_not_a_loss_of_function_carrier_study() -> None:
    common = _paper(
        "GENE-A rs1234 and disease a",
        "Carriers of the T allele of GENE-A rs1234 had higher risk of disease a.",
        mesh=["Humans"],
    )
    lof = _paper(
        "Loss-of-function carriers of GENE-A and disease a",
        "Carriers of protein-truncating GENE-A variants had lower risk of disease a.",
        mesh=["Humans"],
    )
    index = _index([common, lof])

    assert index.papers[str(common.evidence_id)].design["genetics"].level == 1
    assert index.papers[str(lof.evidence_id)].design["genetics"].level == 3


def test_a_trial_protocol_ranks_below_the_trials_results() -> None:
    protocol = _paper(
        "Rationale and design of a phase 3 randomized trial of drugamab in disease a",
        types=["Randomized Controlled Trial"],
    )
    results = _paper(
        "Drugamab in disease a: a phase 3 randomized trial",
        types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
    )

    assert _order([protocol, results], "clinical")[0] == results.extra["title"]


def test_within_a_class_an_in_scope_trial_comes_before_a_related_one() -> None:
    related_phase3 = _paper(
        "Drugamab in disease z: a phase 3 randomized trial",
        "One case of disease a occurred.",
        types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
    )
    in_scope_phase2 = _paper(
        "Drugamab in disease a: a randomized phase 2 trial",
        types=["Randomized Controlled Trial", "Clinical Trial, Phase II"],
    )

    order = _order([related_phase3, in_scope_phase2], "clinical")

    assert order[0] == in_scope_phase2.extra["title"]


def test_symbols_match_as_papers_write_them_and_common_words_do_not() -> None:
    from services.evidence.literature_selection import symbol_pattern

    il = symbol_pattern(["IL17A"])
    assert il is not None
    assert il.search("serum IL-17A rose") and il.search("Il17a-deficient mice")
    assert not il.search("IL17AB") and not il.search("il17a in lower case prose")
    word = symbol_pattern(["SET"])
    assert word is not None
    assert word.search("the SET complex")
    assert not word.search("Set up the assay") and not word.search("a set of cells")


def test_mesh_decides_when_an_indexed_paper_only_mentions_the_disease_in_its_abstract() -> None:
    other_disease_trial = _paper(
        "Drugamab in disease z",
        "Drugamab helped disease z. Disease a occurred in two patients. Disease a resolved.",
        mesh=["Disease Z", "Humans"],
    )
    indexed_for_it = _paper(
        "Drugamab outcomes", "Disease a improved. Disease a relapsed less.", mesh=["Disease A*"]
    )
    as_adverse_event = _paper("Drugamab in disease z", "Results.", mesh=["Disease Z*", "Disease A"])
    not_indexed = _paper("Drugamab outcomes", "Disease a improved. Disease a relapsed less.")
    papers = [other_disease_trial, indexed_for_it, not_indexed, as_adverse_event]
    index = _index(papers, mesh_descriptor="Disease A", mesh_is_disease=True)

    # A minor heading (e.g. the disease indexed as an adverse event) is not the topic.
    assert index.papers[str(as_adverse_event.evidence_id)].scope == "related"

    assert index.papers[str(other_disease_trial.evidence_id)].scope == "related"
    assert index.papers[str(indexed_for_it.evidence_id)].scope == "in_scope"
    assert index.papers[str(not_indexed.evidence_id)].scope == "in_scope"
