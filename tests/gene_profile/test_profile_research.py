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


def test_old_template_runs_keep_their_sections_new_runs_use_the_current_one() -> None:
    assert ds.Manifest(query="G", gene="G", generated_at="x").template == 1
    v1 = [k for k, _ in ds.draft_sections(1)]
    v2 = [k for k, _ in ds.draft_sections(2)]
    assert "literature" in v1 and "druggability" in v1
    assert v2[-3:] == ["status", "translational", "gaps"] and "field" in v2
    assert ds.TEMPLATE_VERSION == 3
    assert ds.draft_sections(3) == ds.draft_sections(2)


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
    (tmp_path / writer.MECHANISM_FILE).write_text(json.dumps(_STEPS))
    (tmp_path / writer.OPEN_FILE).write_text(
        json.dumps(
            [
                {
                    "question": "Does it act in vivo?",
                    "evidence": ["pmid:11"],
                    "would_settle": "a knockout",
                }
            ]
        )
    )
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


def _v3_run(tmp_path: Path, depth: str = "standard") -> Path:
    run = _v2_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text())
    (run / "manifest.json").write_text(json.dumps({**manifest, "template": 3, "depth": depth}))
    (run / writer.HOW_FILE).write_text(json.dumps([{"claim": "c", "supports": ["gnomad_lof"]}]))
    return run


_V3_SNAPSHOT = "{{table:how_we_know}}\n\nFact [@gnomad_lof]."


def _v3_draft() -> str:
    return _v2_draft(snapshot=_V3_SNAPSHOT) + "\n{{table:drug_landscape}}\n"


def test_template_3_requires_the_claim_checks_and_the_drug_table(tmp_path: Path) -> None:
    run = _v3_run(tmp_path)
    codes = {i.code for i in check_profile.check(run, _v3_draft()).errors}
    assert "E_CLAIM_CHECK" in codes
    missing = check_profile.check(run, _v2_draft(snapshot=_V3_SNAPSHOT))
    assert any("drug_landscape" in i.message for i in missing.errors)


def test_quick_runs_and_older_templates_skip_the_claim_checks(tmp_path: Path) -> None:
    quick = _v3_run(tmp_path, "quick")
    assert "E_CLAIM_CHECK" not in {i.code for i in check_profile.check(quick, _v3_draft()).errors}


def test_a_paper_block_needs_an_audit_in_template_3(tmp_path: Path) -> None:
    run = _v3_run(tmp_path)
    draft = _v3_draft().replace(
        "Fact [@gnomad_lof].", "Fact [@gnomad_lof].\n\nPaper [@pmid:13].", 1
    )
    codes = {i.code for i in check_profile.check(run, draft).errors}
    assert "E_CLAIM_AUDIT" in codes


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


# ------------------------------------------------------------ genetics: harmonisation

import profile_figures as figs  # noqa: E402
import profile_variants as variants  # noqa: E402


def _hit(
    allele: str | None, direction: str = "decrease", beta: float = 0.43, **kw: Any
) -> dict[str, Any]:
    return {
        "rs_id": "rs1", "effect_allele": allele, "beta_num": beta, "beta_unit": "unit",
        "beta_direction": direction, "ci_lower": 0.42, "ci_upper": 0.44, "trait": "LDL",
        "efo_id": "EFO_1", "study_accession": "GCST1", "pvalue_mantissa": 3, "pvalue_exponent": -50, **kw,
    }  # fmt: skip


def test_effect_allele_equal_to_alt_keeps_direction_per_function() -> None:
    a = variants.harmonise_association(_hit("T"), "G", ["T"], "loss")
    assert a["harmonised"] == "alt" and a["direction"] == "decrease"
    assert a["per"] == "per loss-of-function allele T"


def test_effect_allele_equal_to_ref_flips_direction_and_odds() -> None:
    a = variants.harmonise_association(_hit("G"), "G", ["T"], "")
    assert a["harmonised"] == "ref (flipped)" and a["direction"] == "increase"
    assert a["per"] == "per alt allele T (function of the allele not typed)"
    o = variants.harmonise_association(
        _hit("G", direction=None, beta=None, or_per_copy=2.0, ci_lower=1.6, ci_upper=2.5),
        "G",
        ["T"],
        "gain",
    )
    assert o["odds_ratio"] == 0.5 and o["ci"] == [0.4, 0.625] and o["direction"] == "decrease"


def test_unknown_effect_allele_is_kept_unharmonised() -> None:
    a = variants.harmonise_association(_hit(None), "G", ["T"], "loss")
    assert a["harmonised"] == "unknown" and a["per"] == "effect allele not recorded"
    b = variants.harmonise_association(_hit("A"), "G", ["T"], "loss")
    assert b["per"] == "per effect allele A (not matched to the variant's alleles)"


def test_multi_allelic_rs_id_matches_the_allele_it_names() -> None:
    a = variants.harmonise_association(_hit("C"), "G", ["T", "C"], "")
    assert a["harmonised"] == "alt" and "T/C" in a["per"]


_ROW = {"change": "R46L", "rsid": "rs1", "position": 46, "consequence": "missense_variant",
        "effect": "loss (curated)", "disease_variant": False, "alleles": [{"id": "1-100-G-T"}]}  # fmt: skip
