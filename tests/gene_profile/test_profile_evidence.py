# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The gene-profile skill's grounded evidence: the literature map (PubTator3), the allelic
series and variant spectrum, the programmatic blocks and figures, and the checks on them.

Offline: PubTator documents, UniProt features and ClinVar rows are built in the tests."""

from __future__ import annotations

import json
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
    assert md.splitlines()[2].endswith("| [@uniprot_extra] |")


def _summary(
    title: str,
    spdi: str,
    sig: str,
    review: str,
    traits: list[str],
    scvs: int,
    consequences: list[str],
    genes: tuple[str, ...] = ("TST1",),
) -> dict[str, Any]:
    return {
        "accession": f"VCV{abs(hash(title)) % 10**9:09d}", "title": title,
        "genes": [{"symbol": g} for g in genes],
        "variation_set": [{"canonical_spdi": spdi}],
        "molecular_consequence_list": consequences,
        "germline_classification": {"description": sig, "review_status": review,
                                    "trait_set": [{"trait_name": t} for t in traits]},
        "supporting_submissions": {"scv": [f"SCV{i}" for i in range(scvs)]},
    }  # fmt: skip


_SUMMARIES = [
    _summary("NM_1.4(TST1):c.1120G>T (p.Asp374Tyr)", "NC_000001.11:55057453:G:T",
             "Pathogenic/Likely pathogenic", "criteria provided, multiple submitters, no conflicts",
             ["Familial hypercholesterolemia", "not provided"], 4, ["missense variant"]),
    _summary("NM_1.4(TST1):c.1120G>C (p.Asp374His)", "NC_000001.11:55057453:G:C",
             "Pathogenic", "criteria provided, single submitter", ["Hypercholesterolemia"], 1,
             ["missense variant"]),
    _summary("NM_1.4(TST1):c.42_43insTG (p.Leu15fs)", "NC_000001.11:55039879::TG",
             "Pathogenic", "criteria provided, single submitter", ["Hypercholesterolemia, familial, 1"],
             1, ["frameshift variant"]),
    _summary("NM_1.4(TST1):c.399+1G>A", "NC_000001.11:55044034:G:A", "Likely pathogenic",
             "criteria provided, single submitter", ["Hypercholesterolemia, autosomal dominant, 3"], 1,
             ["splice donor variant", "intron variant"]),
    _summary("NM_1.4(TST1):c.10C>T (p.Gln4Ter)", "NC_000001.11:55039800:C:T",
             "Pathogenic; risk factor", "no assertion criteria provided", ["Hypocholesterolemia"], 1,
             ["nonsense"]),
    _summary("GRCh38/hg38 1p32.3-p31.1(chr1:50000000-60000000)x1", "", "Pathogenic",
             "criteria provided, single submitter", ["See cases"], 1, [], ("TST1", "OTHER")),
    _summary("NM_1.4(TST1):c.20A>G (p.Lys7Arg)", "NC_000001.11:55039810:A:G",
             "Conflicting classifications of pathogenicity", "criteria provided, conflicting classifications",
             ["Hypercholesterolemia"], 3, ["missense variant"]),
    _summary("NM_9.1(OTHER):c.5A>G (p.Lys2Arg)", "NC_000001.11:55039900:A:G", "Pathogenic",
             "criteria provided, single submitter", ["X"], 1, ["missense variant"], ("OTHER",)),
]  # fmt: skip


def _clinvar_records() -> dict[str, Any]:
    from mcp_servers.clinvar.tools import parse_summaries

    p, lp = parse_summaries(_SUMMARIES, "TST1")
    return {
        "pathogenic": [c.model_dump() for c in p],
        "likely_pathogenic": [c.model_dump() for c in lp],
    }


def test_the_series_names_each_clinvar_call_by_condition_and_keeps_splice_calls_and_alleles():
    clinvar = _clinvar_records()
    series = tables.allelic_series(
        uniprot={}, clinvar=clinvar, lof={}, gwas={}, literature={}, effects={},
        clinvar_key="clinvar",
    )  # fmt: skip
    rows = {r["change"]: r for r in series["rows"]}

    assert rows["D374Y"]["origins"] == [
        "ClinVar Pathogenic/Likely pathogenic for Familial hypercholesterolemia (2★, 4 submissions)"
    ]
    assert rows["D374Y"]["alleles"] == [
        {"id": "1-55057454-G-T", "hgvsc": "c.1120G>T", "transcript": "NM_1.4"}
    ]
    assert rows["D374H"]["alleles"][0]["id"] == "1-55057454-G-C"
    # a splice-site call has no protein change and is still a (truncating) row
    assert rows["c.399+1G>A"]["effect"] == "loss (truncating)"
    assert "clinvar" in rows["c.399+1G>A"]["refs"] and "gnomad_clinvar" not in rows["D374Y"]["refs"]
    md = tables.allelic_series_md(series, {"clinvar", "allelic_series"})
    assert "| D374Y · c.1120G>T |" in md
    assert "HGVS c. on NM_1.4" in md
    spectrum = tables.variant_spectrum(clinvar, {}, [], "clinvar")
    assert "[@clinvar]" in tables.variant_spectrum_md(spectrum, {"clinvar"})


def test_one_protein_change_from_two_alleles_lists_both():
    clinvar = {"pathogenic": [
        {"clinical_significance": "Pathogenic", "gold_stars": 1, "hgvsp": "p.Ser127Arg",
         "hgvsc": "c.381T>G", "variant_id": "1-55044016-T-G", "major_consequence": "missense_variant"},
        {"clinical_significance": "Pathogenic", "gold_stars": 2, "hgvsp": "p.Ser127Arg",
         "hgvsc": "c.381T>A", "variant_id": "1-55044016-T-A", "major_consequence": "missense_variant"},
    ]}  # fmt: skip
    series = tables.allelic_series(
        uniprot={}, clinvar=clinvar, lof={}, gwas={}, literature={}, effects={}
    )
    (row,) = series["rows"]

    assert [a["hgvsc"] for a in row["alleles"]] == ["c.381T>G", "c.381T>A"]
    assert tables.variant_name(row) == "S127R · c.381T>G / c.381T>A"


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


def test_calling_a_clinvar_call_wrong_without_a_paper_is_a_warning(run: Path):
    unsupported = check_profile.check(
        run,
        _draft(
            genetics="Two truncating calls are listed [@gnomad_constraint].\n\n*Interpretation:* "
            "the two truncating ClinVar calls are best read as calls for hypocholesterolaemia "
            "or as misclassifications."
        ),
    )
    with_paper = check_profile.check(
        run, _draft(genetics="ClinVar's call was reclassified as a misclassification [@pmid:1].")
    )
    unrelated = check_profile.check(
        run, _draft(biology="The prodomain is best read as an inhibitor [@uniprot_extra].")
    )

    def codes(rep: check_profile.CheckReport) -> list[str]:
        return [w.code for w in rep.warnings if w.code == "W_RECLASSIFICATION"]

    assert codes(unsupported) == ["W_RECLASSIFICATION"]
    assert codes(with_paper) == [] and codes(unrelated) == []


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


# --------------------------------------------------------------------------- functional studies


@pytest.mark.parametrize(
    "change,text,named",
    [
        ("D374Y", "the D374Y mutant", True),
        ("D374Y", "Asp374Tyr binds more tightly", True),
        ("D374Y", "p.D374Y", True),
        ("D374Y", "Asp-374-Tyr", True),
        ("D374Y", "only D374H was tested", False),  # another allele at the same codon
        ("Y142*", "Y142X carriers", True),
        ("Y142*", "Tyr142Ter", True),
        ("C679X", "the C679X allele", True),
        ("L15fs", "an L15fs frameshift", True),
        ("c.399+1G>A", "the c.399+1G>A splice variant", True),
        ("R46L", "AR46LX", False),  # inside another token
    ],
)
def test_a_variant_is_found_in_the_ways_papers_write_it(change, text, named):
    assert bool(tables.variant_pattern(change).search(text)) is named


_ABSTRACT = (
    "We expressed the P112Q mutant in HEK293 cells. P112Q increased calcium influx "
    "three-fold over wild type in whole-cell recordings."
)
_ENTRY = {
    "variant": "P112Q", "readout": "calcium influx", "direction": "gain",
    "system": "HEK293 cells, whole-cell recordings", "pmid": "111",
    "quote": "P112Q increased calcium influx three-fold over wild type",
}  # fmt: skip
_ROWS = [{"change": "P112Q", "alleles": [], "disease_variant": True, "effect": ""}]


def test_a_functional_entry_must_be_the_papers_own_finding():
    ok = tables.function_problems([_ENTRY], _ROWS, {"111": _ABSTRACT}, {"pmid:111"})
    assert ok == []

    def problems(**change: Any) -> list[str]:
        return tables.function_problems(
            [{**_ENTRY, **change}], _ROWS, {"111": _ABSTRACT, "222": "No variant here."},
            {"pmid:111", "pmid:222"},
        )  # fmt: skip

    assert "not in the abstract" in problems(quote="P112Q abolished channel activity")[0]
    assert "not registered" in problems(pmid="333")[0]
    assert "does not name P112Q" in " ".join(problems(pmid="222", quote="No variant here."))
    assert "not a variant of the allelic series" in problems(variant="G20D")[0]
    assert "direction must be" in problems(direction="activating")[0]
    assert "missing quote" in problems(quote="")[0]


def test_paper_evidence_is_shown_apart_from_the_records_and_tallied_separately():
    rows = [
        {**_series_row(), "disease_variant": True, "alleles": [],
         "clinical": ["UniProt: FSGS2"], "population": ["GWAS lead: eGFR"],
         "literature": ["named in 4 papers"]},
        {**_series_row(), "change": "R895*", "consequence": "stop_gained", "uniprot_note": "",
         "effect": "loss (truncating)", "disease_variant": True, "alleles": []},
    ]  # fmt: skip
    functions = tables.functions_by_variant([_ENTRY])
    md = tables.allelic_series_md(
        {"total": 2, "rows": rows}, {"uniprot_extra", "allelic_series", "pmid:111"}, functions
    )
    header, _, first, second = md.splitlines()[:4]

    assert (
        "| Molecular effect | Clinical classification | Population / trait | Literature |" in header
    )
    assert "gain: calcium influx (HEK293 cells, whole-cell recordings) [paper]" in first
    assert "| UniProt: FSGS2 | GWAS lead: eGFR | named in 4 papers |" in first
    assert first.endswith("[@uniprot_extra; @pmid:111] |")
    assert "loss (truncating) [records]" in second
    tally = md.splitlines()[-1]
    assert "typed from the records: truncating (loss) 1 · not typed 1" in tally
    assert "functional studies give: gain 1 [@pmid:111]" in tally


def test_an_unverified_functional_entry_fails_the_check(run: Path):
    _raw(run, "allelic_series", {"series": {"total": 1, "rows": [_series_row()]}, "spectrum": {}})
    _raw(run, "pubmed_abstracts", {"papers": {"1": {"abstract": _ABSTRACT}}})
    (run / tables.FUNCTION_FILE).write_text(
        json.dumps([{**_ENTRY, "pmid": "1", "quote": "a sentence the paper never wrote"}])
    )
    rep = check_profile.check(
        run, _draft(genetics="Rows below [@gnomad_constraint].\n\n{{table:allelic_series}}")
    )
    assert [i.code for i in rep.errors] == ["E_VARIANT_FUNCTION"]
    assert "not in the abstract of pmid:1" in rep.errors[0].message


# --------------------------------------------------------------------------- safety


def test_the_drug_safety_query_asks_pooled_trials_about_the_approved_drugs_or_the_target():
    ctx = gather.Ctx(query="X", symbol="TST1")
    ctx.data["ot_known_drugs"] = {"drugs": [
        {"drug_name": "DRUGMAB", "is_approved": True},
        {"drug_name": "DRUGSIRAN SODIUM", "is_approved": True},  # salt form merged
        {"drug_name": "TRIALMAB", "is_approved": False},  # not approved: not asked about
    ]}  # fmt: skip
    q = gather.q_drug_safety(ctx)

    assert q.startswith('("drugmab"[tiab] OR "drugsiran"[tiab] OR "TST1"[tiab])')
    assert "trialmab" not in q
    assert '("meta-analysis"[pt] OR "systematic review"[pt])' in q
    assert gather._need_approved_drug(gather.Ctx(query="X", symbol="TST1")) is not None


@pytest.mark.parametrize(
    "text,flagged",
    [
        (
            "*Interpretation:* both drugs act in plasma, which fits their clean safety records.",
            True,
        ),
        ("Inhibition is considered safe [@gnomad_constraint].", True),
        ("No safety liabilities were found [@gnomad_constraint].", True),
        (
            "No curated target-safety liability is listed in Open Targets [@gnomad_constraint].",
            False,
        ),
        ("The drug was well tolerated in its trials [@gnomad_constraint].", False),
        ("Lowering the dose makes it safer [@gnomad_constraint].", False),
    ],
)
def test_a_safety_conclusion_the_sources_cannot_carry_is_a_warning(run: Path, text, flagged):
    rep = check_profile.check(run, _draft(safety=text))
    assert ("W_OVERCLAIM_SAFETY" in [w.code for w in rep.warnings]) is flagged


@pytest.mark.parametrize(
    "text,flagged",
    [
        ("FAERS shows a death rate of 0.5% for the drug [@gnomad_constraint].", True),
        ("FAERS holds more reports for drug A than for drug B [@gnomad_constraint].", True),
        ("Drug C has no US FAERS reports, so it looks safe [@gnomad_constraint].", True),
        (
            "FAERS lists 120 reports for drug A, mostly injection-site terms [@gnomad_constraint].",
            False,
        ),
    ],
)
def test_faers_read_as_a_rate_or_a_ranking_is_a_warning(run: Path, text, flagged):
    rep = check_profile.check(run, _draft(safety=text))
    assert ("W_FAERS_INFERENCE" in [w.code for w in rep.warnings]) is flagged


def test_the_faers_table_keeps_handling_terms_apart_and_states_what_reports_cannot_show():
    data = {
        "by_drug": {"DRUGMAB": {"total_reports": 1000, "serious_reports": 100, "death_reports": 5,
                                "serious_rate": 0.1, "death_rate": 0.005, "top_reactions": [
                                    {"reaction": "DEVICE DIFFICULT TO USE", "count": 300},
                                    {"reaction": "MYALGIA", "count": 50}]}},
        "no_faers_reports": ["OTHERMAB"],
    }  # fmt: skip
    md = tables.faers_md(data, "2026-10-24T18:00:00+00:00", {"faers"})
    row = md.splitlines()[2]

    assert (
        "| Drugmab | 1,000 | 100 (10.0%) | 5 (0.5%) | myalgia (50) | device difficult to use (300) |"
        in row
    )
    assert "not incidence" in md and "does not rank drugs" in md and "retrieved 2026-10-24" in md
    assert "Othermab" in md and "not evidence of safety" in md


def test_the_constraint_table_reads_population_depletion_on_a_named_transcript():
    data = {"loeuf": 1.14, "pli": 2.8e-18, "obs_lof": 57, "exp_lof": 62.1, "mis_z": 1.24,
            "moeuf": 0.96, "dataset": "gnomAD v4 (GRCh38)", "transcript": "ENST1.5 (NM_1.4, MANE Select)"}  # fmt: skip
    md = tables.constraint_md(data, {"gnomad_constraint"})

    assert "| LOEUF | 1.14 | no strong depletion of pLoF variants (NOT haploinsufficient) |" in md
    assert "| Observed / expected pLoF | 57 / 62.1 |" in md
    assert "Computed by gnomAD v4 (GRCh38), ENST1.5 (NM_1.4, MANE Select)." in md
    assert "does not establish whether a given variant is pathogenic" in md
    assert "tolerant" not in md.lower()
    assert tables.constraint_md({}, {"gnomad_constraint"}) == ""


def test_each_screen_gives_its_own_gene_level_call_and_per_line_rule():
    md = tables.essentiality_md(
        {"release": "DepMap 24Q4 Public", "is_common_essential": False, "num_dependent_lines": 17,
         "total_lines": 1178},
        {"is_pancan_core_fitness": False, "cancer_specific_core_fitness_tissues": ["Breast"],
         "num_fitness_lines": 55, "total_lines": 350},
        {"depmap", "project_score"},
    )  # fmt: skip
    assert (
        "| neither common essential nor strongly selective | 17 of 1178 | Chronos gene effect ≤ −0.5 |"
        in md
    )
    assert (
        "| core fitness in Breast | 55 of 350 | scaled Bayes factor < 0, without FDR control |"
        in md
    )
    assert "not comparable across screens" in md and "tumour–immune" in md
    assert tables.essentiality_md({}, {}, set()) == ""


@pytest.mark.parametrize(
    "text,flagged",
    [
        ("GENE1 is not a cancer dependency: 17 of 1178 lines [@gnomad_constraint].", True),
        ("It is irrelevant to cancer biology [@gnomad_constraint].", True),
        (
            "GENE1 is not a broadly essential cancer-cell fitness gene in DepMap [@gnomad_constraint].",
            False,
        ),
    ],
)
def test_a_screen_stretched_beyond_cell_fitness_is_a_warning(run: Path, text, flagged):
    rep = check_profile.check(run, _draft(biology=text))
    assert ("W_ESSENTIALITY_SCOPE" in [w.code for w in rep.warnings]) is flagged


def test_tissue_evidence_keeps_rna_protein_and_secretion_apart_and_says_what_each_cannot_show():
    hpa = {
        "RNA tissue specificity": "Tissue enriched",
        "RNA tissue specific nTPM": {"liver": "47.5"},
        "Protein tissue specificity": "Group enriched",
        "Evidence": "Evidence at protein level",
        "Secretome location": "Secreted to blood",
        "Blood concentration - Conc. blood MS [pg/L]": 190000000,
    }
    gtex = {"gtex_expressions": [{"tissue": "Brain_Cerebellum", "median_tpm": 22.1}],
            "hpa_subcellular_location": ["Endosome"]}  # fmt: skip
    md = tables.tissue_evidence_md(hpa, gtex, {"hpa", "gtex"})

    assert (
        "| RNA, tissues (GTEx) | Brain Cerebellum 22.1 TPM | protein amount, secretion, function |"
        in md
    )
    assert "| Protein (HPA) | Group enriched; Evidence at protein level |" in md
    assert "Secreted to blood; Endosome; blood 190 ng/mL (MS)" in md
    assert "Abundance is not function" in md
