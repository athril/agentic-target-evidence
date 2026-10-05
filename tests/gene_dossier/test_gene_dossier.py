# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the gene-dossier Claude skill scripts (.claude/skills/gene-dossier/scripts).

Offline: a fixture run directory stands in for a gather.py run; HGNC is mocked with respx.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-dossier" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import check_dossier  # noqa: E402
import dossier_schema as ds  # noqa: E402
import gather  # noqa: E402
import render  # noqa: E402

# ----------------------------------------------------------------------------- fixtures


def _raw(run: Path, key: str, data: Any, status: str = "ok", section: str = "genetics") -> None:
    rec = ds.RawRecord(
        key=key,
        section=section,
        db_name=key,
        tool="t",
        status=status,
        retrieved_at="2026-10-04T00:00:00+00:00",
        data=data,
    )
    (run / "raw" / f"{key}.json").write_text(rec.model_dump_json())


def _src(
    key: str, kind: str = "database", tier: int = 1, verified: bool = True, **kw: Any
) -> ds.Source:
    return ds.Source(
        key=key,
        kind=kind,
        tier=tier,
        title=kw.pop("title", f"{key} record"),  # type: ignore[arg-type]
        url=f"https://example.org/{key}",
        accessed="2026-10-04",
        verified=verified,
        **kw,
    )