_FREQ = {
    "by_variant": {"R46L": ["1-100-G-T"]},
    "alleles": {"1-100-G-T": {"variant_id": "1-100-G-T", "found": True, "ref": "G", "alt": "T",
                              "af": 0.012, "homozygote_count": 7, "populations": {
                                  "nfe": {"af": 0.016, "an": 100000}, "eas": {"af": 0.0, "an": 30000},
                                  "mid": {"af": 0.5, "an": 100}}}},
}  # fmt: skip


def test_harmonise_joins_alleles_frequencies_and_associations() -> None:
    [r] = variants.harmonise([_ROW], [_hit("T"), _hit(None)], _FREQ)
    assert (r["ref"], r["alts"], r["function"]) == ("G", ["T"], "loss")
    assert [a["harmonised"] for a in r["associations"]] == ["alt", "unknown"]
    assert variants.coverage([r]) == {
        "variants": 1, "typed": 1, "associations": 2, "allele_known": 1, "harmonised": 1,
        "with_ci": 2, "in_gnomad": 1,
    }  # fmt: skip


def test_paper_typed_function_is_used_and_labelled() -> None:
    row = {**_ROW, "effect": ""}
    [r] = variants.harmonise([row], [], _FREQ, {"R46L": "gain"})
    assert (r["function"], r["function_source"]) == ("gain", "paper")


def test_variant_effects_table_and_absent_from_gnomad() -> None:
    records = variants.harmonise([_ROW], [_hit("T")], _FREQ)
    md = variants.variant_effects_md(records, {"allelic_series", "gwas", "variant_frequencies"})
    row = md.splitlines()[2]
    assert row.startswith("| R46L (rs1) | G>T | loss |")
    assert "LDL: β 0.43 unit decrease [0.42–0.44], p 3e-50; per loss-of-function allele T" in row
    assert "0.012 (eas 0.0e+00 – nfe 0.016)" in row  # ancestries below AN 2,000 left out
    assert "| 7 |" in row
    assert "Effect allele known for 1 of 1 GWAS associations" in md
    absent = {"by_variant": {"R46L": ["1-100-G-T"]}, "alleles": {"1-100-G-T": {"found": False}}}
    assert variants.frequency_text(variants.harmonise([_ROW], [], absent)[0]["alleles"]) == (
        "Not in gnomAD",
        "—",
    )


def test_trait_groups_from_efo_areas_and_shared_parents() -> None:
    ontology = {
        "EFO_A": {"areas": ["measurement"], "parents": ["lipid amount", "biomarker measurement"]},
        "EFO_B": {"areas": ["measurement"], "parents": ["lipid amount"]},
        "MONDO_C": {"areas": ["phenotype", "cardiovascular disease"], "parents": []},
    }
    g = variants.trait_groups(["EFO_A", "EFO_B", "MONDO_C", "EFO_X"], ontology)
    assert g == {
        "EFO_A": "measurement: lipid amount",  # the parent the gene's traits share
        "EFO_B": "measurement: lipid amount",
        "MONDO_C": "cardiovascular disease",
        "EFO_X": "other traits",
    }


def test_phenome_points_mark_position_only_hits() -> None:
    support = {"associations": {"GCST1|rs1": {"supported": True}}, "therapeutic_areas": {}}
    pts = variants.phenome_points([_hit("T"), {**_hit(None), "study_accession": "GCST2"}], support)
    assert [(p["supported"], p["direction"]) for p in pts] == [(True, "decrease"), (False, None)]
    assert pts[0]["mlog10p"] == pytest.approx(50 - 0.4771, abs=1e-3)


