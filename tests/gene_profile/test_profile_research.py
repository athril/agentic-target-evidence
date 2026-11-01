# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The gene profile's researcher blocks: new records (HPA immune fields, mouse orthology,
paper metadata), the analyses built on them, the blocks they render and their empty states.

Offline: every record is a fixture. Gene and variant names appear only as fixture data."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))


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
