# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The gene profile's researcher blocks: new records (HPA immune fields, mouse orthology,
paper metadata), the analyses built on them, the blocks they render and their empty states.

Offline: every record is a fixture. Gene and variant names appear only as fixture data."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gather  # noqa: E402
import profile_biology as bio  # noqa: E402
import profile_strength as strength  # noqa: E402

# ------------------------------------------------------------------ HPA fields

# Abridged from a real HPA record (https://www.proteinatlas.org/<ENSG>.json, 2026-10-31).
_HPA_RECORD: dict[str, Any] = {
    "Gene": "COL4A4",
    "RNA tissue specificity": "Tissue enhanced",
    "RNA blood cell specificity": "Immune cell enhanced",
    "RNA blood cell specific nTPM": {"memory B-cell": "2.7"},
    "RNA blood lineage specificity": "Lineage enriched",
    "RNA blood lineage specific nTPM": {"B-cells": "2.7"},
    "RNA cell line specificity": "Cancer enhanced",
    "RNA cell line specific nTPM": {"Myeloma": "9.1"},
    "Antibody": None,
    "Antibody RRID": None,
    "Reliability (IF)": None,
    "Blood expression cluster": "Cluster 23: B-cells - Immunoglobulins",
    "Cell line expression cluster": "Cluster 15: Kidney cancer - Membrane components",
    "Secretome location": "Secreted to extracellular matrix",
}


def test_hpa_keeps_blood_cell_line_and_antibody_fields() -> None:
    out = gather.hpa_fields(_HPA_RECORD)
    assert out["RNA blood cell specificity"] == "Immune cell enhanced"
    assert out["RNA blood lineage specific nTPM"] == {"B-cells": "2.7"}
    assert out["RNA cell line specific nTPM"] == {"Myeloma": "9.1"}
    assert out["Blood expression cluster"].startswith("Cluster 23")
    assert "Antibody" not in out  # null in the record: dropped, not shown as a value
    assert "Gene" not in out  # not a field the profile uses


def test_hpa_keeps_its_own_negative_wording() -> None:
    out = gather.hpa_fields({"RNA blood cell specificity": "Not detected in immune cells"})
    assert out == {"RNA blood cell specificity": "Not detected in immune cells"}


def test_hpa_every_new_key_absent() -> None:
    assert gather.hpa_fields({"Gene": "X", "RNA tissue specificity": "Low tissue specificity"}) == {
        "RNA tissue specificity": "Low tissue specificity"
    }


# ---------------------------------------------------------------- orthology

_NCBI = [{"gene_id": "1001", "symbol": "Abc1", "type": "PROTEIN_CODING"}]
_NCBI_NC = [{"gene_id": "1002", "symbol": "Lnc1", "type": "ncRNA"}]
_ONE2ONE = [
    {
        "id": "ENSMUSG1",
        "type": "ortholog_one2one",
        "identity_target": 76.513,
        "identity_source": 76.7,
    }
]
_MANY = [
    {"id": "ENSMUSG2", "type": "ortholog_many2many", "identity_target": 33.6, "identity_source": 31.2},
    {"id": "ENSMUSG3", "type": "ortholog_many2many", "identity_target": 37.1, "identity_source": 28.9},
]  # fmt: skip


def _orth(
    ncbi: list[dict[str, Any]], ensembl: list[dict[str, Any]], error: str = ""
) -> dict[str, Any]:
    out = {
        **bio.ortholog_reading(ncbi, ensembl),
        "ncbi": {"orthologs": ncbi, "release": "18.40.0"},
        "ensembl": {"homologues": ensembl, "release": "116", "error": error},
    }
    out["summary"] = bio.ortholog_summary(out)
    return out


def test_conserved_protein_coding_ortholog_has_type_and_identity() -> None:
    d = _orth(_NCBI, _ONE2ONE)
    assert d["reading"] == "ortholog"
    assert d["ortholog"]["symbol"] == "Abc1"
    assert d["ortholog"]["ensembl_type"] == "ortholog_one2one"
    assert "77% identity (Ensembl one-to-one)" in d["summary"]


def test_noncoding_ortholog_without_ensembl_match() -> None:
    d = _orth(_NCBI_NC, [])
    assert d["reading"] == "ortholog"
    assert "identity not available" in d["summary"]