def test_new_figures_draw_and_skip_when_empty(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pts = [{"group": "g1", "trait": "t", "mlog10p": 50.0, "direction": "decrease", "supported": True},
           {"group": "g2", "trait": "u", "mlog10p": 9.0, "direction": None, "supported": False}]  # fmt: skip
    assert figs.phenome(tmp_path / "p.svg", pts) is not None
    assert figs.phenome(tmp_path / "q.svg", []) is None
    spec = [{"af": 0.01, "effect": 0.4, "direction": "decrease", "function": "loss", "label": "R46L"},
            {"af": 1e-5, "effect": None, "direction": None, "function": "gain", "label": "D374Y"}]  # fmt: skip
    assert figs.allelic_spectrum(tmp_path / "s.svg", spec) is not None
    assert figs.allelic_spectrum(tmp_path / "t.svg", []) is None
    drawn = figs.protein_map(
        tmp_path / "m.svg", length=100, features=[], missense=[46], truncating=[80],
        uniprot_disease=[], lof=[], plddt=[], pdb=[], labels={46: "R46L"},
        effects={46: "loss"}, somatic={10: 12, 46: 3}, somatic_labels={10: "E10"},
    )  # fmt: skip
    assert drawn is not None and "effect: loss / truncating" in drawn.read_text()


# ------------------------------------------------------------- somatic pattern

import profile_somatic as somatic  # noqa: E402


def _bundle(
    mutations: list[tuple[str, int | None]], seq: int = 1000, amp: int = 0, dele: int = 0
) -> dict[str, Any]:
    """One study: (mutation type, position) per mutated sample."""
    return {"studies": [{
        "study_id": "x_tcga_pan_can_atlas_2018", "name": "Cancer X (TCGA, PanCancer Atlas)",
        "cancer_type": "x", "sequenced_samples": seq, "cna_samples": seq,
        "mutations": [{"sample_id": f"S{i}", "protein_change": f"E{p}K" if p else "", "position": p,
                       "mutation_type": t} for i, (t, p) in enumerate(mutations)],
        "amplified_samples": [f"A{i}" for i in range(amp)],
        "deep_deleted_samples": [f"D{i}" for i in range(dele)],
    }]}  # fmt: skip


def test_binomial_tail() -> None:
    assert somatic.binomial_sf(0, 10, 0.1) == 1.0
    assert somatic.binomial_sf(11, 10, 0.1) == 0.0
    assert somatic.binomial_sf(1, 10, 0.1) == pytest.approx(1 - 0.9**10)
    assert somatic.binomial_sf(9, 10, 0.5) == pytest.approx(11 / 1024)


def test_recurrence_bar_scales_with_protein_length() -> None:
    hits = [100] * 5 + list(range(1, 46))  # 5 at one residue among 50 missense
    assert 100 in somatic.recurrent_residues(hits, 500)
    assert somatic.recurrent_residues(hits, 30000) == {100: 5}  # still improbable…
    assert somatic.recurrent_residues([100] * 2 + list(range(1, 3000)), 3000) == {}  # …2 is not


def test_oncogene_pattern() -> None:
    s = somatic.somatic_pattern(
        _bundle(
            [("Missense_Mutation", 545)] * 30 + [("Missense_Mutation", p) for p in range(1, 21)],
            amp=50,
        ),
        1000,
    )
    assert s["label"] == "oncogene-like" and s["recurrent"] == {"E545": 30}
    assert s["hotspot_share"] == pytest.approx(0.6) and s["amplified_share"] == pytest.approx(0.05)


def test_tumour_suppressor_pattern() -> None:
    muts = [("Nonsense_Mutation", p) for p in range(10, 40)] + [
        ("Missense_Mutation", p) for p in range(50, 70)
    ]
    s = somatic.somatic_pattern(_bundle(muts, dele=27), 400)
    assert s["label"] == "tumour-suppressor-like" and s["truncating_share"] == pytest.approx(0.6)


def test_long_gene_passenger_pattern_is_not_a_driver() -> None:
    muts = [("Missense_Mutation", p) for p in range(1, 30000, 100)]  # 300 tumours of 1000
    s = somatic.somatic_pattern(_bundle(muts), 34000)
    assert s["mutated_share"] == pytest.approx(0.3)
    assert s["label"] == "no driver pattern" and s["recurrent"] == {}


def test_too_few_and_no_events() -> None:
    s = somatic.somatic_pattern(_bundle([("Nonsense_Mutation", 5)] * 10), 300)
    assert s["label"] == "too few mutations to classify"
    md = somatic.somatic_md(_bundle([]), 300, {"somatic"})
    assert (
        md
        == "*No mutation or copy-number event in the 1 TCGA PanCancer Atlas studies, 1,000 sequenced tumours.* [@somatic]"
    )


def test_somatic_table_and_failed_study() -> None:
    data = _bundle(
        [("Missense_Mutation", 545)] * 30 + [("Missense_Mutation", p) for p in range(1, 21)]
    )
    data["studies"].append({"study_id": "y_tcga_pan_can_atlas_2018", "error": "CNA HTTP 500"})
    md = somatic.somatic_md(data, 1000, {"somatic"})
    assert "| Pattern (20/20 rule) | oncogene-like | [@somatic] |" in md
    assert "| Tumours mutated | 50 of 1,000 (5.0%) |" in md
    assert "not checked: y_tcga_pan_can_atlas_2018" in md
    assert "not a curated driver call" in md
    assert somatic.residue_counts(data) == {545: 30, **{p: 1 for p in range(1, 21)}}


# --------------------------------------------------------- effect sizes in abstracts

import profile_effects as effects  # noqa: E402

_V1 = {"change": "R46L", "rsid": "rs11", "hgvsc": []}
_V2 = {"change": "E670G", "rsid": "rs22", "hgvsc": []}
_IDS = {"111"}


def _doc_with(
    pmid: str, text: str, tags: list[tuple[str, str, dict[str, str]]], date: str = "2015-01-01"
) -> PubTatorDocument:
    """One abstract; ``tags`` are (type, exact text, extra fields) annotated at their first
    occurrence."""
    anns = []
    for kind, token, extra in tags:
        start = text.index(token)
        anns.append(
            PubTatorAnnotation(type=kind, text=token, offset=start, length=len(token), **extra)
        )
    return PubTatorDocument(
        pmid=pmid,
        date=date,
        passages=[PubTatorPassage(kind="abstract", offset=0, text=text, annotations=anns)],
    )


def _var(token: str, rsid: str) -> tuple[str, str, dict[str, str]]:
    return (
        "Variant",
        token,
        {"gene_id": "111", "rsid": rsid, "identifier": f"RS#:{rsid[2:]};CorrespondingGene:111"},
    )


def _dis(token: str, mesh: str) -> tuple[str, str, dict[str, str]]:
    return ("Disease", token, {"identifier": f"MESH:{mesh}", "name": token})


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("OR = 0.51 (95% CI 0.42-0.62; P = 3e-8)", (0.51, [0.42, 0.62], 3e-8, True, False)),
        ("hazard ratio, 1.20; 95% CI, 0.98 to 1.47", (1.2, [0.98, 1.47], None, False, False)),
        ("ORallelic 1.30 (1.10-1.50)", (1.3, [1.1, 1.5], None, True, True)),  # per-allele form
        ("OR (95% CI): 1.5 (1.2–1.9)", (1.5, [1.2, 1.9], None, True, False)),  # bracket form
        ("OR 2.5 (95% CI 1.1-1.9)", (2.5, [1.1, 1.9], None, None, False)),  # CI misses estimate
        ("RR 0.8, p < 0.001", (0.8, None, 0.001, True, False)),
        ("OR 1.1, P = 2.1 × 10−5", (1.1, None, 2.1e-5, True, False)),
    ],
)
def test_estimate_patterns(sentence: str, expected: tuple[Any, ...]) -> None:
    [e] = effects.parse_estimates(sentence)
    value, ci, p, significant, per_allele = expected
    assert e["value"] == value and e["ci"] == ci and e["significant"] is significant
    assert (e["p"] == pytest.approx(p)) if p is not None else e["p"] is None
    assert e["per_allele"] is per_allele
    assert e["flagged"] is (sentence.startswith("OR 2.5"))


