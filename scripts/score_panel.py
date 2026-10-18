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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.checkpoint import get_checkpointer  # noqa: E402
from services.evaluation.panel import (  # noqa: E402
    Observed,
    load_expectations,
    render_markdown,
    score_panel,
)


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


async def _observed(rows: list[dict[str, str]]) -> list[Observed]:
    out: list[Observed] = []
    async with get_checkpointer() as cp:
        for row in rows:
            tup = await cp.aget_tuple({"configurable": {"thread_id": row["thread_id"]}})
            if tup is None:
                continue
            state = tup.checkpoint["channel_values"]
            results = list(state.get("experiment_results") or [])
            first = results[0] if results else {}
            out.append(
                Observed(
                    gene=row["gene"],
                    disease=row["disease"],
                    direction=row["direction"],
                    band=str(first.get("band") or ""),
                    score=first.get("score"),
                    lenses={
                        str(_field(lv, "lens")): str(_field(lv, "overall_verdict"))
                        for lv in state.get("lens_verdicts") or []
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
