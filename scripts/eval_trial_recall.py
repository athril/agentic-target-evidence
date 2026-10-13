# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Landmark-trial recall of the clinical-trial retrieval (live registry calls).

Runs services.retrieval.clinical_trial.collect_trial_records for every pair in
tests/fixtures/landmark_trials.yaml and reports which landmark trials were found.

    uv run python scripts/eval_trial_recall.py [--out results/evals/trial_recall.md]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.retrieval.clinical_trial import collect_trial_records  # noqa: E402

_FIXTURE = Path("tests/fixtures/landmark_trials.yaml")


async def _pair(p: dict) -> tuple[str, int, int, list[str], int]:
    records, matched_by = await collect_trial_records(
        p["gene"], p["disease"], gene_id=p.get("gene_id", "")
    )
    found = {r.nct_id for r in records}
    expected = p["trials"]
    missed = [f"{n} {expected[n]}" for n in expected if n not in found]
    return (
        f"{p['gene']} × {p['disease']}",
        len(expected) - len(missed),
        len(expected),
        missed,
        len(records),
    )


async def main(out: Path | None) -> int:
    pairs = yaml.safe_load(_FIXTURE.read_text())["pairs"]
    rows = []
    for p in pairs:  # sequential: the registry rate-limits parallel bursts
        rows.append(await _pair(p))
    hit = sum(r[1] for r in rows)
    total = sum(r[2] for r in rows)
    lines = [
        f"# Landmark-trial recall — {date.today().isoformat()}",
        "",
        f"**Overall: {hit}/{total} ({hit / total:.0%})**",
        "",
        "| Pair | Found | Trials retrieved | Missed |",
        "|---|---|---|---|",
    ]
    for name, h, t, missed, n in rows:
        lines.append(f"| {name} | {h}/{t} | {n} | {'; '.join(missed) or '—'} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return 0 if hit == total else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    raise SystemExit(asyncio.run(main(ap.parse_args().out)))