def test_two_variants_in_one_sentence_each_own_their_estimate() -> None:
    text = "The R46L variant lowered risk of coronary disease (OR 0.50, 95% CI 0.40-0.60) whereas E670G raised it (OR 1.40, 95% CI 1.10-1.70)."
    doc = _doc_with(
        "1", text, [_var("R46L", "rs11"), _var("E670G", "rs22"), _dis("coronary disease", "D1")]
    )
    out = effects.extract([doc], [_V1, _V2], _IDS)
    got = sorted((e["variant"], e["value"]) for e in out["estimates"])
    assert got == [("E670G", 1.4), ("R46L", 0.5)]
    assert {e["rule"] for e in out["estimates"]} == {"rs id tag"}
    assert out["estimates"][0]["outcome"] == {"id": "MESH:D1", "name": "coronary disease"}


def test_respectively_maps_in_order() -> None:
    text = (
        "R46L and E670G were associated with ORs of 0.6 (0.5-0.7) and 1.3 (1.1-1.5), respectively."
    )
    doc = _doc_with("2", text, [_var("R46L", "rs11"), _var("E670G", "rs22")])
    found = effects.parse_estimates(text)
    assert len(found) == 1  # "ORs of 0.6"; the second number has no measure word of its own
    text = (
        "R46L and E670G were associated with OR 0.6 (0.5-0.7) and OR 1.3 (1.1-1.5), respectively."
    )
    doc = _doc_with("2", text, [_var("R46L", "rs11"), _var("E670G", "rs22")])
    out = effects.extract([doc], [_V1, _V2], _IDS)
    assert sorted((e["variant"], e["value"]) for e in out["estimates"]) == [
        ("E670G", 1.3),
        ("R46L", 0.6),
    ]


def test_stratum_mention_is_skipped() -> None:
    text = "E670G increased risk in carriers of R46L (OR 1.8, 95% CI 1.2-2.6)."
    doc = _doc_with("3", text, [_var("E670G", "rs22"), _var("R46L", "rs11")])
    out = effects.extract([doc], [_V1, _V2], _IDS)
    assert [(e["variant"], e["value"]) for e in out["estimates"]] == [("E670G", 1.8)]


def test_combined_score_is_not_the_variant() -> None:
    text = "R46L together with other loci formed a genetic risk score (OR 2.1, 95% CI 1.5-2.9)."
    doc = _doc_with("4", text, [_var("R46L", "rs11")])
    out = effects.extract([doc], [_V1], _IDS)
    assert out["estimates"] == [] and out["excluded"] == {"a combined score or haplotype": 1}


def test_interval_without_its_estimate_is_flagged_and_unused() -> None:
    text = "R46L was associated with stroke (OR 2.5, 95% CI 1.1-1.9)."
    doc = _doc_with("5", text, [_var("R46L", "rs11"), _dis("stroke", "D2")])
    out = effects.extract([doc], [_V1], _IDS)
    assert out["estimates"][0]["flagged"] is True
    assert effects.stated_summary(out) == {}


def test_subgroup_disease_is_not_the_outcome() -> None:
    text = "In patients with diabetes, R46L was associated with lower risk of myocardial infarction (OR 0.7, 95% CI 0.6-0.8)."
    doc = _doc_with(
        "6",
        text,
        [_dis("diabetes", "D3"), _var("R46L", "rs11"), _dis("myocardial infarction", "D4")],
    )
    [e] = effects.extract([doc], [_V1], _IDS)["estimates"]
    assert e["outcome"]["id"] == "MESH:D4"


def test_untagged_spelling_from_the_record_and_another_gene_owner() -> None:
    text = "Carriers of Arg46Leu had lower LDL. OtherGene was associated with gout (OR 1.9, 95% CI 1.2-3.0) in Arg46Leu carriers."
    doc = _doc_with("7", text, [("Gene", "OtherGene", {"identifier": "999"})])
    out = effects.extract([doc], [_V1], _IDS)
    assert out["estimates"] == []  # the estimate follows another gene, not the variant
    assert out["excluded"] == {"another variant or gene owns it": 1}