def test_distant_homologues_are_never_the_ortholog() -> None:
    d = _orth([], _MANY)
    assert d["reading"] == "distant_homologues"
    assert d["ortholog"] is None
    assert [h["id"] for h in d["distant_homologues"]] == ["ENSMUSG3", "ENSMUSG2"]  # best first
    assert "No mouse ortholog in NCBI's curated set" in d["summary"]
    assert "many-to-many, best identity 37%" in d["summary"]


def test_missing_from_both_is_worded_as_not_found() -> None:
    d = _orth([], [])
    assert d["reading"] == "not_found"
    assert "not found, not shown absent" in d["summary"]
    assert "18.40.0" in d["summary"] and "116" in d["summary"]


def test_ensembl_failure_is_not_checked_not_absent() -> None:
    assert "not checked" in _orth(_NCBI, [], error="HTTPStatusError: 503")["summary"]
    assert "Ensembl not checked" in _orth([], [], error="timeout")["summary"]


def test_ortholog_spec_reports_not_found_as_empty() -> None:
    spec = gather.SPEC_BY_KEY["orthologs"]
    assert spec.found is not None
    assert spec.found(_orth([], [])) is False
    assert spec.found(_orth(_NCBI, [])) is True


# ------------------------------------------------------------ paper metadata


def test_paper_metadata_keeps_types_mesh_and_every_author() -> None:
    meta = SimpleNamespace(
        publication_types=["Journal Article", "Meta-Analysis"],
        mesh_headings=[
            SimpleNamespace(descriptor="Hypercholesterolemia", ui="D006937", major=True)
        ],
        indexed=True,
        authors=[f"Author{i} A" for i in range(9)],
    )
    out = strength.paper_metadata(meta)
    assert out["publication_types"] == ["Journal Article", "Meta-Analysis"]
    assert out["mesh"] == [{"descriptor": "Hypercholesterolemia", "ui": "D006937", "major": True}]
    assert out["indexed"] is True
    assert out["authors"][-1] == "Author8 A"  # past esummary's six


def test_unindexed_paper_metadata() -> None:
    meta = SimpleNamespace(
        publication_types=["Preprint"], mesh_headings=[], indexed=False, authors=[]
    )
    assert strength.paper_metadata(meta)["indexed"] is False
    assert strength.paper_metadata(None) == {
        "publication_types": [],
        "mesh": [],
        "indexed": False,
        "authors": [],
    }


async def test_mesh_trees_are_cached_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    async def lookup(uis: list[str]) -> dict[str, list[str]]:
        calls.append(uis)
        return {u: [f"C14.{u[-3:]}"] for u in uis}

    monkeypatch.setattr(gather, "_MESH_CACHE", tmp_path / "mesh.json")
    monkeypatch.setattr(gather.pubmed, "mesh_tree_numbers", lookup)
    assert await gather.mesh_trees(["D000001"]) == {"D000001": ["C14.001"]}
    assert await gather.mesh_trees(["D000001", "D000002"]) == {
        "D000001": ["C14.001"],
        "D000002": ["C14.002"],
    }
    assert calls == [["D000001"], ["D000002"]]  # the cached descriptor is not asked again


# ---------------------------------------------------------- presentation basics

import profile_blocks as blocks  # noqa: E402
import profile_schema as ds  # noqa: E402
import profile_tables as tables  # noqa: E402
import render  # noqa: E402


def test_series_literature_column_drops_relation_tags_of_old_rows() -> None:
    row = {
        "change": "A10T", "position": 10, "rsid": "", "consequence": "missense_variant",
        "region": "", "effect": "", "effect_source": "", "uniprot_note": "", "refs": [],
        "literature": ["Disease X (Association, Positive_Correlation tags, 12 papers)"],
    }  # fmt: skip
    md = tables.allelic_series_md({"total": 1, "rows": [row]}, set())
    assert "Disease X (12 papers)" in md
    assert "tags" not in md and "Positive_Correlation" not in md


def test_provenance_endings() -> None:
    assert (
        blocks.provenance("records", ["gwas", "gwas", "ot_coloc"])
        == "From records: gwas, ot_coloc."
    )
    assert blocks.provenance("llm", ["pmid:1"]) == "Chosen by the LLM from: pmid:1."


def _raw(run: Path, key: str, data: Any, status: str = "ok", note: str = "") -> None:
    rec = ds.RawRecord(
        key=key, section="biology", db_name=f"DB {key}", tool="t", status=status, note=note,
        retrieved_at="2026-10-31T00:00:00+00:00", data=data,
    )  # fmt: skip
    (run / "raw").mkdir(parents=True, exist_ok=True)
    (run / "raw" / f"{key}.json").write_text(rec.model_dump_json())