@pytest.fixture
def run(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir()
    _raw(
        tmp_path, "gnomad_constraint", {"loeuf": 0.7594, "pli": 1e-10, "mis_z": 1.70, "moeuf": 0.93}
    )
    _raw(
        tmp_path,
        "hpa",
        {"RNA single cell type specific nCPM": {"Podocytes": "241.3"}},
        section="biology",
    )
    _raw(tmp_path, "uspto", [{"patent_id": "1"}, {"patent_id": "2"}], section="commercial")
    _raw(
        tmp_path,
        "ot_known_drugs",
        {"total_count": 0, "drugs": []},
        status="empty",
        section="clinical",
    )
    sources = {
        s.key: s
        for s in [
            _src("gnomad_constraint"),
            _src("hpa"),
            _src("uspto"),
            _src("ot_known_drugs"),
            _src(
                "pmid:1",
                kind="article",
                authors=["A B", "C D", "E F", "G H"],
                venue="Nature",
                year=2005,
                pmid="1",
                title="A landmark?",
            ),
            _src("web:press", kind="web", tier=3, venue="Acme Inc."),
            _src("pmid:2", kind="article", verified=False),
        ]
    }
    ds.save_sources(tmp_path, sources)
    manifest = ds.Manifest(
        query="TST1",
        gene="TST1",
        hgnc_id="HGNC:1",
        ensembl_id="ENSG1",
        generated_at="2026-10-04T00:00:00+00:00",
        entries=[
            ds.ManifestEntry(
                key="gnomad_constraint", section="genetics", db_name="gnomAD", status="ok"
            ),
            ds.ManifestEntry(
                key="omim", section="genetics", db_name="OMIM", status="skipped", note="gated off"
            ),
        ],
    )
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    return tmp_path


def _draft(**sections: str) -> str:
    """A draft with every required heading; override a section's body by key."""
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


def _codes(rep: check_dossier.CheckReport) -> list[str]:
    return [i.code for i in rep.errors]


# ------------------------------------------------------------------------- anchors / paths


def test_get_path_and_formats(run: Path) -> None:
    assert ds.resolve_anchor(run, "gnomad_constraint", "loeuf", ".2f") == "0.76"
    assert (
        ds.resolve_anchor(run, "hpa", "RNA single cell type specific nCPM.Podocytes", ".0f")
        == "241"
    )
    assert ds.resolve_anchor(run, "uspto", "@", "len") == "2"
    assert ds.resolve_anchor(run, "uspto", "1.patent_id", None) == "2"
    assert ds.resolve_anchor(run, "gnomad_constraint", "mis_z", "pct") == "170%"
    assert ds.resolve_anchor(run, "gnomad_constraint", "nope", None) is None
    assert ds.resolve_anchor(run, "missing_key", "x", None) is None


def test_anchor_regex_allows_spaces_in_path() -> None:
    m = ds.ANCHOR_RE.search("{{hpa:RNA tissue specific nTPM.lung|.1f}}")
    assert m and m.group(2) == "RNA tissue specific nTPM.lung" and m.group(3) == ".1f"


def test_cited_keys_handles_groups() -> None:
    assert ds.cited_keys("x [@a; @pmid:12] y [@b]") == ["a", "pmid:12", "b"]


# ----------------------------------------------------------------------------- checker


def test_clean_draft_passes(run: Path) -> None:
    draft = _draft(genetics="LOEUF {{gnomad_constraint:loeuf|.2f}} [@gnomad_constraint].")
    rep = check_dossier.check(run, draft)
    assert rep.ok, rep.errors
    assert rep.stats["citation_coverage"] == 1.0
    assert rep.stats["anchored_values"] == 1


def test_uncited_fact_is_error_but_exemptions_are_not(run: Path) -> None:
    body = "\n\n".join(
        [
            "An uncited fact.",
            "*Interpretation:* judgement needs no citation.",
            "**Key findings**",
            "Is this a question?",
            "| Metric | Value | Refs |\n|---|---|---|\n| LOEUF | x | [@gnomad_constraint] |",
        ]
    )
    rep = check_dossier.check(run, _draft(biology=body, gaps="OMIM not checked (gated)."))
    assert _codes(rep) == ["E_UNCITED"]


def test_uncited_table_row_and_list_continuation(run: Path) -> None:
    body = (
        "- a bullet that wraps\n  onto a second line [@gnomad_constraint]\n\n"
        "| A | B |\n|---|---|\n| row without refs | x |"
    )
    rep = check_dossier.check(run, _draft(biology=body))
    assert _codes(rep) == ["E_UNCITED"]


def test_unknown_key_unverified_and_anchor_errors(run: Path) -> None:
    body = (
        "Fact [@not_a_source].\n\nFact [@pmid:2].\n\n"
        "Value {{gnomad_constraint:bogus}} [@gnomad_constraint].\n\n"
        "Value {{gnomad_constraint:loeuf|.2f}} cited elsewhere [@hpa]."
    )
    codes = _codes(check_dossier.check(run, _draft(biology=body)))
    assert codes == ["E_UNKNOWN_KEY", "E_UNVERIFIED", "E_ANCHOR", "E_ANCHOR_NOT_CITED"]


def test_tier3_in_snapshot_is_error_elsewhere_warning(run: Path) -> None:
    rep = check_dossier.check(
        run, _draft(snapshot="Deal news [@web:press].", commercial="Deal [@web:press].")
    )
    assert _codes(rep) == ["E_TIER3_SNAPSHOT"]
    assert "W_TIER3_ONLY" in [w.code for w in rep.warnings]


def test_missing_section_and_indication_requirement(run: Path) -> None:
    draft = _draft().replace("## Safety considerations", "## Safety")
    assert "E_MISSING_SECTION" in _codes(check_dossier.check(run, draft))
    m = ds.load_manifest(run)
    m.disease = "some disease"
    (run / "manifest.json").write_text(m.model_dump_json())
    assert "E_MISSING_SECTION" in _codes(check_dossier.check(run, _draft()))


def test_numbered_headings_are_recognised() -> None:
    assert check_dossier.section_key("3 Human genetics") == "genetics"
    assert check_dossier.section_key("10. Key literature") == "literature"


def test_bare_number_warning_skipped_for_literature(run: Path) -> None:
    rep = check_dossier.check(
        run, _draft(biology="Score 0.76 [@gnomad_constraint].\n\nResponse 35% [@pmid:1].")
    )
    assert [w.message for w in rep.warnings if w.code == "W_BARE_NUMBER"] == [
        "hand-typed number '0.76'"
    ]


def test_constraint_guard_fires_on_resolved_text(run: Path) -> None:
    body = (
        "TRPC6 is haploinsufficient (LOEUF {{gnomad_constraint:loeuf|.2f}}) [@gnomad_constraint]."
    )
    assert "E_GUARD_CONSTRAINT" in _codes(check_dossier.check(run, _draft(genetics=body)))
    ok = "TRPC6 is not haploinsufficient [@gnomad_constraint]."
    assert "E_GUARD_CONSTRAINT" not in _codes(check_dossier.check(run, _draft(genetics=ok)))


def test_commercial_guard_fires_on_blanket_no_drugs(run: Path) -> None:
    rep = check_dossier.check(
        run, _draft(commercial="There are no drugs targeting TST1 [@ot_known_drugs].")
    )
    assert "E_GUARD_COMMERCIAL" in _codes(rep)
    ok = "There is no approved TST1-targeted drug [@ot_known_drugs]."
    assert "E_GUARD_COMMERCIAL" not in _codes(check_dossier.check(run, _draft(commercial=ok)))


# ------------------------------------------------------------------------------ render


def test_number_citations_first_appearance_order_and_tier3_mark(run: Path) -> None:
    sources = ds.load_sources(run)
    text, numbers = render.number_citations("a [@hpa] b [@web:press; @hpa] c [@uspto]", sources)
    assert numbers == {"hpa": 1, "web:press": 2, "uspto": 3}
    assert "[[1](#ref-1), [2◇](#ref-2)]" in text


def test_reference_formatting(run: Path) -> None:
    s = ds.load_sources(run)
    art = render.format_reference(4, s["pmid:1"])
    assert art.startswith(
        '<a id="ref-4"></a>\\[4\\] A B, C D, E F, et al. A landmark? *Nature*. 2005.'
    )
    assert "?." not in art and "et al.." not in art
    assert render.format_reference(5, s["web:press"]).count("Acme Inc.") == 1


def test_render_refuses_on_errors_then_allows(run: Path) -> None:
    (run / "draft.md").write_text(_draft(biology="Uncited."))
    assert render.render(run, "draft.md", allow_issues=False, pdf=False) == 1
    assert not (run / "TST1-report.md").exists()
    assert render.render(run, "draft.md", allow_issues=True, pdf=False) == 0
    out = (run / "TST1-report.md").read_text()
    assert "## Known issues" in out and "E_UNCITED" in out


def test_render_output(run: Path) -> None:
    (run / "draft.md").write_text(
        _draft(genetics="LOEUF {{gnomad_constraint:loeuf|.2f}} [@gnomad_constraint].")
    )
    assert render.render(run, "draft.md", allow_issues=False, pdf=False) == 0
    out = (run / "TST1-report.md").read_text()
    assert out.startswith("# TST1 — Gene Dossier")
    assert "LOEUF 0.76 [[1](#ref-1)]" in out
    assert "{{" not in out and "[@" not in out
    # references are separate paragraphs (a markdown list would renumber them)
    assert '<a id="ref-1"></a>\\[1\\]' in out and "\n\n" in out.split("## References")[1]
    assert "| Human genetics | gnomAD | — | OMIM |" in out
    assert json.loads((run / "check_report.json").read_text())["ok"] is True


# ------------------------------------------------------------------------------ gather


def test_is_substantive_ignores_echoed_inputs() -> None:
    assert not gather.is_substantive({"disease": "x", "total": 0, "mapping": "none", "text": "t"})
    assert not gather.is_substantive({"gene_symbol": "X", "associations": [], "total": 0})
    assert gather.is_substantive({"gene_symbol": "X", "total": 3})
    assert gather.is_substantive([{"patent_id": "1"}])


def test_derived_sources_from_pubmed_and_trials() -> None:
    rec = ds.RawRecord(
        key="pubmed_top",
        section="literature",
        db_name="PubMed",
        tool="t",
        status="ok",
        retrieved_at="x",
        data={
            "records": [
                {"pmid": "9", "title": "T", "authors": [], "journal": "J", "year": 2020, "doi": ""}
            ]
        },
    )
    [s] = gather.derived_sources(rec, "2026-10-04")
    assert (s.key, s.kind, s.verified) == ("pmid:9", "article", True)
    rec = ds.RawRecord(
        key="ct_gene",
        section="clinical",
        db_name="CT",
        tool="t",
        status="ok",
        retrieved_at="x",
        data={"trials": [{"nct_id": "NCT1", "title": "Trial"}]},
    )
    [s] = gather.derived_sources(rec, "2026-10-04")
    assert (s.key, s.kind, s.tier) == ("nct:NCT1", "trial", 2)


_HGNC = "https://rest.genenames.org"


@respx.mock
async def test_resolve_identity_direct_and_alias() -> None:
    respx.get(f"{_HGNC}/fetch/symbol/TRPC6").mock(
        return_value=httpx.Response(200, json={"response": {"docs": [{"symbol": "TRPC6"}]}})
    )
    doc, note = await gather.resolve_identity("TRPC6")
    assert doc["symbol"] == "TRPC6" and note == ""

    respx.get(f"{_HGNC}/fetch/symbol/FSGS2").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC}/search/prev_symbol/FSGS2").mock(
        return_value=httpx.Response(200, json={"response": {"docs": [{"symbol": "TRPC6"}]}})
    )
    respx.get(f"{_HGNC}/search/alias_symbol/FSGS2").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    doc, note = await gather.resolve_identity("FSGS2")
    assert doc["symbol"] == "TRPC6" and "previous symbol" in note


