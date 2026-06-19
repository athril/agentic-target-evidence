# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for FunctionalAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from agents.retrieval.functional.agent import FunctionalAgent
from agents.retrieval.functional.contract import CONTRACT
from mcp_servers.depmap.tools import DependencyBundle
from mcp_servers.impc.tools import ImpcBundle
from mcp_servers.project_score.tools import ProjectScoreBundle
from schemas.evidence import DataClass, EvidenceType
from tests.agents.conftest import make_task_msg

_SCREENS_ROWS = [
    {
        "gene_symbol": "KRAS",
        "screen_id": "PRISM_001",
        "cell_line": "A549",
        "cancer_type": "Lung",
        "gene_effect": -2.1,
        "is_essential": True,
        "dataset_version": "22Q4",
        "_classification": "SENSITIVE",
    },
]

_DEPMAP_BUNDLE = DependencyBundle(
    gene_symbol="KRAS",
    gene_effect_mean=-1.45,
    num_dependent_lines=312,
    total_lines=850,
    is_common_essential=False,
    selective_lineages=["Lung", "Pancreas"],
    source_link="https://depmap.org/portal/gene/KRAS",
    text="DepMap: KRAS mean gene effect=-1.45, dependent in 312/850 lines.",
)

_IMPC_BUNDLE_EMPTY = ImpcBundle(
    gene_symbol="KRAS",
    viability="unknown",
    phenotypes=[],
    total=0,
    source_link="https://www.ebi.ac.uk/mi/impc/",
    text="No statistically significant IMPC phenotypes found for KRAS.",
)
_PROJECT_SCORE_BUNDLE_EMPTY = ProjectScoreBundle(
    gene_symbol="KRAS",
    sidg_id="",
    text="Project Score: no gene record found for KRAS.",
)


async def test_functional_agent_contract_enforced():
    assert CONTRACT.name == "functional"
    assert CONTRACT.consumes == {"target_gene", "disease", "direction", "gene_id", "disease_id"}
    assert CONTRACT.produces == set()
    assert CONTRACT.max_loops == 1


async def test_functional_agent_returns_sensitive_and_non_sensitive(run_id, trace_id, ctx):
    msg = make_task_msg(
        "functional", {"target_gene": "KRAS", "disease": "lung cancer"}, run_id, trace_id
    )

    with (
        patch(
            "services.retrieval.functional.query_internal_db", AsyncMock(return_value=_SCREENS_ROWS)
        ),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        result = await FunctionalAgent().run(msg, ctx)

    assert result.intent == "result"
    sensitive = [e for e in result.payload if e.classification == DataClass.SENSITIVE]
    non_sensitive = [e for e in result.payload if e.classification == DataClass.NON_SENSITIVE]
    assert len(sensitive) == 1
    assert len(non_sensitive) == 1
    assert sensitive[0].evidence_type == EvidenceType.FUNCTIONAL_GENOMICS
    assert non_sensitive[0].evidence_type == EvidenceType.FUNCTIONAL_GENOMICS


async def test_functional_agent_excludes_classification_from_extra(run_id, trace_id, ctx):
    msg = make_task_msg(
        "functional", {"target_gene": "KRAS", "disease": "lung cancer"}, run_id, trace_id
    )

    with (
        patch(
            "services.retrieval.functional.query_internal_db", AsyncMock(return_value=_SCREENS_ROWS)
        ),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        result = await FunctionalAgent().run(msg, ctx)

    for ev in result.payload:
        assert "_classification" not in ev.extra


async def test_functional_agent_no_internal_rows_still_returns_depmap(run_id, trace_id, ctx):
    msg = make_task_msg(
        "functional", {"target_gene": "KRAS", "disease": "lung cancer"}, run_id, trace_id
    )

    with (
        patch("services.retrieval.functional.query_internal_db", AsyncMock(return_value=[])),
        patch(
            "services.retrieval.functional.get_dependency", AsyncMock(return_value=_DEPMAP_BUNDLE)
        ),
        patch(
            "services.retrieval.functional.get_impc_phenotypes",
            AsyncMock(return_value=_IMPC_BUNDLE_EMPTY),
        ),
        patch(
            "services.retrieval.functional.get_project_score",
            AsyncMock(return_value=_PROJECT_SCORE_BUNDLE_EMPTY),
        ),
    ):
        result = await FunctionalAgent().run(msg, ctx)

    assert len(result.payload) == 1
    assert result.payload[0].classification == DataClass.NON_SENSITIVE


async def test_functional_agent_loop_guard_max_loops_1(run_id, trace_id, ctx):
    assert CONTRACT.max_loops == 1