def test_every_block_caption_carries_a_source_ending(tmp_path: Path) -> None:
    _raw(tmp_path, "gnomad_constraint", {"loeuf": 0.4, "pli": 0.1, "mis_z": 1.0})
    _raw(tmp_path, "depmap", {"num_dependent_lines": 1, "total_lines": 10})
    draft = "{{table:constraint}}\n\n{{table:essentiality}}\n\n{{table:structures}}"
    out, _notes = blocks.expand(
        draft, tmp_path, "G", {"gnomad_constraint", "depmap"}, figures=False
    )
    assert "*From records: gnomad_constraint.*" in out
    assert "*From records: depmap.*" in out


def test_a_block_without_data_renders_its_empty_state(tmp_path: Path) -> None:
    _raw(tmp_path, "uniprot_extra", {}, status="empty")
    _raw(tmp_path, "faers", None, status="skipped", note="no approved drug")
    out, notes = blocks.expand(
        "{{table:structures}}\n{{table:faers}}\n{{table:constraint}}",
        tmp_path, "G", {"uniprot_extra"}, figures=False,
    )  # fmt: skip
    lines = out.splitlines()
    assert lines[0] == "*No PDB entry and no AlphaFold model.* [@uniprot_extra]"
    assert lines[1] == "*Not checked: DB faers skipped (no approved drug).*"
    assert lines[2] == "*Not checked: `gnomad_constraint` was not gathered in this run.*"
    assert notes == [
        f"{n}: empty state" for n in ("table:structures", "table:faers", "table:constraint")
    ]


def test_legend_says_what_is_from_records_and_what_the_llm_chose() -> None:
    assert "*from records*" in render.LEGEND and "*chosen by the LLM*" in render.LEGEND


def test_terms_are_footnoted_once_outside_code_links_and_headings() -> None:
    terms = [
        {"term": "TPM", "pattern": "TPM", "text": "t"},
        {"term": "nTPM", "pattern": "nTPM", "text": "n"},
        {"term": "GWAS", "pattern": "GWAS", "text": "g"},
    ]
    body = (
        "## GWAS heading\n"
        "See `GWAS` and [GWAS](http://x/GWAS) [@gwas].\n"
        "HPA nTPM and GTEx TPM; GWAS hits; TPM again.\n"
    )
    out, notes = render.footnote_terms(body, terms)
    first, second, third = out.splitlines()
    assert first == "## GWAS heading"
    assert second == "See `GWAS` and [GWAS](http://x/GWAS) [@gwas]."  # code and links skipped
    assert third == (
        "HPA nTPM<sup>[t1](#term-1)</sup> and GTEx TPM<sup>[t2](#term-2)</sup>; "
        "GWAS<sup>[t3](#term-3)</sup> hits; TPM again."
    )
    assert notes.splitlines()[2].startswith('<a id="term-1"></a>t1. **nTPM**')


def test_old_template_runs_keep_their_sections_new_runs_use_v2() -> None:
    assert ds.Manifest(query="G", gene="G", generated_at="x").template == 1
    v1 = [k for k, _ in ds.draft_sections(1)]
    v2 = [k for k, _ in ds.draft_sections(2)]
    assert "literature" in v1 and "druggability" in v1
    assert v2[-3:] == ["status", "translational", "gaps"] and "field" in v2
    assert ds.TEMPLATE_VERSION == 2


# ------------------------------------------------------------ literature grounding

import profile_literature as lit  # noqa: E402

from mcp_servers.pubtator.tools import (  # noqa: E402
    PubTatorAnnotation,
    PubTatorDocument,
    PubTatorPassage,
)
from services.evidence.grounding import TargetIdentity  # noqa: E402

_TARGET = TargetIdentity(symbol="ABC1", ncbi_id="111", ortholog_ids=["222"])


def _tagged(
    pmid: str, text: str, token: str, kind: str = "", identifier: str = ""
) -> PubTatorDocument:
    """A one-passage document whose ``token`` is annotated as ``kind`` (none: untagged)."""
    start = text.index(token)
    anns = (
        [
            PubTatorAnnotation(
                type=kind, text=token, offset=100 + start, length=len(token), identifier=identifier
            )
        ]
        if kind
        else []
    )
    return PubTatorDocument(
        pmid=pmid,
        passages=[PubTatorPassage(kind="abstract", offset=100, text=text, annotations=anns)],
    )


