# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Render draft.md → <GENE>-profile.md (and optionally <GENE>-profile.pdf).

The model writes only the body (``draft.md``). This script adds everything that must be
exact: header identity line, numbered citations, substituted data values, the reference
list, the coverage table and the methods/provenance appendix. It runs check_profile first
and refuses to render on errors unless ``--allow-issues`` is passed, in which case the
failures are printed in a "Known issues" section of the report.

Programmatic blocks (``{{table:…}}``, ``{{figure:…}}`` lines; profile_blocks.py) are
expanded first: tables from raw/, figures drawn into ``figures/`` (``--no-figures`` leaves
them out).

Usage:
    uv run python .claude/skills/gene-profile/scripts/render.py <run_dir> [--pdf] [--allow-issues]
"""

from __future__ import annotations

import argparse
import html
import importlib.metadata
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_profile import CheckReport, check, section_key  # noqa: E402
from profile_blocks import expand  # noqa: E402
from profile_schema import (  # noqa: E402
    ANCHOR_RE,
    CITE_GROUP_RE,
    CITE_KEY_RE,
    SECTIONS,
    Manifest,
    Source,
    load_manifest,
    load_raw,
    load_sources,
    resolve_anchor,
)

_KIND_HEADINGS = [
    ("database", "Databases"),
    ("article", "Literature"),
    ("trial", "Clinical trials"),
    ("patent", "Patents"),
    ("label", "Drug labels"),
    ("web", "Web (company / news)"),
]
_TIER3 = "◇"
_PACKAGE = "agentic-target-evidence"
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DETERMINISTIC = "deterministic, no LLM"


def package_version() -> str:
    try:
        return importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_id() -> str:
    """``git describe`` when rendering inside the repo checkout; "" for an installed plugin."""
    if (
        not (_REPO_ROOT / "pyproject.toml").is_file()
        or not (_REPO_ROOT / "src/mcp_servers").is_dir()
    ):
        return ""
    try:
        return subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def number_citations(text: str, sources: dict[str, Source]) -> tuple[str, dict[str, int]]:
    """Replace [@a; @b] groups with linked numbers in first-appearance order."""
    numbers: dict[str, int] = {}

    def sub(m: re.Match[str]) -> str:
        keys = [k for k in CITE_KEY_RE.findall(m.group(1)) if k in sources]
        for k in keys:
            numbers.setdefault(k, len(numbers) + 1)
        if not keys:
            return m.group(0)
        refs = sorted({numbers[k] for k in keys})
        by_num = {numbers[k]: k for k in keys}
        parts = [f"[{n}{_TIER3 if sources[by_num[n]].tier == 3 else ''}](#ref-{n})" for n in refs]
        return "[" + ", ".join(parts) + "]"

    return CITE_GROUP_RE.sub(sub, text), numbers


def drop_generated_sections(body: str) -> str:
    """The draft without the sections this script writes itself (References, Appendix, Run
    Information, Known issues), so they never appear twice."""
    out: list[str] = []
    skipping = False
    for line in body.splitlines():
        if line.startswith("## "):
            skipping = section_key(line[3:]) == "generated"
        if not skipping:
            out.append(line)
    text = "\n".join(out).rstrip()
    while text.endswith("---"):  # a rule left dangling before a dropped section
        text = text[:-3].rstrip()
    return text + "\n"


def substitute_anchors(text: str, run_dir: Path) -> str:
    def sub(m: re.Match[str]) -> str:
        v = resolve_anchor(run_dir, m.group(1), m.group(2), m.group(3))
        return v if v is not None else f"⟨unresolved {m.group(1)}:{m.group(2)}⟩"

    return ANCHOR_RE.sub(sub, text)


def _authors(a: list[str]) -> str:
    if not a:
        return ""
    return ", ".join(a[:3]) + (", et al. " if len(a) > 3 else ". ")


def _sentence(text: str) -> str:
    """End a title with exactly one terminal punctuation mark."""
    text = text.strip()
    return text if text.endswith((".", "?", "!")) else text + "."


def format_reference(n: int, s: Source) -> str:
    anchor = f'<a id="ref-{n}"></a>\\[{n}\\] '
    tier = f" {_TIER3} Tier 3" if s.tier == 3 else ""
    if s.kind == "article":
        doi = f" doi:[{s.doi}](https://doi.org/{s.doi})." if s.doi else ""
        venue = f" *{s.venue}*." if s.venue else ""
        year = f" {s.year}." if s.year else ""
        return f"{anchor}{_authors(s.authors)}{_sentence(s.title)}{venue}{year} PMID: [{s.pmid}]({s.url}).{doi}{tier}"
    if s.kind == "trial":
        sponsor = f" {_sentence(s.venue)}" if s.venue else ""
        return f"{anchor}{_sentence(s.title)}{sponsor} ClinicalTrials.gov [{s.nct_id}]({s.url}). Accessed {s.accessed}."
    if s.kind == "patent":
        who = f" {_sentence(s.venue)}" if s.venue else ""
        filed = f" Filed {s.year}." if s.year else ""
        return f"{anchor}{_sentence(s.title)}{who} US patent [{s.patent_number}]({s.url}).{filed}"
    if s.kind == "database":
        ver = f" release {s.db_version}" if s.db_version else ""
        return (
            f"{anchor}{s.title}. {s.db_name}{ver}. <{s.url}>. Accessed {s.accessed}"
            f" (via {s.retrieved_via})."
        )
    venue = f" {_sentence(s.venue)}" if s.venue else ""
    year = f" {s.year}." if s.year else ""
    return f"{anchor}{_sentence(s.title)}{venue}{year} <{s.url}>. Accessed {s.accessed}.{tier}"


def references_md(numbers: dict[str, int], sources: dict[str, Source]) -> str:
    out = ["## References", ""]
    for kind, heading in _KIND_HEADINGS:
        items = sorted((n, sources[k]) for k, n in numbers.items() if sources[k].kind == kind)
        if not items:
            continue
        out += [f"### {heading}", ""]
        for n, s in items:  # one paragraph each: a markdown list would renumber them
            out += [format_reference(n, s), ""]
    return "\n".join(out)


def _evidence_links(
    keys: list[str], m: Manifest, sources: dict[str, Source], numbers: dict[str, int]
) -> tuple[str, str]:
    """(source URL, "[n, m]" cited reference links) for a group of retrieval keys."""
    urls = {e.key: e.url for e in m.entries}
    url = next((sources[k].url for k in keys if k in sources and sources[k].url), "") or next(
        (urls[k] for k in keys if urls.get(k)), ""
    )
    refs = sorted({numbers[k] for k in keys if k in numbers})
    return url, "[" + ", ".join(f"[{n}](#ref-{n})" for n in refs) + "]" if refs else ""


def appendix_md(m: Manifest, sources: dict[str, Source], numbers: dict[str, int]) -> str:
    titles = dict(SECTIONS)
    rows = ["| Section | Found | Checked, nothing found | Not checked |", "|---|---|---|---|"]
    # section -> bucket -> db_name -> retrieval keys
    by_sec: dict[str, dict[str, dict[str, list[str]]]] = {}
    for e in m.entries:
        bucket = {"ok": "found", "empty": "empty"}.get(e.status, "not")
        sec = by_sec.setdefault(e.section, {"found": {}, "empty": {}, "not": {}})
        sec[bucket].setdefault(e.db_name, []).append(e.key)

    def cell(dbs: dict[str, list[str]]) -> str:
        out = []
        for name, keys in dbs.items():
            url, refs = _evidence_links(keys, m, sources, numbers)
            out.append((f"[{name}]({url})" if url else name) + (f" {refs}" if refs else ""))
        return ", ".join(out) or "—"

    for sec, _ in SECTIONS:
        if sec in by_sec:
            b = {k: cell(v) for k, v in by_sec[sec].items()}
            rows.append(f"| {titles[sec]} | {b['found']} | {b['empty']} | {b['not']} |")
    detail = ["| Key | Database | Status | Evidence | Note |", "|---|---|---|---|---|"]
    for e in m.entries:
        note = e.note.replace("|", "/")[:120]
        url, refs = _evidence_links([e.key], m, sources, numbers)
        links = [f"[source]({url})" if url else "", refs]
        evidence = " · ".join(x for x in links if x) or "—"
        detail.append(f"| `{e.key}` | {e.db_name} | {e.status} | {evidence} | {note} |")
    versions = ", ".join(f"{k} {v}" for k, v in sorted(m.db_versions.items())) or "not reported"
    return "\n".join(
        [
            "## Appendix: Coverage, methods & provenance",
            "",
            "### Coverage",
            "",
            "*Found* = the source returned data. *Checked, nothing found* = the source was queried and "
            "returned no record (a real negative for that source). *Not checked* = the source was "
            "skipped (licence gate, missing key, not applicable) or failed — absence there is **not** "
            "evidence of absence. Each database links to the record or query that was run; "
            "bracketed numbers are the references that cite it.",
            "",
            *rows,
            "",
            "### Retrieval log",
            "",
            "*source* opens the record page or the exact API request that was run; bracketed "
            "numbers are the references that cite it. The full API responses are kept in the run "
            "directory under `raw/<key>.json`.",
            "",
            *detail,
            "",
            "### Methods",
            "",
            f"- Structured data retrieved {m.generated_at} by `gather.py` (deterministic, no LLM) via the "
            "repo's source connectors and public REST APIs; each response is stored in `raw/`.",
            f"- Database releases reported by the APIs: {versions}.",
            "- Every numeric value in the body is substituted from `raw/` at render time; literature, "
            "trial and patent metadata come from PubMed E-utilities, ClinicalTrials.gov and USPTO "
            "responses, never from model recall.",
            "- Literature: NCBI's curated gene–PubMed links (gene2pubmed, GeneRIFs) and PubMed "
            "title/abstract search for the symbol and its previous symbols. *Most cited* = the "
            "curated papers plus the top 1,000 keyword hits by relevance, re-ranked by NIH iCite "
            "citation count; *Reviews* = review articles from the last 5 years; *Recent* = the "
            "last 24 months. PubTator3 annotations identify the gene (NCBI Gene id, orthologs), "
            "its variants, chemicals, diseases, organisms and cell lines in each title and "
            "abstract; keyword hits that do not name the gene are left out, and the literature "
            "map counts PubTator's relations inside the gene's papers (relation types are "
            "text-mining tags). The papers discussed are chosen by the writer from these lists "
            "after reading each abstract.",
            "- Allelic series, variant spectrum and figures are built by the scripts from the "
            "records (UniProt features and variants, ClinVar via gnomAD, gnomAD pLoF, GWAS "
            "Catalog, Open Targets variant effects, AlphaFold, GTEx, HPA); a variant's effect "
            "on the target is typed only from its consequence, Open Targets' curated direction "
            "or its own QTL.",
            "- Narrative synthesis by Claude; checked by `check_profile.py` (citation coverage, reference "
            "validity, value anchors, gnomAD-constraint and commercial guards).",
            "- Licence-gated sources (OMIM, TTD, SCImago) are used only when enabled; see NOTICE.md.",
            "",
        ]
    )


def header_md(
    run_dir: Path, m: Manifest, sources: dict[str, Source], numbers: dict[str, int]
) -> str:
    rec = load_raw(run_dir, "hgnc")
    hg = rec.data if rec and isinstance(rec.data, dict) else {}
    kinds: dict[str, int] = {}
    for k in numbers:
        kinds[sources[k].kind] = kinds.get(sources[k].kind, 0) + 1
    kind_str = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    lines = [f"# {m.gene} — Gene Profile", ""]
    if hg.get("name"):
        lines += [
            f"**{hg['name']}**" + (f" · chr {hg['location']}" if hg.get("location") else ""),
            "",
        ]
    ids = [
        m.hgnc_id,
        f"Ensembl [{m.ensembl_id}](https://www.ensembl.org/Homo_sapiens/Gene/Summary?g={m.ensembl_id})"
        if m.ensembl_id
        else "",
        f"UniProt [{m.uniprot}](https://www.uniprot.org/uniprotkb/{m.uniprot}/entry)"
        if m.uniprot
        else "",
        f"NCBI Gene [{m.entrez_id}](https://www.ncbi.nlm.nih.gov/gene/{m.entrez_id})"
        if m.entrez_id
        else "",
    ]
    lines.append(" · ".join(x for x in ids if x))
    lines.append("")
    if m.disease:
        lines += [
            f"**Indication scope:** {m.disease}" + (f" (`{m.disease_id}`)" if m.disease_id else ""),
            "",
        ]
    lines += [
        f"**Version:** {package_version()} · **Depth:** {m.depth} · **Sources cited:** "
        f"{len(numbers)} ({kind_str})",
        "",
    ]
    if m.identity_note:
        lines += [f"> **Identity note:** {m.identity_note}", ""]
    lines += [
        "> Every factual statement carries a numbered citation; numbers are substituted from "
        "the retrieved records at render time. Statements labelled *Interpretation* are "
        f"analysis, not data. {_TIER3} marks Tier-3 (company/news) sources.",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def run_info_md(m: Manifest, generated_at: datetime, model: str) -> str:
    """Closing section: when it was rendered, the exact build, and what ran each step."""
    build = build_id()
    synthesis = f"`{model}`" if model else "_not recorded_"
    return "\n".join(
        [
            "## Run Information",
            "",
            f"- **Generated:** {generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            f"- **Version:** {_PACKAGE} {package_version()}" + (f" (`{build}`)" if build else ""),
            f"- **Data retrieved:** {m.generated_at}",
            "",
            "| Step | Model |",
            "|---|---|",
            f"| Data retrieval (`gather.py`) | {_DETERMINISTIC} |",
            f"| Narrative synthesis (`draft.md`) | {synthesis} |",
            f"| Citation & guard checks (`check_profile.py`) | {_DETERMINISTIC} |",
            f"| Value substitution & rendering (`render.py`) | {_DETERMINISTIC} |",
            "",
        ]
    )


def known_issues_md(rep: CheckReport) -> str:
    out = ["## Known issues", "", "This report was rendered with unresolved check failures:", ""]
    out += [f"- `{i.code}` (draft line {i.line}): {i.message}" for i in rep.errors]
    return "\n".join(out) + "\n"


_CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:10.5pt;line-height:1.45;
color:#1f2328;max-width:900px;margin:0 auto;padding:24px}
h1{font-size:20pt;border-bottom:2px solid #d0d7de;padding-bottom:6px}
h2{font-size:14pt;border-bottom:1px solid #d0d7de;padding-bottom:4px;margin-top:22px;page-break-after:avoid}
h3{font-size:11.5pt;page-break-after:avoid}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:9pt;page-break-inside:auto}
th,td{border:1px solid #d0d7de;padding:4px 6px;vertical-align:top;text-align:left}
th{background:#f6f8fa} tr{page-break-inside:avoid}
blockquote{margin:8px 0;padding:4px 12px;border-left:4px solid #d0d7de;color:#57606a}
code{font-size:9pt;background:#f6f8fa;padding:1px 3px;border-radius:3px}
a{color:#0969da;text-decoration:none}
img{max-width:100%;page-break-inside:avoid}
"""


