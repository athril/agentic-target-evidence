# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The gene-profile skill's grounded evidence: the literature map (PubTator3), the allelic
series and variant spectrum, the programmatic blocks and figures, and the checks on them.

Offline: PubTator documents, UniProt features and ClinVar rows are built in the tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import check_profile  # noqa: E402
import gather  # noqa: E402
import profile_blocks  # noqa: E402
import profile_literature as lit  # noqa: E402
import profile_schema as ds  # noqa: E402
import profile_tables as tables  # noqa: E402

from mcp_servers.pubtator.tools import (  # noqa: E402
    PubTatorAnnotation,
    PubTatorDocument,
    PubTatorPassage,
    PubTatorRelation,
)
from services.evidence.grounding import TargetIdentity  # noqa: E402

# ----------------------------------------------------------------------------- helpers

_TRPC6 = TargetIdentity(symbol="TRPC6", ncbi_id="7225", ortholog_ids=["22068"])


def _ann(kind: str, text: str, identifier: str = "", **kw: Any) -> PubTatorAnnotation:
    return PubTatorAnnotation(
        type=kind, text=text, offset=0, length=len(text), identifier=identifier, **kw
    )


def _doc(
    pmid: str, *annotations: PubTatorAnnotation, relations: list[PubTatorRelation] | None = None
) -> PubTatorDocument:
    return PubTatorDocument(
        pmid=pmid,
        passages=[
            PubTatorPassage(kind="abstract", offset=0, text="x", annotations=list(annotations))
        ],
        relations=relations or [],
    )


def _rel(
    kind: str, one: str, one_type: str, two: str, two_type: str, two_name: str = ""
) -> PubTatorRelation:
    return PubTatorRelation(
        type=kind,
        role1=one,
        role1_type=one_type,
        role2=two,
        role2_type=two_type,
        role2_name=two_name,
    )


_FSGS = "MESH:D005923"


# --------------------------------------------------------------------------- literature map


def test_a_paper_names_the_target_by_its_gene_id_an_ortholog_a_variant_or_a_drug_class():
    docs = [
        _doc("1", _ann("Gene", "TRPC6", "7225")),
        _doc("2", _ann("Gene", "Trpc6", "22068"), _ann("Species", "mice", "10090")),
        _doc("3", _ann("Variant", "P112Q", "", gene_id="7225", rsid="rs121434390", hgvs="p.P112Q")),
        _doc("4", _ann("Chemical", "TRPC6 inhibitors", "")),
        _doc("5", _ann("Gene", "TRPC3", "7222")),
    ]
    m = lit.build_map(docs, _TRPC6, listed=["1", "2", "3", "4", "5", "99"])

    assert m["papers_naming_target"] == 4
    assert m["gene_studied"] == {"human_gene": 1, "ortholog": 1, "variant": 1, "drug": 1}
    assert {p: v["names_target"] for p, v in m["papers"].items()} == {
        "1": True, "2": True, "3": True, "4": True, "5": False,
    }  # fmt: skip
    assert m["organisms"] == [{"taxon": "10090", "name": "mouse", "papers": 1}]


def test_relations_count_only_inside_papers_about_the_target_and_with_the_target_at_one_end():
    fsgs = _rel(
        "Association", "7225", "Gene", _FSGS, "Disease", "Glomerulosclerosis Focal Segmental"
    )
    docs = [
        _doc("1", _ann("Gene", "TRPC6", "7225"), relations=[fsgs]),
        _doc(
            "2",
            _ann("Gene", "Trpc6", "22068"),
            relations=[_rel("Positive_Correlation", "22068", "Gene", _FSGS, "Disease")],
        ),
        # a paper about another gene: its relations never reach the map
        _doc(
            "3",
            _ann("Gene", "TRPC3", "7222"),
            relations=[_rel("Association", "7222", "Gene", _FSGS, "Disease")],
        ),
        # a relation between two other entities of a target paper
        _doc(
            "4",
            _ann("Gene", "TRPC6", "7225"),
            relations=[_rel("Association", "MESH:D002118", "Chemical", _FSGS, "Disease")],
        ),
    ]
    m = lit.build_map(docs, _TRPC6, citations={"1": 50, "2": 5})

    [fsgs_row] = m["diseases"]
    assert (fsgs_row["papers"], fsgs_row["pmids"]) == (2, ["1", "2"])
    assert fsgs_row["relations"] == {"Association": 1, "Positive_Correlation": 1}
    assert m["chemicals"] == []