def test_tally_and_forest_need_three_papers() -> None:
    est = [
        {"variant": "R46L", "pmid": str(i), "outcome": {"id": "MESH:D1", "name": "CAD"}, "value": 0.6,
         "ci": [0.5, 0.7], "p": None, "significant": True, "flagged": False, "meta_analysis": i == 2}
        for i in range(3)
    ] + [{"variant": "R46L", "pmid": "9", "outcome": None, "value": 1.1, "ci": None, "p": None,
          "significant": None, "flagged": False, "meta_analysis": False}]  # fmt: skip
    tally = effects.outcome_tally(est, "R46L")
    assert tally[0] == {
        "outcome": "CAD",
        "id": "MESH:D1",
        "papers": 3,
        "up": 0,
        "down": 3,
        "ns": 0,
        "unknown": 0,
        "meta": 1,
    }
    assert tally[1]["outcome"] == "outcome not tagged"
    assert len(effects.forest_rows(est, "R46L")) == 3
    assert effects.forest_rows(est[:2], "R46L") == []
    md = effects.outcomes_md({"estimates": est, "excluded": {}, "papers_read": 4}, {"pmid:0"})
    assert (
        "**R46L**: 4 estimate(s) in 4 paper(s)." in md
        and "| CAD | 3 | 0 | 3 | 0 | 0 | 1 | [@pmid:0] |" in md
    )


def test_variant_outcomes_block_tally_alone_and_empty(tmp_path: Path) -> None:
    est = [{"variant": "R46L", "pmid": "1", "outcome": {"id": "MESH:D1", "name": "CAD"}, "value": 0.6,
            "ci": [0.5, 0.7], "p": None, "significant": True, "flagged": False, "meta_analysis": False}]  # fmt: skip
    _raw(tmp_path, "stated_effects", {"estimates": est, "excluded": {}, "papers_read": 1})
    out, notes = blocks.expand(
        "{{figure:variant_outcomes}}", tmp_path, "G", {"stated_effects", "pmid:1"}
    )
    assert "| CAD | 1 | 0 | 1 | 0 | 0 | 0 | [@pmid:1] |" in out  # tally, no forest (one paper)
    assert out.rstrip().endswith("*From records: stated_effects.*") and not notes
    empty = tmp_path / "e"
    _raw(empty, "stated_effects", {"estimates": [], "excluded": {}}, status="empty")
    out, _ = blocks.expand("{{figure:variant_outcomes}}", empty, "G", {"stated_effects"})
    assert out == "*No estimates stated in the abstracts for the key variants.* [@stated_effects]"


# ---------------------------------------------- view of the field: writer files and records

import profile_field as field  # noqa: E402


def test_mechanism_problems() -> None:
    sources = {"pmid:1": object()}
    vocab = {"Cytoplasm"}
    good = [
        {"actor": "a", "action": "b", "target": "c", "compartment": "cytoplasm", "keys": ["pmid:1"]}
    ] * 4
    assert writer.mechanism_problems(good, sources, vocab) == []
    bad = [
        *good[:3],
        {
            "actor": "a",
            "action": "b",
            "target": "c",
            "compartment": "Nucleolus organiser",
            "keys": [],
        },
    ]
    assert writer.mechanism_problems(bad, sources, vocab) == [
        "step 4: not cited (no keys)",
        "step 4: compartment 'Nucleolus organiser' is not a UniProt subcellular location",
    ]
    assert writer.mechanism_problems(good[:2], sources, vocab) == ["2 steps; write 4–8"]


def test_step_strength_from_the_cited_records() -> None:
    assert (
        writer.step_strength({"keys": ["pmid:1", "pmid:2"]}, _STRENGTHS) == "solid"
    )  # 2 human groups
    assert writer.step_strength({"keys": ["pmid:4"]}, _STRENGTHS) == "dashed"  # cells only
    assert writer.step_strength({"keys": ["pmid:6"]}, _STRENGTHS) == "dotted"  # a review
    assert writer.step_strength({"keys": ["gnomad_lof"]}, _STRENGTHS) == "solid"


def test_open_questions_table_and_problems() -> None:
    entries = [
        {
            "question": "Q?",
            "evidence": ["pmid:1"],
            "would_settle": "a trial",
            "data": ["gwas"],
            "active": [],
        }
    ]
    md, keys = writer.open_questions_md(entries, {"pmid:1", "gwas"})
    assert "| Q? | [@pmid:1] | *a trial* | [@gwas] | none found |" in md and keys == [
        "pmid:1",
        "gwas",
    ]
    assert writer.open_problems([{"question": "Q?"}], {}) == [
        "question 1: no evidence key (what leaves it open)"
    ]