def write_pdf(md_path: Path, title: str) -> Path | None:
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        print("PDF skipped: markdown-it-py not installed", file=sys.stderr)
        return None
    chrome = next(
        (
            shutil.which(b)
            for b in ("google-chrome", "chromium", "chromium-browser")
            if shutil.which(b)
        ),
        None,
    )
    if not chrome:
        print("PDF skipped: no Chrome/Chromium binary found", file=sys.stderr)
        return None
    body = MarkdownIt("commonmark", {"html": True}).enable("table").render(md_path.read_text())
    pdf_path = md_path.with_suffix(".pdf")
    # Chrome prints HTML, so the page is built in a temp dir and nothing but the PDF is left.
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / f"{md_path.stem}.html"
        # The base URL resolves the figures next to the Markdown file.
        base = md_path.resolve().parent.as_uri() + "/"
        html_path.write_text(
            f"<!doctype html><html><head><meta charset='utf-8'><base href='{base}'>"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
            f"<body>{body}</body></html>"
        )
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    return pdf_path


def render(
    run_dir: Path,
    draft_name: str,
    allow_issues: bool,
    pdf: bool,
    model: str = "",
    figures: bool = True,
) -> int:
    draft = (run_dir / draft_name).read_text()
    rep = check(run_dir, draft)
    (run_dir / "check_report.json").write_text(rep.to_json())
    if not rep.ok and not allow_issues:
        for i in rep.errors:
            print(f"ERROR L{i.line} {i.code}: {i.message}", file=sys.stderr)
        print(
            f"\n{len(rep.errors)} error(s); fix draft.md or pass --allow-issues.", file=sys.stderr
        )
        return 1

    m = load_manifest(run_dir)
    sources = load_sources(run_dir)
    body = re.sub(r"^#\s+.*\n", "", draft, count=1)  # header is generated
    body = drop_generated_sections(body)
    body, notes = expand(body, run_dir, m.gene, set(sources), figures=figures)
    for note in notes:
        print(f"block {note}", file=sys.stderr)
    body = substitute_anchors(body, run_dir)
    body, numbers = number_citations(body, sources)
    parts = [header_md(run_dir, m, sources, numbers)]
    if not rep.ok:
        parts.append(known_issues_md(rep))
    parts += [
        body.strip() + "\n",
        "---",
        "",
        references_md(numbers, sources),
        appendix_md(m, sources, numbers),
        "---",
        "",
        run_info_md(m, datetime.now(UTC), model),
    ]
    md_path = run_dir / f"{m.gene}-profile.md"
    md_path.write_text("\n".join(parts))
    print(md_path)
    if pdf:
        out = write_pdf(md_path, f"{m.gene} — Gene Profile")
        if out:
            print(out)
    print(f"stats: {rep.stats}", file=sys.stderr)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("--draft", default="draft.md")
    p.add_argument(
        "--pdf",
        action="store_true",
        help="also write <GENE>-profile.pdf (off by default; needs headless Chrome/Chromium)",
    )
    p.add_argument(
        "--allow-issues",
        action="store_true",
        help="render despite check errors, listing them under 'Known issues'",
    )
    p.add_argument(
        "--no-figures",
        action="store_true",
        help="leave the {{figure:…}} blocks out instead of drawing them into figures/",
    )
    p.add_argument(
        "--model",
        default="",
        help="model ID that wrote draft.md, recorded in the Run Information section",
    )
    a = p.parse_args()
    sys.exit(render(a.run_dir, a.draft, a.allow_issues, a.pdf, a.model, not a.no_figures))


if __name__ == "__main__":
    main()
