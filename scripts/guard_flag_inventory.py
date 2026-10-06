# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Inventory the lens-output guard flags in generated reports.

Scans markdown reports (a run's ``results*/report/...`` tree, or ``docs/examples``)
for ``[⚠ <MARKER>: …]`` annotations and reports, per guard family, how many flags
fired and on which text. Two uses:

  * **Handoff accuracy** — after regenerating an example dossier, list the flags that
    remain from the files themselves instead of from memory.
  * **Corpus harvesting** — ``--candidates`` prints the distinct sentences that most
    likely triggered each flag, as YAML entries ready to be labelled (``expect:``
    fire / no_fire) and appended to ``tests/fixtures/guard_corpus.yaml``.

The guarded text is reconstructed from the report: the annotation's own line before
the ``[⚠`` marker, or the nearest preceding non-annotation line. Candidate sentences
are picked with per-family trigger terms; they are a starting point for labelling,
not a verdict.

Usage:
    uv run python scripts/guard_flag_inventory.py docs/examples
    uv run python scripts/guard_flag_inventory.py results*/ --candidates > /tmp/candidates.yaml
    uv run python scripts/guard_flag_inventory.py docs/examples --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.evidence.guard_core import split_sentences  # noqa: E402
from services.evidence.guard_rules import GUARD_RULES  # noqa: E402

_ANNOTATION = re.compile(r"\[⚠ ([A-Z][A-Z /-]*?GUARD)\b[^\]]*\]")
_MARKER_TO_FAMILY = {rule.marker: family for family, rule in GUARD_RULES.items()}

# Terms that make a sentence a likely trigger for each family (harvesting only).
_TRIGGERS: dict[str, re.Pattern[str]] = {
    "constraint": re.compile(r"haploinsuff|missense|mis_z|pLoF|LoF", re.IGNORECASE),
    "mendelian_floor": re.compile(r"GWAS|genome-wide|coloc", re.IGNORECASE),
    "commercial": re.compile(
        r"underserved|under-served|uncrowded|white.?space|competition|crowded|"
        r"market size|prevalence|population|\bno\b[^.]{0,40}(?:drug|therap|program)",
        re.IGNORECASE,
    ),
    "clinical_trial": re.compile(r"phase|NCT\d+|recruit|complet", re.IGNORECASE),
    "tissue_relevance": re.compile(r"tissue|express|TPM", re.IGNORECASE),
    "dependency_relevance": re.compile(r"depmap|dependen|crispr|chronos|essential", re.IGNORECASE),
    "quantity": re.compile(r"cell lines?|lineage|phase|dependen", re.IGNORECASE),
}


@dataclass(frozen=True)
class Flag:
    path: str
    line: int
    family: str
    guarded_text: str


def _guarded_text(lines: list[str], idx: int) -> str:
    """The prose an annotation on ``lines[idx]`` refers to."""
    own = lines[idx].split("[⚠", 1)[0].strip().strip("|").strip()
    if own:
        return own
    for j in range(idx - 1, -1, -1):
        prev = lines[j].strip()
        if prev and not prev.startswith("[⚠"):
            return prev.lstrip(">").strip()
    return ""


def scan(paths: list[Path]) -> list[Flag]:
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    flags: list[Flag] = []
    for f in files:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            for m in _ANNOTATION.finditer(line):
                family = _MARKER_TO_FAMILY.get(m.group(1), m.group(1))
                flags.append(Flag(str(f), i + 1, family, _guarded_text(lines, i)))
    return flags


def candidate_sentences(flags: list[Flag]) -> dict[str, dict[str, str]]:
    """family → {sentence: first source path}, de-duplicated."""
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for fl in flags:
        trigger = _TRIGGERS.get(fl.family)
        for sentence in split_sentences(fl.guarded_text):
            if trigger is None or trigger.search(sentence):
                out[fl.family].setdefault(sentence, fl.path)
    return out


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid YAML scalars


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="report files or directories")
    ap.add_argument("--json", action="store_true", help="machine-readable inventory")
    ap.add_argument(
        "--candidates", action="store_true", help="emit unlabelled corpus entries as YAML"
    )
    args = ap.parse_args()

    flags = scan(args.paths)

    if args.candidates:
        n = 0
        for family, sentences in sorted(candidate_sentences(flags).items()):
            for sentence, source in sentences.items():
                n += 1
                print(f"- id: harvested-{family}-{n:03d}")
                print(f"  family: {family}")
                print("  facts: <fact-sheet>")
                print(f"  text: {_yaml_str(sentence)}")
                print("  expect: <fire|no_fire>")
                print(f"  source: {_yaml_str(source)}")
        return 0

    by_family = Counter(fl.family for fl in flags)
    by_file: dict[str, Counter[str]] = defaultdict(Counter)
    for fl in flags:
        by_file[fl.path][fl.family] += 1

    if args.json:
        print(
            json.dumps(
                {
                    "total": len(flags),
                    "by_family": dict(by_family),
                    "by_file": {k: dict(v) for k, v in by_file.items()},
                    "flags": [fl.__dict__ for fl in flags],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"{len(flags)} guard flag(s) in {len(by_file)} file(s)\n")
    for family, count in by_family.most_common():
        print(f"  {count:4d}  {family}")
    for path in sorted(by_file):
        print(f"\n{path}")
        for fl in (f for f in flags if f.path == path):
            excerpt = fl.guarded_text[:140] + ("…" if len(fl.guarded_text) > 140 else "")
            print(f"  L{fl.line:<5d} {fl.family:<22s} {excerpt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
