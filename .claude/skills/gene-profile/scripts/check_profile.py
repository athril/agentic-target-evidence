# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic checks for a gene-profile draft. No LLM involved.

Usage:
    uv run python .claude/skills/gene-profile/scripts/check_profile.py <run_dir> [--draft draft.md]

Errors (exit 1):
    E_UNKNOWN_KEY        [@key] does not exist in sources.json
    E_UNVERIFIED         cited source whose metadata was not fetched from an authoritative API
    E_ANCHOR             {{key:path|fmt}} does not resolve to a value in raw/<key>.json, or
                         its format does not apply (unknown format, a long list unformatted)
    E_ANCHOR_UNIT        the text around an anchor misstates its unit: a fraction followed
                         by '%' (use |pct), a count followed by '-fold'
    E_ANCHOR_NOT_CITED   a block uses a value from raw/<key>.json but does not cite [@key]
    E_UNCITED            a factual block (paragraph, list item, table row) has no citation
    E_TIER3_SNAPSHOT     a Tier-3 (press/news/web) source is cited inside the Snapshot
    E_MISSING_SECTION    a required section heading is absent
    E_GUARD_CONSTRAINT   text contradicts gnomAD constraint bands (repo guard)
    E_GUARD_COMMERCIAL   text overstates commercial whitespace (repo guard)
    E_UNKNOWN_BLOCK      a {{table:…}} / {{figure:…}} line names no known block
    E_MISSING_TABLE      a required block is absent from its section (the allelic series in
                         Human genetics, when the run has one)
Warnings:
    W_BARE_NUMBER        decimal/percentage typed by hand instead of a {{key:path}} anchor
    W_TIER3_ONLY         a block's only support is Tier-3 sources
    W_EMPTY_SECTION      a section heading with no content
    W_GUARDS_UNAVAILABLE repo guard modules could not be imported
    W_OFF_TARGET_CITATION a cited article PubTator3 annotated without any mention of the
                         target, an ortholog, a variant of it or one of its drugs
    W_GENERATED_SECTION  the draft writes a section render.py generates (References,
                         Appendix, Run Information, Known issues); it is dropped at render

Questions need a citation like any claim: an open question cites the evidence that leaves
it open. Block lines are checked as blocks, not as prose.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_blocks import BLOCKS, blank_blocks, find_blocks, required  # noqa: E402
from profile_schema import (  # noqa: E402
    ANCHOR_RE,
    CITE_GROUP_RE,
    GENERATED_SECTIONS,
    SECTIONS,
    Manifest,
    Source,
    anchor_problem,
    anchor_value,
    cited_keys,
    load_manifest,
    load_raw,
    load_sources,
    resolve_anchor,
)

BlockKind = Literal["para", "item", "row"]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_INTERP_RE = re.compile(r"^[>\s]*[*_]{0,2}\s*(interpretation|analyst note)\b", re.IGNORECASE)
# "Not checked …" / "Not assessed …" describe the run, not the gene (e.g. sections skipped at
# --depth quick); the coverage appendix is their evidence.
_NOT_CHECKED_RE = re.compile(r"^[>\s]*[*_]{0,2}\s*not (checked|assessed)\b", re.IGNORECASE)
_LABEL_RE = re.compile(r"^[*_]{1,2}[^*_]{1,80}[*_]{1,2}:?$")
_BARE_NUMBER_RE = re.compile(r"(?<![\w.:/-])(\d+\.\d+|\d+(?:\.\d+)?\s?%)(?![\w.])")
_NUMBERING_RE = re.compile(r"^\d+(\.\d+)*[.)]?\s+")
_FOLD_AFTER_RE = re.compile(r"^\s*-?\s*fold\b", re.IGNORECASE)


@dataclass
class Block:
    kind: BlockKind
    text: str
    line: int
    section: str  # section key ("" before the first known heading)


@dataclass
class Issue:
    code: str
    line: int
    message: str


@dataclass
class CheckReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "errors": [asdict(i) for i in self.errors],
                "warnings": [asdict(i) for i in self.warnings],
                "stats": self.stats,
            },
            indent=2,
            ensure_ascii=False,
        )


