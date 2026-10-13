# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prompts must not carry a concrete gene, drug, trial or disease as an example.

Small models copy examples: the safety prompt's TRPC6 phenotypes ("increased
vasoconstriction") and the clinical prompt's NCT id turned up in other genes' reports.
Skills and the string literals of the code that builds prompts and context blocks are
scanned for the genes, drugs, trials and diseases used while developing and evaluating
the pipeline, and for any NCT id. Examples must use placeholders (GENE-A, DRUG-A,
<NCT id>) or the run's own data. Comments and docstrings are not prompts and are not
scanned; config vocabularies (organ and tissue patterns) are data, not examples.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]

# Pairs used while building the pipeline, plus every pair of the trial-recall eval.
_DEV_TERMS = [
    "TRPC6", "PCSK9", "LRRK2", "SEPTIN9", "C1orf87", "BRCA1", "PTPN1", "KRAS",
    "focal segmental glomerulosclerosis", "FSGS", "myocardial infarction",
    "sparsentan", "BI 764198", "BIIB122", "DNL151", "bococizumab", "sotorasib", "AMG.?510",
    "FOURIER", "ODYSSEY", "VESALIUS", "CORALreef", "G2019S",
]  # fmt: skip


def _eval_terms() -> list[str]:
    """Genes and drugs of the trial-recall eval. Its disease names and trial acronyms are
    ordinary vocabulary ("obesity", "SELECT") that phenotype lists and SQL legitimately use."""
    data = yaml.safe_load((_ROOT / "tests/fixtures/landmark_trials.yaml").read_text())
    terms: list[str] = []
    for pair in data["pairs"]:
        terms.append(pair["gene"])
        terms += [label.partition(" (")[2].rstrip(")") for label in pair["trials"].values()]
    return [t for t in terms if t]


_DENY = re.compile(
    r"\b(?:"
    + "|".join(sorted({t if "?" in t else re.escape(t) for t in _DEV_TERMS + _eval_terms()}))
    + r")\b|\bNCT(?!0000000\d)\d{8}\b",
    re.IGNORECASE,
)

_PROMPT_CODE = [
    "src/agents",
    "src/services/evidence",
    "src/services/decision",
    "src/capabilities/target_validation",
    "src/core/evidence_text.py",
]
_VOCABULARY = {"src/services/evidence/organ_systems.py"}


def _string_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _code_files() -> list[Path]:
    files: list[Path] = []
    for entry in _PROMPT_CODE:
        p = _ROOT / entry
        files += [p] if p.is_file() else sorted(p.rglob("*.py"))
    return [f for f in files if str(f.relative_to(_ROOT)) not in _VOCABULARY]


@pytest.mark.parametrize("skill", sorted((_ROOT / "skills").glob("*.md")), ids=lambda p: p.name)
def test_skill_has_no_concrete_example(skill: Path) -> None:
    hits = [
        f"{skill.name}:{i}: {m.group(0)}"
        for i, line in enumerate(skill.read_text(encoding="utf-8").splitlines(), 1)
        if (m := _DENY.search(line))
    ]
    assert not hits, hits


def test_prompt_building_code_has_no_concrete_example() -> None:
    hits = [
        f"{f.relative_to(_ROOT)}:{line}: {m.group(0)}"
        for f in _code_files()
        for line, text in _string_literals(f)
        if (m := _DENY.search(text))
    ]
    assert not hits, hits


def test_the_scan_catches_a_planted_example() -> None:
    assert _DENY.search("e.g. TRPC6 knockouts show increased vasoconstriction")
    assert _DENY.search("one Phase 2 trial (NCT05213624, completed)")
    assert not _DENY.search("one Phase 2 trial (<NCT id>, completed) of DRUG-A in DISEASE-A")
