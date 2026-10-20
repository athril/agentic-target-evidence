# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Score a benchmark-panel run against tests/fixtures/panel_expectations.yaml.

Reads the thread ids of a panel (results/panel/<timestamp>/summary.tsv, written by
scripts/run_panel.sh), loads each run's final lens verdicts and score from its LangGraph
checkpoint, and reports which bands and lens verdicts fall within the expectations.

    uv run python scripts/score_panel.py results/panel/<timestamp> [--out results/evals/panel.md]
        [--section held_out]

Needs DATABASE_URL (the checkpoint store), e.g. `set -a; . ./.env; set +a`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

# The checkpoints live in the database the runs wrote to: read DATABASE_URL from .env as
# run_analysis.py does, so the script works from a plain shell.
load_dotenv()

from core.checkpoint import get_checkpointer  # noqa: E402
from schemas.evidence import EvidenceType  # noqa: E402
from services.decision.suitability import load_scoring_config  # noqa: E402
from services.evaluation.panel import (  # noqa: E402
    Observed,
    axis_conflict_counts,
    flag_counts,
    load_expectations,
    render_markdown,
    score_panel,
    ungated,
    verdict_rules,
)

_LITERATURE = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)


async def _observed(rows: list[dict[str, str]]) -> list[Observed]:
    out: list[Observed] = []
    bands = load_scoring_config().bands
    async with get_checkpointer() as cp:
        for row in rows:
            tup = await cp.aget_tuple({"configurable": {"thread_id": row["thread_id"]}})
            if tup is None:
                continue
            state = tup.checkpoint["channel_values"]
            results = list(state.get("experiment_results") or [])
            first = results[0] if results else {}
            verdicts = [
                lv if isinstance(lv, dict) else lv.model_dump(mode="json")
                for lv in state.get("lens_verdicts") or []
            ]
            score, band = ungated(dict(first.get("score_breakdown") or {}), bands)
            guards, repairs = flag_counts(verdicts)
            quality = state.get("source_quality") or {}
            literature = [
                c
                for c in state.get("extracted_claims") or []
                if getattr(c, "evidence_type", None) in _LITERATURE
            ]
            scored = sum(
                1
                for c in literature
                if (quality.get(str(c.source_evidence_id)) or {}).get("sjr_score") is not None
            )
            out.append(
                Observed(
                    gene=row["gene"],
                    disease=row["disease"],
                    direction=row["direction"],
                    band=str(first.get("band") or ""),
                    score=first.get("score"),
                    lenses={str(lv.get("lens")): str(lv.get("overall_verdict")) for lv in verdicts},
                    ungated_score=score,
                    ungated_band=band,
                    computed_by_design=[
                        str(lv.get("lens"))
                        for lv in verdicts
                        if "design" in verdict_rules(list(lv.get("validation_flags") or []))
                    ],
                    rescued=[
                        str(lv.get("lens"))
                        for lv in verdicts
                        if "rescue" in verdict_rules(list(lv.get("validation_flags") or []))
                    ],
                    guard_flags=guards,
                    repairs=repairs,
                    literature_claims=len(literature),
                    literature_scored=scored,
                    axis_conflicts=axis_conflict_counts(verdicts),
                    degradations=[
                        str(d.get("component") or "")
                        for d in state.get("degradations") or []
                        if d.get("component")
                    ],
                    shown_inputs={
                        str(lv.get("lens")): dict(lv.get("shown_inputs") or {})
                        for lv in verdicts
                        if lv.get("shown_inputs")
                    },
                )
            )
    return out


async def main(panel_dir: Path, out: Path | None, section: str = "panel") -> int:
    with (panel_dir / "summary.tsv").open() as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t") if r.get("thread_id")]
    scores = score_panel(load_expectations(section), await _observed(rows))
    text = render_markdown(scores, f"{section.replace('_', '-').title()} score — {panel_dir.name}")
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("panel_dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--section",
        choices=["panel", "held_out"],
        default="panel",
        help="which expectations to score (the held-out pairs have bands only)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.panel_dir, args.out, args.section)))
