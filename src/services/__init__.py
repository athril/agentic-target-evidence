# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic operators and governed model-ops (agent/service split).

Agents reason (judgment / synthesis / planning); services transform, retrieve, and
orchestrate. "Service" is a role/folder label — it is NOT an exemption from harness
governance. A service that calls a model (claim extraction, semantic clustering,
screening, …) is a *model-op*: it must route via ``ctx.model``, be wrapped in a
Langfuse span, and count against ``step_budget`` (CLAUDE.md rules #4/#7). Such
services reuse ``harness.ServiceContract`` (= ``AgentContract``) and inherit
``BaseAgent``. Pure operators (dedup math, ID crosswalk, SJR lookup, graph build,
template render) call no model and are plain modules, still traced.

Subpackages:
- ``retrieval``  — MCP-backed fetch → CoreClaim (patent, clinical_trial, opentargets, functional)
- ``evidence``   — extraction, normalization, scoring, graph build, screening
- ``decision``   — reconciler, suitability, report_generator
"""