def test_word_like_symbol_is_mostly_untagged() -> None:
    docs = [_tagged(str(i), "Patients who met the criteria", "met") for i in range(8)]
    docs += [_tagged("8", "MET was amplified", "MET", "Gene", "111")]
    docs += [_tagged("9", "MET in mouse", "MET", "Gene", "222")]  # the ortholog counts
    docs += [
        PubTatorDocument(
            pmid="10", passages=[PubTatorPassage(kind="title", offset=0, text="no token")]
        )
    ]
    row = lit.token_senses(docs, "MET", _TARGET)
    assert row["papers"] == 10  # the paper without the token in its text is not counted
    assert row["shares"] == {"this gene": 0.2, "untagged": 0.8}
    assert row["this_gene"] == 0.2


def test_chemical_like_symbol_and_another_gene() -> None:
    docs = [
        _tagged(str(i), "LPA receptor signalling", "LPA", "Chemical", "MESH:D1") for i in range(6)
    ]
    docs += [_tagged("6", "LPA levels", "LPA", "Gene", "999")]
    docs += [_tagged(str(i), "plasma LPA", "LPA", "Gene", "111") for i in range(7, 10)]
    row = lit.token_senses(docs, "LPA", _TARGET)
    assert row["shares"] == {"this gene": 0.3, "another gene": 0.1, "chemical": 0.6}


def test_renamed_symbol_is_kept_and_a_shared_alias_is_dropped() -> None:
    senses = [
        {"term": "ABC1", "papers": 40, "this_gene": 0.9, "shares": {"this gene": 0.9}},
        {"term": "ABC-OLD9", "papers": 30, "this_gene": 0.95, "shares": {"this gene": 0.95}},
        {"term": "XY", "papers": 30, "this_gene": 0.9, "shares": {"this gene": 0.9}},
        {
            "term": "WORD",
            "papers": 30,
            "this_gene": 0.1,
            "shares": {"this gene": 0.1, "untagged": 0.9},
        },
        {"term": "RARE", "papers": 3, "this_gene": 1.0, "shares": {"this gene": 1.0}},
    ]
    chosen = lit.choose_terms("ABC1", senses, {"XY": ["DEF2"]})
    assert chosen["terms"] == ["ABC1", "ABC-OLD9"]
    reasons = {x["term"]: x["reason"] for x in chosen["dropped"]}
    assert reasons == {
        "XY": "also a symbol of DEF2",
        "WORD": "mostly untagged in its hits",
        "RARE": "3 sampled hits hold it",
    }


def test_symbol_is_searched_even_when_ambiguous() -> None:
    row = {"term": "MET", "papers": 50, "this_gene": 0.02, "shares": {"untagged": 0.98}}
    assert lit.choose_terms("MET", [row], {})["terms"] == ["MET"]


def test_grounded_by_gene2pubmed_or_tag_never_by_a_drug_alone() -> None:
    drug_only = PubTatorDocument(
        pmid="3",
        passages=[PubTatorPassage(kind="abstract", offset=0, text="drugx", annotations=[
            PubTatorAnnotation(type="Chemical", text="drugx", offset=0, length=5, identifier="MESH:D9")
        ])],
    )  # fmt: skip
    docs = [
        _tagged("1", "ABC1 binds", "ABC1", "Gene", "111"),
        _tagged("2", "no tag here", "tag"),  # untagged, but linked by gene2pubmed
        drug_only,
    ]
    m = lit.build_map(docs, _TARGET, drug_names=["drugx"], listed=["1", "2", "3"], curated=["2"])
    assert m["grounded_pmids"] == ["1", "2"]
    assert m["papers"]["3"]["names_target"] is True  # relevant to cite (its drug) …
    assert m["papers"]["3"]["grounded"] is False  # … but not the gene's literature
    assert m["papers"]["2"]["routes"] == ["gene2pubmed"]
    assert lit.grounded_set(m) == {"1", "2"}
    assert lit.grounded_set({}) == set()


# ------------------------------------------------------------ evidence strength

import json  # noqa: E402

import check_profile  # noqa: E402
import profile_writer as writer  # noqa: E402

from mcp_servers.pubmed.design import mesh_species, study_design  # noqa: E402