def test_a_variant_of_the_target_keeps_what_the_papers_relate_it_to():
    variant = "RS#:72613567;CorrespondingGene:345275"
    target = TargetIdentity(symbol="HSD17B13", ncbi_id="345275")
    docs = [
        _doc(
            str(n),
            _ann("Gene", "HSD17B13", "345275"),
            _ann("Variant", "rs72613567", "", gene_id="345275", rsid="rs72613567"),
            relations=[
                PubTatorRelation(
                    type="Negative_Correlation",
                    role1="MESH:D008107",
                    role1_type="Disease",
                    role1_name="Liver Diseases",
                    role2=variant,
                    role2_type="Variant",
                )
            ],
        )
        for n in (1, 2)
    ]
    m = lit.build_map(docs, target)

    [v] = m["variants"]
    assert (v["rsid"], v["papers"]) == ("rs72613567", 2)
    assert v["related"][0]["name"] == "Liver Diseases"
    assert v["related"][0]["kind"] == "disease"
    assert m["diseases"][0]["name"] == "Liver Diseases"  # the gene's literature, via its variant


def test_the_approved_symbol_rule_rehomes_a_mention_written_as_the_symbol():
    target = TargetIdentity(symbol="MET", ncbi_id="4233")
    other = SimpleNamespace(symbol="SLTM", aliases=["MET"], previous_symbols=[])
    docs = [
        _doc(
            "1",
            _ann("Gene", "MET", "79811"),
            relations=[
                _rel("Association", "79811", "Gene", "MESH:D009369", "Disease", "Neoplasms")
            ],
        )
    ]

    m = lit.build_map(docs, target, hgnc={"79811": other})

    assert m["papers_naming_target"] == 1
    assert m["diseases"][0]["name"] == "Neoplasms"


def test_keyword_precision_decides_which_literature_volume_is_reported():
    word = lit.volume(keyword_total=345_759, curated_total=2_891, precision=0.08)
    gene = lit.volume(keyword_total=1_558, curated_total=451, precision=0.99)
    unknown = lit.volume(keyword_total=10, curated_total=3, precision=None)

    assert (word["basis"], word["total"]) == ("curated", 2_891)
    assert (gene["basis"], gene["total"]) == ("keyword", 1_558)
    assert unknown["basis"] == "keyword"


def test_precision_needs_enough_annotated_keyword_hits():
    docs = [_doc(str(n), _ann("Gene", "TRPC6", "7225")) for n in range(12)]
    few = lit.build_map(docs[:5], _TRPC6, keyword_sample=[str(n) for n in range(5)])
    many = lit.build_map(docs, _TRPC6, keyword_sample=[str(n) for n in range(12)] + ["100"])

    assert few["keyword"]["precision"] is None
    assert many["keyword"] == {"sample": 13, "annotated": 12, "naming_target": 12, "precision": 1.0}


# --------------------------------------------------------------------------- variant tables


def test_uniprot_variant_notes_split_into_diseases_dbsnp_and_a_verbatim_note():
    assert tables.parse_uniprot_variant("in FSGS2; increases calcium ion transport; dbSNP:rs121434390") == {
        "diseases": ["FSGS2"], "rsid": "rs121434390", "note": "increases calcium ion transport",
    }  # fmt: skip
    assert tables.parse_uniprot_variant("in dbSNP:rs145886902") == {
        "diseases": [],
        "rsid": "rs145886902",
        "note": "",
    }
    assert (
        tables.parse_uniprot_variant("found in patients with FH; dbSNP:rs1")["note"]
        == "found in patients with FH"
    )


@pytest.mark.parametrize(
    ("hgvsp", "change"),
    [
        ("p.Pro112Gln", ("P", 112, "Q")),
        ("p.Ser364LysfsTer12", ("S", 364, "fs")),
        ("p.Arg399Ter", ("R", 399, "*")),
        ("c.1A>G", None),
    ],
)
def test_protein_changes_are_read_one_letter_with_frameshifts_named(hgvsp, change):
    assert tables.protein_change(hgvsp) == change


_FEATURES = [
    {"type": "Topological domain", "description": "Cytoplasmic", "start": 1, "end": 438},
    {"type": "Repeat", "description": "ANK 1", "start": 96, "end": 125},
    {"type": "Transmembrane", "description": "Helical", "start": 439, "end": 459},
]


