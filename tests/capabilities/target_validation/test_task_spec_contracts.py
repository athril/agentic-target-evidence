# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Every task_spec key a workflow node sends must be declared in the agent's contract.

Agents reject undeclared keys at runtime (ContractViolation) and the node then continues
without that agent's verdict. Adding `gtex_tissue_ranks` / `ot_known_drugs` to the lens
specs without updating the contracts silently dropped the biology, safety and commercial
lenses and the investigator from a full run, while every unit test passed. This test reads
the literal spec dicts in workflow.py and checks them against the contracts.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_WORKFLOW = Path("src/capabilities/target_validation/workflow.py")

# Agent name → contract module (package path under src/agents).
_CONTRACTS = {
    "genetics_lens": "agents.interpretation.genetics_lens.contract",
    "biology_lens": "agents.interpretation.biology_lens.contract",
    "safety_lens": "agents.interpretation.safety_lens.contract",
    "clinical_lens": "agents.interpretation.clinical_lens.contract",
    "commercial_lens": "agents.interpretation.commercial_lens.contract",
    "regulatory_lens": "agents.interpretation.regulatory_lens.contract",
    "investigator": "agents.synthesis.investigator.contract",
    "experiment": "agents.synthesis.experiment.contract",
}


def _dict_keys(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _own_nodes(fn: ast.AST):
    """Nodes of ``fn``'s body, not descending into nested function definitions."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def _sent_keys() -> dict[str, set[str]]:
    """Agent → keys of the spec dicts passed to ``_task_msg(state, "<agent>", …)``."""
    tree = ast.parse(_WORKFLOW.read_text())
    sent: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        # literal dicts assigned to a name inside this function (e.g. `spec = {...}`)
        named: dict[str, set[str]] = {}
        for node in _own_nodes(fn):
            if isinstance(node, ast.AnnAssign | ast.Assign):
                targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                for tgt in targets:
                    if isinstance(tgt, ast.Name) and node.value is not None:
                        named.setdefault(tgt.id, set()).update(_dict_keys(node.value))
        for node in _own_nodes(fn):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_task_msg"
                and len(node.args) >= 3
                and isinstance(node.args[1], ast.Constant)
            ):
                continue
            agent, spec = node.args[1].value, node.args[2]
            keys = _dict_keys(spec) | (
                named.get(spec.id, set()) if isinstance(spec, ast.Name) else set()
            )
            sent.setdefault(agent, set()).update(keys)
    return sent


@pytest.mark.parametrize("agent", sorted(_CONTRACTS))
def test_sent_spec_keys_are_declared(agent: str) -> None:
    sent = _sent_keys().get(agent, set())
    assert sent, f"no _task_msg call found for {agent}"
    contract = importlib.import_module(_CONTRACTS[agent]).CONTRACT
    undeclared = sent - set(contract.consumes) - {"direction"}  # direction is injected
    assert not undeclared, f"{agent} would reject undeclared task_spec keys: {sorted(undeclared)}"