@pytest.mark.parametrize(
    ("types", "mesh", "design"),
    [
        (["Journal Article", "Meta-Analysis"], [], "meta-analysis"),
        (["Randomized Controlled Trial"], ["Humans"], "randomized controlled trial"),
        (["Clinical Trial, Phase II"], [], "clinical trial"),
        (["Journal Article"], ["Genome-Wide Association Study", "Humans"], "genetic association"),
        (["Journal Article"], ["Prospective Studies"], "cohort"),
        (["Journal Article"], ["Case-Control Studies"], "case-control"),
        (["Case Reports"], ["Humans"], "case report"),
        (["Journal Article"], ["Crystallography, X-Ray"], "structure"),
        (["Journal Article"], ["Mice, Knockout", "Animals"], "animal model"),
        (["Journal Article"], ["HEK293 Cells"], "cells / in vitro"),
        (["Review"], [], "review"),
        (["Journal Article"], ["Humans"], "other primary research"),
    ],
)
def test_design_from_publication_types_and_mesh(
    types: list[str], mesh: list[str], design: str
) -> None:
    assert study_design(types, mesh) == design


def test_unindexed_paper_design_and_species() -> None:
    assert study_design(["Journal Article"], [], indexed=False) == "not indexed"
    assert mesh_species(["Humans", "Mice", "Animals", "Male"]) == ["human", "mouse"]


@pytest.mark.parametrize(
    ("abstract", "n"),
    [
        ("We studied n = 1,234 adults.", 1234),
        ("In 52,341 participants and 312 cases we found", 52341),
        ("Knockout mice (N=12) showed", 12),
        ("Twelve mice were used.", None),
        ("", None),
    ],
)
def test_stated_n(abstract: str, n: int | None) -> None:
    assert strength.stated_n(abstract) == n


def test_paper_strength_prefers_pubtator_species_and_reads_last_author() -> None:
    paper = {
        "publication_types": ["Journal Article"],
        "mesh": [{"descriptor": "Humans", "ui": "D006801", "major": False}],
        "indexed": True,
        "authors": ["First A", "Middle B", "Senior C"],
        "abstract": "In 400 patients the variant",
    }
    s = strength.paper_strength("1", paper, {"organisms": ["mouse"]})
    assert s["species"] == ["mouse"] and s["species_source"] == "PubTator"
    assert s["last_author"] == "Senior C" and s["n"] == 400
    s = strength.paper_strength("1", paper)
    assert s["species"] == ["human"] and s["species_source"] == "MeSH"


def _strength(design: str, species: list[str], author: str, n: int | None = None) -> dict[str, Any]:
    return {"design": design, "species": species, "last_author": author, "n": n}


_STRENGTHS = {
    "1": _strength("genetic association", ["human"], "Lab A", 12000),
    "2": _strength("cohort", ["human"], "Lab B", 900),
    "3": _strength("other primary research", ["mouse"], "Lab C"),
    "4": _strength("cells / in vitro", [], "Lab A"),
    "5": _strength("cells / in vitro", [], "Lab A"),
    "6": _strength("review", ["human"], "Lab D"),
    "7": _strength("randomized controlled trial", ["human"], "Lab E", 27000),
}


def test_claim_cells_count_papers_groups_and_n_per_kind() -> None:
    claim = {
        "claim": "x",
        "supports": ["pmid:1", "pmid:2", "pmid:3", "gnomad_lof", "pmid:6"],
        "contradicts": ["pmid:7"],
    }
    cells = writer.claim_cells(claim, _STRENGTHS)
    assert cells["human"]["papers"] == 2 and len(cells["human"]["groups"]) == 2
    assert cells["human"]["n"] == 12000 and cells["human"]["records"] == ["gnomad_lof"]
    assert cells["animal"]["species"] == {"mouse"}
    assert cells["pharmacology"]["contra"] == ["pmid:7"]
    assert not writer.is_weak(cells)
    assert "pmid:6" not in [k for c in cells.values() for k in c["support"]]  # a review


def test_a_cell_paper_that_also_studied_a_model_organism_counts_as_animal() -> None:
    both = _strength("cells / in vitro", ["human", "mouse"], "Lab F")
    assert strength.paper_kind(both) == "animal"
    assert strength.paper_kind(_strength("cells / in vitro", ["human"], "Lab F")) == "cells"
    assert strength.paper_kind(_strength("structure", ["mouse"], "Lab F")) == "cells"


def test_one_group_or_cells_only_is_weak() -> None:
    cells_only = writer.claim_cells({"claim": "x", "supports": ["pmid:4", "pmid:5"]}, _STRENGTHS)
    one_group = writer.claim_cells({"claim": "x", "supports": ["pmid:1"]}, _STRENGTHS)
    with_record = writer.claim_cells(
        {"claim": "x", "supports": ["pmid:1", "gnomad_lof"]}, _STRENGTHS
    )
    assert writer.is_weak(cells_only) and writer.is_weak(one_group)
    assert not writer.is_weak(with_record)