def test_template_2_mechanism_and_open_questions_render_and_check(tmp_path: Path) -> None:
    run = _v2_run(tmp_path)
    (run / writer.HOW_FILE).write_text(
        json.dumps([{"claim": "c", "supports": ["pmid:13", "gnomad_lof"]}])
    )
    draft = _v2_draft(snapshot="{{table:how_we_know}}\n\nFact [@gnomad_lof].")
    assert check_profile.check(run, draft).ok
    (run / writer.OPEN_FILE).unlink()
    assert [i.code for i in check_profile.check(run, draft).errors] == ["E_OPEN_QUESTION"]
    pytest.importorskip("matplotlib")
    out, _ = blocks.expand("{{figure:mechanism}}", run, "G", set(ds.load_sources(run)))
    assert (
        "| 1 | A | binds | B | Cell membrane | — | human or in vivo, ≥ 2 groups, or records |"
        in out
    )
    assert "| 2 | B | signals to | C | Cytoplasm | — | one group or cells only |" in out
    assert "Chosen by the LLM from: pmid:13, gnomad_lof, pmid:11, pmid:12." in out


_FIELD = {
    "papers": {
        "1": {"year": 2024, "indexed": True, "last_author": "Lab A", "mesh": [["Hypercholesterolemia", "D1", True], ["Patch-Clamp Techniques", "D9", False]]},
        "2": {"year": 2023, "indexed": True, "last_author": "Lab A", "mesh": [["Hypercholesterolemia", "D1", True]]},
        "3": {"year": 2005, "indexed": True, "last_author": "Lab B", "mesh": [["Neoplasms", "D2", True], ["Patch-Clamp Techniques", "D9", False]]},
        "4": {"year": 2025, "indexed": False, "last_author": "Lab C", "mesh": [], "pubtator": ["stroke"]},
    },
    "mesh_trees": {"D1": ["C18.452.648"], "D2": ["C04"], "D9": ["E05.200"]},
    "branch_labels": {"C18": "Nutritional and Metabolic Diseases", "C04": "Neoplasms"},
}  # fmt: skip


def test_field_map_by_branch_and_year_with_unindexed_apart() -> None:
    fm = field.field_map(_FIELD, 2026)
    assert [(r["branch"], r["papers"]) for r in fm["rows"]] == [("C18", 2), ("C04", 1)]
    assert fm["rows"][0]["recent_share"] == 1.0 and fm["rows"][1]["recent_share"] == 0.0
    assert (fm["indexed"], fm["unindexed"], fm["unindexed_tags"]) == (3, 1, [("stroke", 1)])
    md = field.field_md(fm, {"field_papers"})
    assert (
        "| Nutritional and Metabolic Diseases (C18) | 2 | 100% | rising | [@field_papers] |" in md
    )
    assert "| Neoplasms (C04) | 1 | 0% | quiet |" in md


def test_groups_and_methods() -> None:
    assert field.groups(_FIELD, 2026)[0] == {
        "name": "Lab A",
        "papers": 2,
        "latest": 2024,
        "pmids": ["1", "2"],
    }
    md = field.groups_md(_FIELD, 2026, {"pmid:1"})
    assert "| Lab A | 2 | 2024 | [@pmid:1] |" in md and "Lab C" not in md  # one paper: not listed
    assert field.methods(_FIELD, 2026) == [
        {"method": "Patch-Clamp Techniques", "papers": 2, "recent": 1}
    ]
    assert field.groups_md({"papers": {}}, 2026, set()) == ""


def test_public_data_rows_and_empty_query() -> None:
    geo = {
        "query": "q",
        "total": 62,
        "series": [
            {
                "accession": "GSE1",
                "organism": "Mus musculus",
                "samples": 6,
                "title": "Knockout liver",
            }
        ],
    }
    gwas = {
        "hits": [{"study_accession": "GCST1", "trait": "LDL", "summary_statistics": "http://ftp"}]
    }
    md = field.public_data_md(geo, gwas, {}, {"geo_perturbation", "gwas"}, "https://geo/q")
    assert (
        "| GEO series matching the [query](https://geo/q) | 62 series; newest: GSE1 (Mus musculus, 6 samples)"
        in md
    )
    assert "1 of the associated studies: GCST1 (LDL)" in md
    empty = field.public_data_md({"query": "q", "total": 0, "series": []}, {}, {}, set())
    assert "No GEO series found by this query" in empty
    assert "None of the associated studies has full summary statistics" in empty


def test_field_and_mechanism_figures_draw(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    fm = field.field_map(_FIELD, 2026)
    assert figs.field_map(tmp_path / "f.svg", fm["rows"], fm["years"]) is not None
    assert figs.field_map(tmp_path / "g.svg", [], []) is None
    steps = [
        {**s, "strength": st}
        for s, st in zip(_STEPS, ["solid", "dashed", "dotted", "solid"], strict=True)
    ]
    assert figs.mechanism(tmp_path / "m.svg", steps) is not None
    assert figs.forest(tmp_path / "x.svg", [], "t") is None


# ------------------------------------------------------------------ generality

import io  # noqa: E402
import re  # noqa: E402
import tokenize  # noqa: E402

# Genes of the eval class set: no script may name one.
_EVAL_DIR = _SCRIPTS.parent / "evals"


def _eval_genes() -> set[str]:
    spec = json.loads((_EVAL_DIR / "evals.json").read_text())
    return {c["gene"] for c in spec["evals"] if c.get("gene")} | {
        "PCSK9", "TRPC6", "C1orf87", "SEPTIN9", "IL7R", "HNF1A", "APOL1", "MALAT1", "PIK3CA",
        "PTEN", "TTN", "LPA", "MET", "CETP", "XIST",
    }  # fmt: skip


def _code_tokens(source: str) -> list[tuple[int, str]]:
    """NAME and STRING tokens that are code: comments and docstrings (a string standing
    alone as a statement) are skipped."""
    out: list[tuple[int, str]] = []
    prev = tokenize.NEWLINE
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.STRING and prev in (
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            prev = tok.type
            continue  # a docstring or a bare string statement
        if tok.type in (tokenize.NAME, tokenize.STRING):
            out.append((tok.start[0], tok.string))
        if tok.type not in (tokenize.COMMENT, tokenize.NL):
            prev = tok.type
    return out


def test_no_script_names_an_rs_id_or_an_eval_gene() -> None:
    genes = _eval_genes()
    word = re.compile(
        r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, sorted(genes))) + r")(?![A-Za-z0-9_])"
    )
    found = []
    for path in sorted(_SCRIPTS.glob("*.py")):
        for line, text in _code_tokens(path.read_text()):
            if re.search(r"(?<![A-Za-z])rs\d{3,}", text) or word.search(text):
                found.append(f"{path.name}:{line}: {text[:60]}")
    assert found == []