_TITLE_TO_KEY = {title.lower(): key for key, title in SECTIONS}


def section_key(heading: str) -> str:
    """Map a '## 3 Human genetics' style heading to its section key ('' if unknown;
    'generated' for a section render.py writes itself)."""
    title = _NUMBERING_RE.sub("", heading).strip().lower()
    if any(title.startswith(g.lower()) for g in GENERATED_SECTIONS):
        return "generated"
    if title in _TITLE_TO_KEY:
        return _TITLE_TO_KEY[title]
    for t, k in _TITLE_TO_KEY.items():
        if title.startswith(t):
            return k
    return ""


def parse_blocks(draft: str) -> tuple[list[Block], dict[str, int]]:
    """Split markdown into citation-bearing blocks. Returns (blocks, {section_key: heading_line})."""
    blocks: list[Block] = []
    headings: dict[str, int] = {}
    lines = draft.splitlines()
    section = ""
    in_fence = in_comment = False
    buf: list[str] = []
    buf_kind: BlockKind | None = None
    buf_line = 0
    item_indent = 0

    def flush() -> None:
        nonlocal buf, buf_kind
        if buf and buf_kind:
            blocks.append(Block(buf_kind, " ".join(s.strip() for s in buf), buf_line, section))
        buf, buf_kind = [], None

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if in_comment or line.strip().startswith("<!--"):
            flush()
            in_comment = "-->" not in line
            continue
        if not line.strip():
            flush()
            continue
        h = _HEADING_RE.match(line)
        if h:
            flush()
            if len(h.group(1)) == 2:
                section = section_key(h.group(2))
                if section:
                    headings.setdefault(section, i)
            continue
        if line.lstrip().startswith("|"):
            flush()
            nxt = lines[i] if i < len(lines) else ""
            if _TABLE_SEP_RE.match(line) or _TABLE_SEP_RE.match(nxt):
                continue  # separator or header row
            blocks.append(Block("row", line.strip(), i, section))
            continue
        m = _ITEM_RE.match(line)
        if m:
            flush()
            buf, buf_kind, buf_line, item_indent = [line[m.end() :]], "item", i, len(m.group(1))
            continue
        if buf_kind == "item" and (len(line) - len(line.lstrip())) > item_indent:
            buf.append(line)
            continue
        if buf_kind != "para":
            flush()
            buf, buf_kind, buf_line = [], "para", i
        buf.append(line.lstrip("> ").rstrip())
    flush()
    return blocks, headings


def needs_citation(block: Block) -> bool:
    t = block.text.strip()
    if block.section in ("gaps", "generated"):  # meta statements; render drops 'generated'
        return False
    if _INTERP_RE.match(t) or _NOT_CHECKED_RE.match(t):
        return False
    plain = CITE_GROUP_RE.sub("", t).strip()
    if _LABEL_RE.match(plain) or (plain.endswith(":") and len(plain) < 120):
        return False
    # A table row of only separators/dashes carries no claim.
    return not (block.kind == "row" and not re.sub(r"[|\s\-—–]", "", plain))


def _unit_problem(value: Any, fmt: str | None, after: str) -> str:
    """The unit the text gives an anchored value, when it contradicts the value: a 0–1
    fraction written as a percentage, a count written as a fold change."""
    if (fmt or "").strip():
        return ""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    if isinstance(value, float) and 0 <= value <= 1 and after.lstrip().startswith("%"):
        return "is a 0–1 fraction followed by '%': write {{key:path|pct}}"
    if isinstance(value, int) and _FOLD_AFTER_RE.match(after):
        return "is a count, not a fold change"
    return ""


def _resolve_anchors(text: str, run_dir: Path) -> str:
    def sub(m: re.Match[str]) -> str:
        v = resolve_anchor(run_dir, m.group(1), m.group(2), m.group(3))
        return v if v is not None else m.group(0)

    return ANCHOR_RE.sub(sub, text)