def test_how_we_know_table_cells_and_empty_cells() -> None:
    entries = [
        {
            "claim": "Variants lower activity",
            "supports": ["pmid:1", "pmid:2"],
            "contradicts": ["pmid:7"],
        }
    ]
    md, keys = writer.how_we_know_md(entries, _STRENGTHS, {"pmid:1", "pmid:2", "pmid:7"})
    row = md.splitlines()[2]
    assert "✓ 2 papers · 2 groups · largest n 12,000 [@pmid:1; @pmid:2]" in row
    assert "✗ 1 source against [@pmid:7]" in row
    assert row.count("none cited") == 2  # animal, cells
    assert keys == ["pmid:1", "pmid:2", "pmid:7"]
    assert "3 of 3 cited papers classified" in md


def test_how_problems() -> None:
    sources = {"pmid:1": object()}
    assert writer.how_problems({"claim": "x"}, sources) == [
        "how_we_know.json must be a JSON list of claims"
    ]
    problems = writer.how_problems(
        [
            {"claim": "a", "supports": []},
            {"claim": "b", "supports": ["pmid:9"]},
            {"supports": ["pmid:1"]},
        ],
        sources,
    )
    assert problems == [
        "claim 1 ('a'): no supporting key",
        "claim 2: [@pmid:9] not in sources.json",
        "claim 3: needs a 'claim' text",
    ]


def _v2_run(tmp_path: Path) -> Path:
    """A template-2 run with three papers: two by one lab on cells, one cohort."""
    papers = {
        "11": {"abstract": "In HEK293 cells the channel", "publication_types": ["Journal Article"],
               "mesh": [{"descriptor": "HEK293 Cells", "ui": "D057809", "major": False}],
               "indexed": True, "authors": ["X A", "Lab One"]},
        "12": {"abstract": "In HeLa cells", "publication_types": ["Journal Article"],
               "mesh": [{"descriptor": "HeLa Cells", "ui": "D006367", "major": False}],
               "indexed": True, "authors": ["Y B", "Lab One"]},
        "13": {"abstract": "In 5,000 participants", "publication_types": ["Journal Article"],
               "mesh": [{"descriptor": "Cohort Studies", "ui": "D015331", "major": False},
                        {"descriptor": "Humans", "ui": "D006801", "major": False}],
               "indexed": True, "authors": ["Z C", "Lab Two"]},
    }  # fmt: skip
    _raw(tmp_path, "pubmed_abstracts", {"papers": papers})
    sources = {
        f"pmid:{p}": ds.Source(key=f"pmid:{p}", kind="article", title="t", url="u", accessed="d",
                               pmid=p, verified=True)
        for p in papers
    }  # fmt: skip
    sources["gnomad_lof"] = ds.Source(
        key="gnomad_lof", kind="database", title="t", url="u", accessed="d", verified=True
    )
    ds.save_sources(tmp_path, sources)
    manifest = ds.Manifest(query="G", gene="G", generated_at="2026-10-31", template=2)
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    _raw(tmp_path, "uniprot_locations", {"names": ["Cell membrane", "Cytoplasm", "Secreted"]})
    return tmp_path


_STEPS = [
    {"actor": "A", "action": "binds", "target": "B", "compartment": "Cell membrane", "keys": ["pmid:13", "gnomad_lof"]},
    {"actor": "B", "action": "signals to", "target": "C", "compartment": "Cytoplasm", "keys": ["pmid:11"]},
    {"actor": "C", "action": "is secreted", "target": "plasma", "compartment": "Secreted", "keys": ["pmid:12"]},
    {"actor": "plasma C", "action": "lowers", "target": "D", "compartment": "Secreted", "keys": ["pmid:13"]},
]  # fmt: skip


def _v2_draft(omit: tuple[str, ...] = (), **sections: str) -> str:
    """A template-2 draft: every heading, and every required block in its section (the
    snapshot's how_we_know only where a test writes it)."""
    parts = []
    for key, title in ds.draft_sections(2):
        if key == "indication":
            continue
        body = sections.get(key, "Placeholder fact [@gnomad_lof].")
        extra = [
            f"{{{{{name}}}}}"
            for name, sec in blocks.REQUIRED_V2.items()
            if sec == key and name not in omit and name != "table:how_we_know"
        ]
        parts += [f"## {title}", "", body, "", *extra, ""]
    return "\n".join(parts)