@respx.mock
async def test_resolve_identity_refuses_ambiguous_alias() -> None:
    respx.get(url__regex=rf"{_HGNC}/fetch/symbol/.*").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC}/search/prev_symbol/AMB1").mock(
        return_value=httpx.Response(200, json={"response": {"docs": [{"symbol": "GENEA"}]}})
    )
    respx.get(f"{_HGNC}/search/alias_symbol/AMB1").mock(
        return_value=httpx.Response(200, json={"response": {"docs": [{"symbol": "GENEB"}]}})
    )
    with pytest.raises(gather.IdentityError) as exc:
        await gather.resolve_identity("AMB1")
    assert exc.value.candidates == ["GENEA", "GENEB"]


def test_faers_names_merge_salts_and_skip_unapproved() -> None:
    drugs = [
        {"drug_name": "INCLISIRAN SODIUM", "is_approved": True},
        {"drug_name": "INCLISIRAN", "is_approved": True},
        {"drug_name": "FROVOCIMAB", "is_approved": False},
        {"drug_name": "EVOLOCUMAB", "is_approved": True},
    ]
    assert gather.faers_names(drugs) == ["INCLISIRAN", "EVOLOCUMAB"]


def test_search_symbols_include_symbol_like_previous_symbols() -> None:
    ctx = gather.Ctx(query="SEPT9", symbol="SEPTIN9", hgnc={"prev_symbol": ["MSF", "SEPT9"]})
    assert gather.search_symbols(ctx) == ["SEPTIN9", "SEPT9"]
    assert gather.tiab(ctx) == '("SEPTIN9"[tiab] OR "SEPT9"[tiab])'
    assert gather.tiab(gather.Ctx(query="X", symbol="TP53")) == '"TP53"[tiab]'


def test_not_checked_statements_need_no_citation(run: Path) -> None:
    body = "Not assessed at quick depth (no commercial sources queried).\n\n*Not checked:* USPTO."
    assert check_dossier.check(run, _draft(commercial=body)).ok


def test_derived_sources_from_uspto_payload() -> None:
    patent = {
        "patent_id": "12156874",
        "title": "INHIBITORS OF TRPC6",
        "assignee": "BI",
        "filing_date": "2021-10-11",
        "uspto_link": "https://patentcenter.uspto.gov/x",
    }
    rec = ds.RawRecord(
        key="uspto",
        section="commercial",
        db_name="USPTO",
        tool="t",
        status="ok",
        retrieved_at="x",
        data={
            "patents": [patent],
            "total": 1,
            "target_count": 1,
            "indication_only_count": 0,
            "capped": False,
        },
    )
    [s] = gather.derived_sources(rec, "2026-10-05")
    assert (s.key, s.kind, s.year) == ("patent:12156874", "patent", 2021)