def test_the_scanner_skips_docstrings_and_comments_but_not_code() -> None:
    src = 'def f():\n    """PCSK9 example."""\n    # rs11591147 here\n    return "rs11591147"\n'
    assert [t for _, t in _code_tokens(src) if "rs" in t] == ['"rs11591147"']


def test_lead_association_prefers_an_estimate_over_a_bare_p() -> None:
    bare = variants.harmonise_association(
        {**_hit(None, beta=None, direction=None), "pvalue_exponent": -900}, "G", ["T"], ""
    )
    good = variants.harmonise_association(_hit("T"), "G", ["T"], "")
    assert variants.lead_association([bare, good]) is good
    assert variants.lead_association([bare]) is bare
    assert variants.lead_association([]) is None


def test_an_rs_id_shared_by_two_series_alleles_selects_by_spelling_only() -> None:
    a, b = (
        {"change": "D374Y", "rsid": "rs9", "hgvsc": []},
        {"change": "D374H", "rsid": "rs9", "hgvsc": []},
    )
    text = "Carriers of D374Y had premature coronary disease (OR 3.1, 95% CI 1.5-6.4)."
    doc = _doc_with("8", text, [_var("D374Y", "rs9"), _dis("coronary disease", "D1")])
    out = effects.extract([doc], [a, b], _IDS)
    assert [(e["variant"], e["rule"]) for e in out["estimates"]] == [
        ("D374Y", "alleles as recorded")
    ]


# Real sentences (2026-11-01 spot check of a gather-only run), with what must be parsed.
@pytest.mark.parametrize(
    ("sentence", "values"),
    [
        # lower-case "or" and a genotype "RR" are not ratios
        ("3 (8.8%) were heterozygous for mutations R46L or N157K in the PCSK9 gene.", []),
        (
            "total cholesterol [5.74 (1.01) mmol/l for RR compared with 5.26+/-1.03 mmol/l for RL; P=0.001]",
            [],
        ),
        # "95% confidence interval [CI]" keeps its interval
        (
            "in the CCHS study (hazard ratio [HR]: 0.94; 95% confidence interval [CI]: 0.68 to 1.31), 46% in the CGPS study (odds ratio [OR]: 0.54; 95% CI: 0.39 to 0.77)",
            [(0.94, [0.68, 1.31]), (0.54, [0.39, 0.77])],
        ),
        (
            "an 88 percent reduction in the risk of CHD (P=0.008 for the reduction; hazard ratio, 0.11; 95 percent confidence interval, 0.02 to 0.81; P=0.03).",
            [(0.11, None)],
        ),
        (
            "had an age- and sex-adjusted odds ratio of 0.64 (95% confidence interval, 0.44-0.95) for aortic valve stenosis",
            [(0.64, [0.44, 0.95])],
        ),
        # an immune-signalling gene: per-allele OR, then a genotype OR in one bracket
        (
            "[per allele odds ratio (OR) 1.32, 95% CI 1.1-1.6, P=0.0031; per CC genotype vs TT + TC genotypes, OR 1.5, 95% CI 1.18-1.87, P=0.0007].",
            [(1.32, [1.1, 1.6]), (1.5, [1.18, 1.87])],
        ),
        # a non-coding gene: "OR (95% CI) = x (lo-hi)"
        (
            "The G allele of the rs619586 was significantly under-represented in MS patients compared with controls (OR (95% CI) = 0.65 (0.46-0.92), adjusted P value = 0.03).",
            [(0.65, [0.46, 0.92])],
        ),
        # a somatic oncogene: an interval that contains 1 is not significant
        (
            "In a multivariable analysis, H1047R mutation trended to predict a lower pCR rate (hazard ratio, 0.1; 95% CI, 0.01 to 1; p=0.056).",
            [(0.1, [0.01, 1.0])],
        ),
    ],
)
def test_real_sentences(sentence: str, values: list[tuple[float, list[float] | None]]) -> None:
    assert [(e["value"], e["ci"]) for e in effects.parse_estimates(sentence)] == values


def test_allele_word_rule_needs_the_variant_named_in_the_paper() -> None:
    text = "Nonsense mutations in PCSK9 were associated with lower risk of CHD (HR 0.11, 95% CI 0.02-0.81)."
    doc = _doc_with("9", text, [("Gene", "PCSK9", {"identifier": "111"}), _dis("CHD", "D1")])
    assert effects.extract([doc], [_V1], _IDS)["estimates"] == []