def _guard_inputs(run_dir: Path) -> dict[str, Any]:
    def data(key: str) -> dict[str, Any]:
        rec = load_raw(run_dir, key)
        return rec.data if rec and rec.status == "ok" and isinstance(rec.data, dict) else {}

    gc, kd = data("gnomad_constraint"), data("ot_known_drugs")
    return {
        "constraint": {k: gc.get(k) for k in ("loeuf", "pli", "mis_z", "moeuf")} if gc else None,
        "known_drugs_count": int(kd.get("total_count") or 0),
        "approved_count": sum(1 for d in kd.get("drugs", []) if d.get("is_approved")),
        "indication_approved_drug_count": int(
            data("openfda_indication").get("approved_drug_count") or 0
        ),
        "indication_active_trial_count": int(data("ct_condition").get("active_count") or 0),
    }


def _check_blocks(rep: CheckReport, run_dir: Path, draft: str) -> None:
    """Block names are known, and the required blocks sit in their sections."""
    section_at: dict[int, str] = {}
    section = ""
    for i, line in enumerate(draft.splitlines(), start=1):
        h = _HEADING_RE.match(line)
        if h and len(h.group(1)) == 2:
            section = section_key(h.group(2)) or section
        section_at[i] = section
    placed: dict[str, set[str]] = {}
    for line, name in find_blocks(draft):
        if name not in BLOCKS:
            rep.errors.append(Issue("E_UNKNOWN_BLOCK", line, f"unknown block '{name}'"))
            continue
        placed.setdefault(name, set()).add(section_at.get(line, ""))
    for name, sec in required(run_dir).items():
        if sec not in placed.get(name, set()):
            rep.errors.append(
                Issue("E_MISSING_TABLE", 0, f"'{{{{{name}}}}}' missing from section '{sec}'")
            )