def test_a_position_is_placed_in_its_most_specific_feature_and_topology():
    assert tables.region_of(112, _FEATURES) == "ANK 1 · Cytoplasmic"
    assert tables.region_of(445, _FEATURES) == "Helical"
    assert tables.region_of(900, _FEATURES) == ""


def _clinvar(sig: str, stars: int, hgvsp: str, consequence: str) -> dict[str, Any]:
    return {
        "clinical_significance": sig,
        "gold_stars": stars,
        "hgvsp": hgvsp,
        "major_consequence": consequence,
    }


_CLINVAR = {
    "pathogenic": [
        _clinvar("Pathogenic", 2, "p.Pro112Gln", "missense_variant"),
        _clinvar(
            "Conflicting classifications of pathogenicity", 1, "p.Pro15Ser", "missense_variant"
        ),
        _clinvar("Pathogenic", 0, "p.Gly20Asp", "missense_variant"),
    ],
    "likely_pathogenic": [_clinvar("Likely pathogenic", 1, "p.Arg399Ter", "stop_gained")],
}


def test_the_spectrum_leaves_out_conflicting_and_unreviewed_calls_and_splits_review_stars():
    sp = tables.variant_spectrum(
        _CLINVAR, {"hc_lof_count": 194, "reported_variants": []}, _FEATURES
    )

    assert sp["plp_total"] == 2
    assert (sp["plp_by_class"]["missense"], sp["plp_by_class"]["truncating"]) == (1, 1)
    assert (sp["plp_2star_by_class"]["missense"], sp["plp_2star_by_class"]["truncating"]) == (1, 0)
    assert sp["missense_by_region"] == {"ANK 1 · Cytoplasmic": 1}


def test_curated_disease_variants_lead_the_series_and_join_their_clinvar_rows():
    uniprot = {
        "features": _FEATURES,
        "diseases": [{"acronym": "FSGS2", "name": "Focal segmental glomerulosclerosis 2"}],
        "variants": [
            {"position": 112, "ref": "P", "alt": "Q", "diseases": ["FSGS2"], "rsid": "rs121434390",
             "note": "increases calcium ion transport", "pmids": ["15879175"]},
        ],
    }  # fmt: skip
    series = tables.allelic_series(
        uniprot=uniprot, clinvar=_CLINVAR, lof={}, gwas={}, literature={}, effects={}
    )

    first, second = series["rows"][:2]
    assert (first["change"], first["rsid"], first["region"]) == (
        "P112Q",
        "rs121434390",
        "ANK 1 · Cytoplasmic",
    )
    assert first["phenotypes"] == ["Focal segmental glomerulosclerosis 2 (FSGS2)"]
    assert first["origins"] == ["UniProt", "ClinVar Pathogenic (2★)"]
    assert (second["change"], second["effect"]) == ("R399*", "loss (truncating)")


def test_two_alleles_of_one_rs_id_stay_separate_rows():
    uniprot = {"diseases": [], "variants": [
        {"position": 374, "ref": "D", "alt": "H", "diseases": [], "rsid": "rs1", "note": ""},
        {"position": 374, "ref": "D", "alt": "Y", "diseases": [], "rsid": "rs1", "note": ""},
    ]}  # fmt: skip
    gwas = {"hits": [{"rs_id": "rs1", "trait": "LDL"}]}
    series = tables.allelic_series(
        uniprot=uniprot, clinvar={}, lof={}, gwas=gwas, literature={}, effects={}
    )
    by_change = {r["change"]: r["origins"] for r in series["rows"]}

    assert set(by_change) == {"D374H", "D374Y"}
    assert by_change["D374Y"] == ["UniProt"]
    assert tables.same_change("G670E", "E670G") and not tables.same_change("D374Y", "D374H")


def test_a_qtl_never_types_a_protein_altering_variant_but_types_a_regulatory_one():
    qtl = {"study_type": "eqtl", "beta": -0.3, "lead_missense_in_target": False}
    missense = {"alt": "T", "curated": [], "consequences": ["missense_variant"], "qtls": [qtl]}
    intronic = {"alt": "T", "curated": [], "consequences": ["intron_variant"], "qtls": [qtl]}
    gwas = {"hits": [{"rs_id": "rs1", "trait": "LDL"}, {"rs_id": "rs2", "trait": "LDL"}]}

    series = tables.allelic_series(
        uniprot={},
        clinvar={},
        lof={},
        gwas=gwas,
        literature={},
        effects={"rs1": [missense], "rs2": [intronic]},
    )
    effect = {r["rsid"]: r["effect"] for r in series["rows"]}

    assert effect == {"rs1": "", "rs2": "lower expression (alt T, own eqtl)"}