# ------------------------------------------- human burden and CRISPR screens


def _burden(**row: Any) -> dict[str, Any]:
    base = {"phenotype": "LDL direct", "detail": "", "n_cases": 376106, "p_skato": 2e-134,
            "p_burden": 3.6e-132, "beta_burden": -0.039, "total_variants": 58}  # fmt: skip
    return {**base, **row}


def _genebass(
    rows: list[dict[str, Any]], caf: float | None = 0.001, coverage: float | None = 41.0
) -> dict[str, Any]:
    return {
        "masks": [
            {
                "mask": "pLoF",
                "phenotypes_tested": 4529,
                "caf": caf,
                "mean_coverage": coverage,
                "rows": rows,
            }
        ]
    }


def test_burden_hits_apply_the_published_thresholds_and_qc() -> None:
    near = _burden(phenotype="below SKAT-O bar", p_skato=3e-7, p_burden=7e-7)
    burden_only = _burden(phenotype="burden only", p_skato=3e-7, p_burden=6e-7)
    one_variant = _burden(phenotype="one variant", total_variants=1)
    few_cases = _burden(phenotype="rare trait", n_cases=1000)  # expected AC 1
    hits = bio.burden_hits(_genebass([_burden(), near, burden_only, one_variant, few_cases]))
    assert [h["phenotype"] for h in hits] == ["LDL direct", "burden only"]
    assert bio.burden_hits(_genebass([_burden()], coverage=12.0)) == []  # below 20×


def test_burden_without_a_qc_record_is_kept_and_says_so() -> None:
    hits = bio.burden_hits(_genebass([_burden()], caf=None, coverage=None))
    assert hits[0]["qc_applied"] is False
    assert "1 without QC" in bio.burden_md(_genebass([_burden()], caf=None, coverage=None), set())


def test_burden_table_states_what_carriers_cannot_show() -> None:
    md = bio.burden_md(_genebass([_burden()]), {"genebass", "pmid:36778668"})
    assert "| LDL direct | pLoF | 2e-134 | 3.6e-132 | lower | 58 | 376,106 |" in md
    assert "does not show the effect of complete loss" in md
    assert "UK Biobank Resource" in md and "CC BY 4.0" in md


def test_no_burden_hit_is_a_power_statement_not_tolerance() -> None:
    md = bio.burden_md(_genebass([]), set())
    assert (
        md.startswith("No association past the thresholds in pLoF")
        and "lack of power, not evidence of tolerance" in md
    )
    assert "may be underpowered" in bio.burden_line(_genebass([]))


def test_models_row_reads_the_burden_record() -> None:
    md = bio.models_md(_orth(_NCBI, []), {}, {}, {}, {}, {"genebass"}, _genebass([_burden()]))
    assert (
        "| Human: loss-of-function burden | 1 of 4,529 phenotypes in pLoF carriers (Genebass): LDL direct (lower) | [@genebass] |"
        in md
    )


def _screen(phenotype: str, hit: str, **kw: Any) -> dict[str, Any]:
    return {"phenotype": phenotype, "experimental_setup": "Timecourse", "hit": hit, "condition": "",
            "cell_line": "HeLa", "pmid": "26627737", **kw}  # fmt: skip


def test_crispr_table_groups_screens_and_leads_with_hits() -> None:
    screens = [
        _screen("viability", "NO"), _screen("viability", "NO"),
        _screen("resistance to chemicals", "YES", condition="Cisplatin", experimental_setup="Drug Exposure"),
    ]  # fmt: skip
    md = bio.crispr_md({"screens": screens}, {"biogrid_orcs", "pmid:26627737"})
    rows = md.splitlines()
    assert (
        rows[2]
        == "| resistance to chemicals | Drug Exposure | 1 / 1 | Cisplatin · HeLa | [@pmid:26627737] |"
    )
    assert rows[3].startswith("| viability | Timecourse | 0 / 2 | — |")
    assert "Hit in 1 of 3 human screens" in md and "not comparable across screens" in md
    assert bio.crispr_md({"screens": []}, set()) == ""


def test_blocks_say_why_a_gated_or_noncoding_source_is_missing(tmp_path: Path) -> None:
    _raw(tmp_path, "biogrid_orcs", None, status="skipped", note="BIOGRID_ORCS_ACCESS_KEY not set")
    _raw(tmp_path, "genebass", None, status="skipped", note="not a protein-coding gene")
    out, _ = blocks.expand(
        "{{table:crispr_screens}}\n{{table:burden}}", tmp_path, "G", set(), figures=False
    )
    assert out.splitlines() == [
        "*Not checked: DB biogrid_orcs skipped (BIOGRID_ORCS_ACCESS_KEY not set).*",
        "*Not checked: DB genebass skipped (not a protein-coding gene).*",
    ]


def test_new_connectors_are_not_imported_at_gather_load() -> None:
    """A connector release without them must fail one source, not every run."""
    source = (_SCRIPTS / "gather.py").read_text()
    for name in ("cbioportal", "genebass", "biogrid_orcs"):
        assert not re.search(rf"^from mcp_servers\.{name}\b", source, re.M)