def check(run_dir: Path, draft: str) -> CheckReport:
    rep = CheckReport()
    sources: dict[str, Source] = load_sources(run_dir)
    manifest: Manifest | None = (
        load_manifest(run_dir) if (run_dir / "manifest.json").exists() else None
    )
    _check_blocks(rep, run_dir, draft)
    blocks, headings = parse_blocks(blank_blocks(draft))

    # Required sections ------------------------------------------------------
    required = [k for k, _ in SECTIONS if k != "indication" or (manifest and manifest.disease)]
    for key in required:
        if key not in headings:
            rep.errors.append(Issue("E_MISSING_SECTION", 0, f"missing '## {dict(SECTIONS)[key]}'"))
    if "generated" in headings:
        rep.warnings.append(
            Issue(
                "W_GENERATED_SECTION",
                headings.pop("generated"),
                "render.py writes References, Appendix and Run Information; drop it from the draft",
            )
        )
        blocks = [b for b in blocks if b.section != "generated"]
    for key, line in headings.items():
        if not any(b.section == key for b in blocks):
            rep.warnings.append(Issue("W_EMPTY_SECTION", line, f"section '{key}' has no content"))

    # Guards (reuse the pipeline's deterministic guard functions) -------------
    guards: dict[str, Any] | None = None
    try:
        from services.evidence.commercial_interpret import apply_commercial_guards
        from services.evidence.constraint_interpret import (
            apply_constraint_guards,
            interpret_constraint,
        )

        gi = _guard_inputs(run_dir)
        reading = (
            interpret_constraint(gene_symbol=manifest.gene if manifest else "", **gi["constraint"])
            if gi["constraint"]
            else None
        )
        guards = {
            "reading": reading,
            "gi": gi,
            "constraint": apply_constraint_guards,
            "commercial": apply_commercial_guards,
        }
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the checker
        rep.warnings.append(Issue("W_GUARDS_UNAVAILABLE", 0, f"guards skipped: {exc}"))

    fact_blocks = cited_fact_blocks = anchors = bare = 0
    cited_all: set[str] = set()
    for b in blocks:
        keys = cited_keys(b.text)
        cited_all.update(keys)
        for k in keys:
            if k not in sources:
                rep.errors.append(Issue("E_UNKNOWN_KEY", b.line, f"[@{k}] not in sources.json"))
                continue
            s = sources[k]
            if not s.verified:
                rep.errors.append(Issue("E_UNVERIFIED", b.line, f"[@{k}] metadata not verified"))
            if s.tier == 3 and b.section == "snapshot":
                rep.errors.append(Issue("E_TIER3_SNAPSHOT", b.line, f"[@{k}] is Tier 3"))
            if s.kind == "article" and s.grounded is False and b.section != "gaps":
                rep.warnings.append(
                    Issue(
                        "W_OFF_TARGET_CITATION",
                        b.line,
                        f"[@{k}]: PubTator3 finds no mention of the gene in its abstract",
                    )
                )
        known = [k for k in keys if k in sources]
        if known and all(sources[k].tier == 3 for k in known) and b.section != "snapshot":
            rep.warnings.append(Issue("W_TIER3_ONLY", b.line, "only Tier-3 support"))

        for m in ANCHOR_RE.finditer(b.text):
            anchors += 1
            key, path = m.group(1), m.group(2)
            problem = anchor_problem(run_dir, key, path, m.group(3))
            if problem:
                rep.errors.append(
                    Issue("E_ANCHOR", b.line, f"{{{{{key}:{path}}}}} does not resolve: {problem}")
                )
            else:
                unit = _unit_problem(
                    anchor_value(run_dir, key, path), m.group(3), b.text[m.end() :]
                )
                if unit:
                    rep.errors.append(Issue("E_ANCHOR_UNIT", b.line, f"{m.group(0)} {unit}"))
            if key not in keys:
                rep.errors.append(
                    Issue(
                        "E_ANCHOR_NOT_CITED",
                        b.line,
                        f"value from '{key}' used without citing [@{key}]",
                    )
                )

        if needs_citation(b):
            fact_blocks += 1
            if keys:
                cited_fact_blocks += 1
            else:
                rep.errors.append(Issue("E_UNCITED", b.line, f"no citation: {b.text[:90]!r}"))
            # Numbers quoted from papers / registries / web pages cannot be anchored to raw/.
            from_text = any(sources[k].kind in ("article", "trial", "web") for k in known)
            stripped = CITE_GROUP_RE.sub("", ANCHOR_RE.sub("", b.text))
            for num in [] if from_text else _BARE_NUMBER_RE.findall(stripped):
                bare += 1
                rep.warnings.append(Issue("W_BARE_NUMBER", b.line, f"hand-typed number {num!r}"))

        if guards:
            text = _resolve_anchors(CITE_GROUP_RE.sub("", b.text), run_dir)
            if guards["reading"] is not None:
                out = guards["constraint"](text, guards["reading"])
                if out != text:
                    rep.errors.append(Issue("E_GUARD_CONSTRAINT", b.line, out[len(text) :].strip()))
            gi = guards["gi"]
            out = guards["commercial"](
                text,
                known_drugs_count=gi["known_drugs_count"],
                approved_count=gi["approved_count"],
                indication_approved_drug_count=gi["indication_approved_drug_count"],
                indication_active_trial_count=gi["indication_active_trial_count"],
            )
            if out != text:
                rep.errors.append(Issue("E_GUARD_COMMERCIAL", b.line, out[len(text) :].strip()))

    kinds: dict[str, int] = {}
    for k in cited_all & sources.keys():
        kinds[sources[k].kind] = kinds.get(sources[k].kind, 0) + 1
    rep.stats = {
        "fact_blocks": fact_blocks,
        "cited_fact_blocks": cited_fact_blocks,
        "citation_coverage": round(cited_fact_blocks / fact_blocks, 3) if fact_blocks else 1.0,
        "anchored_values": anchors,
        "bare_numbers": bare,
        "sources_cited": len(cited_all & sources.keys()),
        "sources_cited_by_kind": kinds,
        "sources_available": len(sources),
    }
    return rep


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("--draft", default="draft.md")
    a = p.parse_args()
    rep = check(a.run_dir, (a.run_dir / a.draft).read_text())
    (a.run_dir / "check_report.json").write_text(rep.to_json())
    for i in rep.errors:
        print(f"ERROR   L{i.line:<4} {i.code:<20} {i.message}")
    for i in rep.warnings:
        print(f"warning L{i.line:<4} {i.code:<20} {i.message}")
    print(json.dumps(rep.stats))
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