def test_table_rows_cite_only_sources_that_exist():
    rows = {"total": 1, "rows": [{"change": "P112Q", "rsid": "", "region": "", "consequence": "missense_variant",
                                   "effect": "", "uniprot_note": "", "phenotypes": [], "origins": ["UniProt"],
                                   "refs": ["uniprot_extra", "pmid:404"]}]}  # fmt: skip
    md = tables.allelic_series_md(rows, {"uniprot_extra"})
    assert md.splitlines()[-1].endswith("| [@uniprot_extra] |")


# --------------------------------------------------------------------------- blocks and checks


def _raw(run: Path, key: str, data: Any, section: str = "genetics") -> None:
    rec = ds.RawRecord(
        key=key, section=section, db_name=key, tool="t", status="ok", retrieved_at="x", data=data
    )
    (run / "raw" / f"{key}.json").write_text(rec.model_dump_json())


@pytest.fixture
def run(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir()
    sources = {
        "gnomad_constraint": ds.Source(key="gnomad_constraint", kind="database", title="g", url="u", accessed="d", verified=True),
        "uniprot_extra": ds.Source(key="uniprot_extra", kind="database", title="u", url="u", accessed="d", verified=True),
        "pmid:1": ds.Source(key="pmid:1", kind="article", title="off", url="u", accessed="d", verified=True, pmid="1", grounded=False),
    }  # fmt: skip
    ds.save_sources(tmp_path, sources)
    (tmp_path / "manifest.json").write_text(
        ds.Manifest(query="TST1", gene="TST1", generated_at="x").model_dump_json()
    )
    return tmp_path


def _draft(**sections: str) -> str:
    parts = []
    for key, title in ds.SECTIONS:
        if key == "indication":
            continue
        parts += [
            f"## {title}",
            "",
            sections.get(key, "Placeholder fact [@gnomad_constraint]."),
            "",
        ]
    return "\n".join(parts)


def _series_row() -> dict[str, Any]:
    return {"change": "P112Q", "rsid": "", "region": "", "consequence": "missense_variant", "effect": "",
            "uniprot_note": "increases calcium ion transport", "phenotypes": ["FSGS2"], "origins": ["UniProt"],
            "refs": ["uniprot_extra"]}  # fmt: skip


def test_the_allelic_series_is_required_in_human_genetics_when_the_run_has_one(run: Path):
    _raw(run, "allelic_series", {"series": {"total": 1, "rows": [_series_row()]}, "spectrum": {}})
    missing = check_profile.check(run, _draft())
    misplaced = check_profile.check(run, _draft(biology="{{table:allelic_series}}"))
    placed = check_profile.check(
        run, _draft(genetics="Rows below [@gnomad_constraint].\n\n{{table:allelic_series}}")
    )

    assert [i.code for i in missing.errors] == ["E_MISSING_TABLE"]
    assert "E_MISSING_TABLE" in [i.code for i in misplaced.errors]
    assert placed.ok, placed.errors


def test_an_unknown_block_is_an_error_and_a_block_line_needs_no_citation(run: Path):
    rep = check_profile.check(run, _draft(biology="{{figure:protein_map}}\n\n{{table:nothing}}"))
    assert [i.code for i in rep.errors] == ["E_UNKNOWN_BLOCK"]


def test_citing_a_paper_pubtator_finds_off_target_is_a_warning(run: Path):
    rep = check_profile.check(run, _draft(biology="A claim [@pmid:1]."))
    assert rep.ok
    assert [w.code for w in rep.warnings] == ["W_OFF_TARGET_CITATION"]


def test_blocks_expand_into_cited_tables_and_figures_can_be_left_out(run: Path):
    _raw(run, "allelic_series", {"series": {"total": 1, "rows": [_series_row()]}, "spectrum": {}})
    text, notes = profile_blocks.expand(
        "{{table:allelic_series}}\n{{figure:protein_map}}",
        run,
        "TST1",
        {"uniprot_extra"},
        figures=False,
    )
    assert "| P112Q |" in text and "[@uniprot_extra]" in text
    assert "{{" not in text
    assert notes == ["figure:protein_map: figures off"]


def test_the_protein_map_is_drawn_as_svg(tmp_path: Path):
    from profile_figures import protein_map

    path = protein_map(
        tmp_path / "figures" / "protein_map.svg",
        length=931, features=_FEATURES, missense=[112], truncating=[399], uniprot_disease=[112],
        lof=[399], plddt=[50.0] * 931, pdb=[(1, 931)], labels={112: "P112Q"},
    )  # fmt: skip
    assert path is not None and path.read_text().lstrip().startswith(("<?xml", "<svg"))
    assert "P112Q" in path.read_text()


def test_grounding_is_recorded_on_articles_and_hides_off_target_papers_in_the_digest():
    sources = {"pmid:1": ds.Source(key="pmid:1", kind="article", title="t", url="u", accessed="d")}
    papers = {
        "1": {"names_target": False, "organisms": []},
        "2": {"names_target": True, "organisms": ["mouse"]},
    }
    gather.mark_grounding(sources, {"papers": papers})
    out = gather._literature_digest(
        {
            "query": '"MET"[tiab]',
            "records": [{"pmid": "1", "title": "metformin"}, {"pmid": "2", "title": "Met kinase"}],
        },
        {},
        papers,
    )

    assert sources["pmid:1"].grounded is False
    assert "metformin" not in out and "[mouse] · Met kinase" in out
    assert "1 hit(s) PubTator3 shows are not about" in out


# ------------------------------------------------------------ checks that generalise


def test_rules_abbreviations_and_draft_written_references_carry_no_claim(run: Path):
    draft = _draft(
        clinical="A fact [@gnomad_constraint].\n\n---\n\n**Abbreviation:** pFSGS, primary FSGS."
    )
    draft += "\n---\n\n## References\n\n(generated by render.py)\n"
    rep = check_profile.check(run, draft)

    assert rep.ok, rep.errors
    assert [w.code for w in rep.warnings] == ["W_GENERATED_SECTION"]


def test_render_drops_the_sections_it_writes_itself():
    import render

    body = "## Snapshot\n\nFact.\n\n---\n\n## References\n\nmine\n\n## Appendix: x\n\nmine\n"
    assert render.drop_generated_sections(body) == "## Snapshot\n\nFact.\n"


@pytest.mark.parametrize(
    ("value", "fmt", "ok"),
    [
        ([1, 2, 3], "length", "3"),
        ([1, 2, 3], "count", "3"),
        (0.342, "pct", "34%"),
        (list(range(30)), None, None),  # a dump, not a value
        ({"a": 1}, None, None),
        (0.5, "percent", None),  # unknown format is not ignored
    ],
)
def test_an_anchor_resolves_only_to_a_value_its_format_applies_to(value, fmt, ok):
    if ok is None:
        with pytest.raises(ValueError):
            ds.format_value(value, fmt)
    else:
        assert ds.format_value(value, fmt) == ok


def test_a_fraction_written_as_a_percentage_or_a_count_as_a_fold_change_is_an_error(run: Path):
    _raw(run, "gnomad_constraint", {"frac": 0.34, "n": 384})
    rep = check_profile.check(
        run,
        _draft(
            biology="{{gnomad_constraint:frac}}% confident [@gnomad_constraint].",
            genetics="Grew {{gnomad_constraint:n}}-fold [@gnomad_constraint].",
            toolkit="{{gnomad_constraint:frac|pct}} confident [@gnomad_constraint].",
        ),
    )
    assert [i.code for i in rep.errors] == ["E_ANCHOR_UNIT", "E_ANCHOR_UNIT"]


def test_a_sentence_citing_only_records_must_anchor_its_numbers_even_beside_a_paper(run: Path):
    rep = check_profile.check(
        run,
        _draft(biology="A paper reports 3.8 Å [@pmid:1]. The model scores 76.69 [@uniprot_extra]."),
    )
    bare = [w.message for w in rep.warnings if w.code == "W_BARE_NUMBER"]
    assert bare == ["hand-typed number '76.69'"]


def test_a_name_the_cited_record_never_mentions_is_a_warning(run: Path):
    _raw(run, "uniprot_extra", {"text": "Boehringer Ingelheim inhibitor"})
    rep = check_profile.check(
        run,
        _draft(
            commercial="Assignees include Boehringer Ingelheim and Vertex Pharmaceuticals "
            "[@uniprot_extra]."
        ),
    )
    names = [w.message for w in rep.warnings if w.code == "W_UNGROUNDED_NAME"]
    assert len(names) == 1 and "Vertex Pharmaceuticals" in names[0]
    assert "Boehringer" not in names[0]


def _scoped(run: Path) -> None:
    (run / "manifest.json").write_text(
        ds.Manifest(
            query="TST1", gene="TST1", disease="nephrosis", generated_at="x"
        ).model_dump_json()
    )
    sources = ds.load_sources(run)
    sources["ot_association_disease"] = ds.Source(
        key="ot_association_disease",
        kind="database",
        title="o",
        url="u",
        accessed="d",
        verified=True,
    )
    ds.save_sources(run, sources)
    _raw(
        run, "ot_association_disease", {"overall_score": 0.78, "genetic_score": 0.89}, "indication"
    )


def _with_indication(text: str) -> str:
    return _draft().replace(
        "## Research toolkit", f"## Indication fit\n\n{text}\n\n## Research toolkit"
    )


def test_a_disease_scoped_run_writes_its_indication_from_the_records(run: Path):
    _scoped(run)
    unscoped = check_profile.check(
        run, _with_indication("No specific disease was given [@ot_association_disease].")
    )
    scoped = check_profile.check(
        run,
        _with_indication("Genetic support [@ot_association_disease].\n\n{{table:indication}}"),
    )
    text, _ = profile_blocks.expand(
        "{{table:indication}}", run, "TST1", {"ot_association_disease"}, disease="nephrosis"
    )

    assert {i.code for i in unscoped.errors} == {"E_MISSING_TABLE", "E_SCOPE_CONTRADICTION"}
    assert scoped.ok, scoped.errors
    assert "overall 0.78 (genetic 0.89)" in text and "[@ot_association_disease]" in text


def test_a_gene_only_run_needs_no_indication_section_or_table(run: Path):
    assert profile_blocks.required(run, "") == {}
    assert check_profile.check(run, _draft()).ok


@pytest.mark.parametrize(
    ("note", "effect"),
    [
        ("increases calcium ion transport", "gain"),
        ("increases cation channel activity; reduces the ratio of cell-surface to total "
         "expression; increases calcium ion transport", "gain"),
        ("uncertain significance; decreases calcium ion transport", "loss"),
        ("abolishes kinase activity", "loss"),
        ("increases protein degradation", "loss"),
        ("reduced inactivation", "gain"),
        ("dominant negative effect on the wild type", "dominant negative"),
        ("increases kinase activity; decreases kinase activity", "mixed"),
        ("no effect on RNF24-binding", ""),
        # a modification another enzyme makes, a double mutant, a phenotype: not typed
        ("partial loss of cleavage by furin and PCSK5", ""),
        ("reduced phosphorylation at Ser-47", ""),
        ("correlated with lower plasma levels of low-density lipoprotein cholesterol", ""),
        ("highly increases LDL uptake when associated with S-218", ""),
        ("loss of catalytic activity", "loss"),
        ("not secreted", "loss"),
        ("uncertain significance", ""),
    ],
)  # fmt: skip
def test_uniprot_notes_type_the_effect_on_the_target(note, effect):
    assert tables.note_effect(note) == effect


def test_the_series_tallies_what_every_disease_variant_does_to_the_target():
    rows = [
        {**_series_row(), "effect": "gain of function (UniProt note)", "disease_variant": True},
        {**_series_row(), "effect": "loss (truncating)", "disease_variant": True},
        {**_series_row(), "effect": "", "disease_variant": True},
        {**_series_row(), "effect": "lower expression (alt T, own eqtl)", "disease_variant": False},
    ]
    md = tables.allelic_series_md({"total": 4, "rows": rows}, {"uniprot_extra", "allelic_series"})

    assert tables.effect_tally(rows) == {"gain": 1, "truncating": 1, "not typed": 1}
    assert "across all 3 disease variants" in md
    assert "gain of function 1 · truncating (loss) 1 · not typed 1" in md


def test_structures_are_listed_best_resolution_first_with_the_alphafold_model():
    uniprot = {"accession": "Q9Y210", "length": 931, "pdb_count": 2, "pdb": [
        {"id": "5YX9", "method": "EM", "resolution": "3.80 A", "chains": "A/B=1-931"},
        {"id": "6UZ8", "method": "EM", "resolution": "2.84 A", "chains": "A/B=85-931"},
    ]}  # fmt: skip
    md = tables.structures_md(uniprot, {"plddt": [90.0]}, {"uniprot_extra", "alphafold"})
    lines = md.splitlines()

    assert lines[2].startswith(
        "| [6UZ8](https://www.rcsb.org/structure/6UZ8) | EM | 2.84 Å | 85–931"
    )
    assert "5YX9" in lines[3] and "AlphaFold Q9Y210" in lines[4]