def test_template_2_requires_how_we_know(tmp_path: Path) -> None:
    run = _v2_run(tmp_path)
    rep = check_profile.check(
        run, _v2_draft(snapshot="{{table:how_we_know}}\n\nFact [@gnomad_lof].")
    )
    assert [i.code for i in rep.errors] == ["E_HOW_WE_KNOW"]
    (run / writer.HOW_FILE).write_text(
        json.dumps([{"claim": "c", "supports": ["pmid:13", "gnomad_lof"]}])
    )
    rep = check_profile.check(
        run, _v2_draft(snapshot="{{table:how_we_know}}\n\nFact [@gnomad_lof].")
    )
    assert rep.ok, rep.errors
    missing = check_profile.check(run, _v2_draft())
    assert [i.code for i in missing.errors] == ["E_MISSING_TABLE"]
    no_models = check_profile.check(
        run, _v2_draft(omit=("table:models",), snapshot="{{table:how_we_know}}\n\nF [@gnomad_lof].")
    )
    assert [i.message for i in no_models.errors] == [
        "'{{table:models}}' missing from section 'biology'"
    ]


def test_strength_prose_warns_on_established_for_a_one_lab_cell_claim(tmp_path: Path) -> None:
    run = _v2_run(tmp_path)
    (run / writer.HOW_FILE).write_text(
        json.dumps([
            {"claim": "The channel conducts calcium", "supports": ["pmid:11", "pmid:12"]},
            {"claim": "Carriers have lower levels", "supports": ["pmid:13", "gnomad_lof"]},
        ])
    )  # fmt: skip
    snapshot = "{{table:how_we_know}}\n\nIt is established that the channel conducts calcium [@pmid:11; @pmid:12]."
    rep = check_profile.check(run, _v2_draft(snapshot=snapshot))
    assert [i.code for i in rep.warnings if i.code == "W_STRENGTH_PROSE"] == ["W_STRENGTH_PROSE"]
    fine = "{{table:how_we_know}}\n\nIt is established that carriers have lower levels [@pmid:13; @gnomad_lof]."
    rep = check_profile.check(run, _v2_draft(snapshot=fine))
    assert not [i for i in rep.warnings if i.code == "W_STRENGTH_PROSE"]
    md, _ = blocks.expand(
        "{{table:how_we_know}}", run, "G", set(ds.load_sources(run)), figures=False
    )
    assert "The channel conducts calcium *(one group or cells only)*" in md
    assert md.rstrip().endswith("*Chosen by the LLM from: pmid:11, pmid:12, pmid:13, gnomad_lof.*")


# ------------------------------------------------- biology: models, partners, reagents

_MOUSE = {
    "phenotypes": [
        {"phenotype_label": "abnormal vascular smooth muscle physiology",
         "phenotype_classes": [{"id": "MP:0005385", "label": "cardiovascular system phenotype"}],
         "allelic_compositions": ["Abc1<tm1>/Abc1<tm1>"]},
        {"phenotype_label": "increased T cell number",
         "phenotype_classes": [{"id": "MP:0005387", "label": "immune system phenotype"},
                               {"id": "MP:0005397", "label": "hematopoietic system phenotype"}],
         "allelic_compositions": ["Abc1<tm1>/Abc1<tm1>"]},
    ]
}  # fmt: skip


def test_immune_trigger_by_each_route_and_none() -> None:
    assert bio.immune_trigger({"RNA blood cell specificity": "Group enriched"}, {}, {}) == [
        "HPA blood cells: Group enriched"
    ]
    hpo = {
        "terms": [
            {"id": "HP:0005390", "label": "Recurrent opportunistic infections", "immune": True}
        ]
    }
    assert bio.immune_trigger({}, hpo, {})[0].startswith("1 HPO phenotype(s) under HP:0002715")
    assert bio.immune_trigger({}, {}, _MOUSE) == [
        "1 mouse phenotype(s) under MP:0005387 (immune system)"
    ]
    none = bio.immune_trigger(
        {"RNA blood cell specificity": "Not detected in immune cells"}, {}, {}
    )
    assert none == []
    line = bio.immune_md(
        {"RNA blood cell specificity": "Low immune cell specificity"}, {}, {}, set()
    )
    assert line.startswith('*No immune trigger: HPA blood cells "Low immune cell specificity"')


