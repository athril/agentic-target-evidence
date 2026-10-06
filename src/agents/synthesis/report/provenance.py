# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run provenance for generated reports: a short version stamp for the header, and a
closing "Run information" section with the exact build and the model behind each step.

Models are resolved through the same ``Router.select`` path the agents use, so the
report states what the active routing policy and registered providers actually
pick — not what ``config/routing.yaml`` merely lists (e.g. a ``hybrid`` entry whose
provider isn't registered falls through to the rules, and the report says so).
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path

from agents.synthesis.investigator.agent import _OLLAMA_MODEL as _INVESTIGATOR_MODEL
from core.routing.router import NoProviderError, Router
from schemas.evidence import DataClass

_PACKAGE = "agentic-target-evidence"
_REPO_ROOT = Path(__file__).resolve().parents[4]

# LLM stages in pipeline order, keyed by the task each agent passes to Router.select,
# with the label shown in the report's "Run information" table.
REASONING_STAGES: dict[str, str] = {
    "screening": "Evidence screening",
    "claim_extraction": "Claim extraction",
    "source_quality": "Source-quality assessment",
    "genetics_lens": "Genetics lens",
    "biology_lens": "Biology lens",
    "safety_lens": "Safety lens",
    "clinical_lens": "Clinical lens",
    "commercial_lens": "Commercial lens",
    "regulatory_lens": "Regulatory lens",
    "gap_detection": "Gap detection",
    "experiment": "Experiment design & suitability",
    "critic": "Critic",
    "critic_verdict_qa": "Critic verdict QA",
    "reviewer": "Reviewer",
    "investigator": "Investigator",
}

# Steps that shaped a single lens's input, rendered in that lens report's footer.
_LENS_INPUT_STAGES = ("screening", "claim_extraction", "source_quality")


@cache
def short_version() -> str:
    """Installed package version, e.g. ``0.1.4`` — the one-line header stamp."""
    try:
        return importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@cache
def build_id() -> str:
    """``git describe`` of the checkout (e.g. ``v0.1.4-12-g846dda7-dirty``), "" outside one.

    Pins the exact commit and flags uncommitted changes, which the version alone can't.
    """
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


@dataclass(frozen=True)
class Provenance:
    policy: str
    stage_models: dict[str, str] = field(default_factory=dict)  # stage → "provider/model"

    def model_for(self, stage: str) -> str:
        return self.stage_models.get(stage, "")


def version_line() -> str:
    """Short header stamp; the full build and models go in the run-information footer."""
    return f"**Version:** {short_version()}"


def run_info_md(
    generated_at: datetime,
    provenance: Provenance | None,
    stages: tuple[str, ...] | None = None,
) -> str:
    """Closing "Run information" section: when, which build, which model ran each step.

    ``stages`` restricts the model table (e.g. a lens report lists only the steps that
    fed that lens); None lists every LLM step of the run.
    """
    build = build_id()
    lines = [
        "## Run Information",
        "",
        f"- **Generated:** {generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **Version:** {_PACKAGE} {short_version()}" + (f" (`{build}`)" if build else ""),
    ]
    if provenance is None:
        return "\n".join(lines) + "\n"
    lines += [f"- **Routing policy:** `{provenance.policy}`", ""]
    rows = [
        f"| {REASONING_STAGES.get(stage, stage)} | `{model}` |"
        for stage in (stages or tuple(REASONING_STAGES))
        if (model := provenance.model_for(stage))
    ]
    if rows:
        lines += ["| Step | Model |", "|---|---|", *rows, ""]
    lines.append(
        "_Retrieval, scoring and report rendering are deterministic (no LLM). "
        "Data classified as sensitive is always routed to the local model._"
    )
    return "\n".join(lines) + "\n"


def lens_stages(lens: str) -> tuple[str, ...]:
    return (*_LENS_INPUT_STAGES, f"{lens}_lens")


def collect_provenance(router: Router) -> Provenance:
    """Resolve every LLM stage's model under the router's active policy.

    Uses NON_SENSITIVE: SENSITIVE data is always pinned to the local provider, so
    this is the routing that applies to the public evidence the reports are built on.
    """
    stage_models: dict[str, str] = {}
    for stage in REASONING_STAGES:
        if stage == "investigator":
            # The investigator drives its own ChatOllama loop outside the router.
            stage_models[stage] = f"ollama/{_INVESTIGATOR_MODEL}"
            continue
        try:
            provider, model = router.select(DataClass.NON_SENSITIVE, stage)
        except NoProviderError:
            continue
        stage_models[stage] = f"{provider.name}/{model}"
    return Provenance(policy=router.policy_name, stage_models=stage_models)