def test_immune_block_rows_when_triggered() -> None:
    hpa = {
        "RNA blood cell specificity": "Immune cell enriched",
        "RNA blood lineage specific nTPM": {"T-cells": "88.1"},
    }
    md = bio.immune_md(hpa, {}, _MOUSE, {"hpa", "ot_mouse"})
    assert "| Blood lineages and cells (HPA RNA, nTPM) | T-cells 88.1 | [@hpa] |" in md
    assert "Mouse immune-system phenotypes (MGI/IMPC) | 1: increased T cell number" in md


def test_models_table_shows_knockouts_only_for_a_curated_ortholog() -> None:
    orth = _orth(_NCBI, _ONE2ONE)
    md = bio.models_md(
        orth,
        _MOUSE,
        {"viability": "viable"},
        {},
        {"reported_variants": []},
        {"orthologs", "ot_mouse"},
    )
    assert "| Knockout: cardiovascular system phenotype | 1 phenotype(s): abnormal vascular" in md
    assert "| IMPC viability | viable |" in md
    assert "Human: loss-of-function burden | not checked (no Genebass record in this run)" in md
    distant = bio.models_md(_orth([], _MANY), _MOUSE, {}, {}, {}, set())
    assert "Not shown: the mouse genes are distant homologues" in distant
    assert "cardiovascular" not in distant
    absent = bio.models_md(_orth([], []), {}, {}, {}, {}, set())
    assert "No mouse ortholog found, so no knockout to read" in absent


def test_partners_keep_channels_apart() -> None:
    string = {"min_combined_score": 0.7, "partners": [
        {"partner": "P1", "combined_score": 0.97, "experimental": 0.5, "database": 0.9, "textmining": 0.4},
        {"partner": "P2", "combined_score": 0.9, "experimental": 0.0, "database": 0.0, "textmining": 0.9},
        {"partner": "P3", "combined_score": 0.8, "experimental": 0.8, "database": 0.0, "textmining": 0.1},
    ]}  # fmt: skip
    md = bio.partners_md(string, {"string"})
    assert (
        "| P1 | 0.97 | 0.50 | 0.90 | 0.40 | experiments and curated databases | [@string] |" in md
    )
    assert "| P2 |" in md and "associated only (text mining, co-expression)" in md
    assert "| experiments | [@string] |" in md
    assert bio.partners_md({"partners": []}, set()) == ""


def test_reagents_rows_and_their_empty_states() -> None:
    hpa = {
        "Antibody": ["HPA001671", "CAB003859"],
        "Reliability (IF)": "Enhanced",
        "RNA cell line specificity": "Low cancer specificity",
    }
    chembl = {
        "num_bioactivities": 761,
        "num_potent": 127,
        "num_highly_potent": 30,
        "num_quantitative": 276,
        "num_mechanisms": 0,
    }
    md = bio.reagents_md(hpa, _MOUSE, _orth(_NCBI, []), chembl, {"hpa", "chembl", "ot_mouse"})
    assert (
        "| Antibodies (HPA) | 2 validated by HPA: HPA001671, CAB003859; reliability (IF) Enhanced |"
        in md
    )
    assert "127 measurements ≤ 100 nM" in md
    assert "Mouse alleles (MGI/IMPC) | 1 phenotyped allele compositions" in md
    empty = bio.reagents_md({}, {}, _orth([], []), {}, set())
    assert "No HPA antibody listed" in empty and "No ChEMBL bioactivity" in empty
    assert "Mouse alleles" not in empty  # no curated ortholog
    noncoding = bio.reagents_md({}, {}, _orth(_NCBI_NC, []), {}, set(), coding=False)
    assert "Antibodies" not in noncoding and "Tool compounds" not in noncoding


def test_noncoding_gene_protein_blocks_render_one_line(tmp_path: Path) -> None:
    _raw(
        tmp_path,
        "hgnc",
        {"symbol": "LNC1", "locus_group": "non-coding RNA", "locus_type": "RNA, long non-coding"},
    )
    out, notes = blocks.expand(
        "{{table:structures}}\n{{figure:protein_map}}", tmp_path, "LNC1", {"hgnc"}
    )
    assert (
        out.splitlines()
        == ["*Not a protein-coding gene (HGNC locus type: RNA, long non-coding).* [@hgnc]"] * 2
    )
    assert bio.protein_coding({}) and not bio.protein_coding({"locus_group": "non-coding RNA"})


def test_tissue_evidence_shows_hpa_blood_wording() -> None:
    md = tables.tissue_evidence_md(
        {"RNA blood cell specificity": "Not detected in immune cells"}, {}, {"hpa"}
    )
    assert "| RNA, blood and immune cells (HPA) | Not detected in immune cells |" in md
