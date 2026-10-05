# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Score gene-dossier runs against the deterministic `checks` in evals.json.

Usage (from the repo root):
    uv run python .claude/skills/gene-dossier/evals/score.py \\
        trpc6-fsgs-standard=results/dossiers/TRPC6_2026-10-04 \\
        pcsk9-gene-only=results/dossiers/PCSK9_2026-10-04 \\
        ambiguous-alias=- [--out evals/baseline.json]

Cases whose checks need no run directory (e.g. `gather_exit`) take "-". Cases not listed are
reported as not run. Judged `expectations` are not scored here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "scripts"))

from dossier_schema import _MISSING, get_path, load_manifest, load_raw, load_sources  # noqa: E402

_GATHER = _HERE.parent / "scripts" / "gather.py"


def _section(report: str, title: str) -> str:
    m = re.search(
        rf"^## (?:\d+[.)]?\s+)?{re.escape(title)}\s*$(.*?)(?=^## |\Z)", report, re.M | re.S
    )
    return m.group(1) if m else ""


def run_check(check: dict[str, Any], run: Path | None, gene: str) -> tuple[bool, str]:
    t = check["type"]
    if t == "gather_exit":
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(_GATHER), gene, "--depth", "quick", "--out", tmp],
                capture_output=True,
                text=True,
                timeout=600,
            )
        return proc.returncode == check["code"], f"exit {proc.returncode}"
    if run is None:
        return False, "no run directory"

    def report() -> str:
        path = run / f"{gene}-report.md"
        return path.read_text() if path.exists() else ""

    def check_report() -> dict[str, Any]:
        path = run / "check_report.json"
        return json.loads(path.read_text()) if path.exists() else {}

    if t == "check_ok":
        ok = check_report().get("ok") is True
        return ok, f"{len(check_report().get('errors', []))} errors"
    if t == "citation_coverage_min":
        cov = check_report().get("stats", {}).get("citation_coverage", 0.0)
        return cov >= check["value"], f"coverage {cov}"
    if t == "file_exists":
        p = run / check["path"].replace("{GENE}", gene)
        return p.exists(), p.name
    if t in ("section_present", "section_absent"):
        present = bool(
            re.search(rf"^## (?:\d+[.)]?\s+)?{re.escape(check['title'])}\s*$", report(), re.M)
        )
        return present == (t == "section_present"), f"present={present}"
    if t in ("report_matches", "report_not_matches"):
        hit = re.search(check["pattern"], report(), re.M)
        found = hit.group(0) if hit else ""
        return bool(hit) == (t == "report_matches"), f"match={found[:60]!r}"
    if t == "gaps_mention":
        hit = re.search(check["pattern"], _section(report(), "Evidence gaps & conflicts"))
        return bool(hit), f"in gaps={bool(hit)}"
    if t == "snapshot_max_lines":
        draft = (run / "draft.md").read_text() if (run / "draft.md").exists() else ""
        n = len(_section(draft, "Snapshot").strip().splitlines())
        return n <= check["value"], f"{n} lines"
    if t == "source_status":
        entry = next((e for e in load_manifest(run).entries if e.key == check["key"]), None)
        status = entry.status if entry else "missing"
        return status in check["status"], status
    if t == "identity_note_matches":
        note = load_manifest(run).identity_note
        return bool(re.search(check["pattern"], note)), note[:80]
    if t == "manifest_depth":
        depth = load_manifest(run).depth
        return depth == check["value"], depth
    if t == "raw_path_matches":
        rec = load_raw(run, check["key"])
        value = get_path(rec.data, check["path"]) if rec else _MISSING
        text = "" if value is _MISSING else str(value)
        return bool(re.search(check["pattern"], text)), text[:80]
    if t == "max_article_sources":
        cited = check_report().get("stats", {}).get("sources_cited_by_kind", {}).get("article", 0)
        return cited <= check["value"], f"{cited} articles cited"
    if t == "max_article_sources_available":
        n = sum(1 for s in load_sources(run).values() if s.kind == "article")
        return n <= check["value"], f"{n} available"
    return False, f"unknown check type {t!r}"


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("runs", nargs="+", help="case_id=run_dir (or case_id=- for no run dir)")
    p.add_argument("--evals", type=Path, default=_HERE / "evals.json")
    p.add_argument("--out", type=Path, help="write the scored results as JSON")
    a = p.parse_args()

    spec = json.loads(a.evals.read_text())
    runs = dict(r.split("=", 1) for r in a.runs)
    results: list[dict[str, Any]] = []
    total = passed = 0
    for case in spec["evals"]:
        if case["id"] not in runs:
            print(f"-- {case['id']}: not run")
            continue
        run = None if runs[case["id"]] == "-" else Path(runs[case["id"]])
        rows = []
        for check in case.get("checks", []):
            ok, detail = run_check(check, run, case["gene"])
            rows.append({"check": check, "pass": ok, "detail": detail})
        n_pass = sum(r["pass"] for r in rows)
        total += len(rows)
        passed += n_pass
        print(f"{'OK' if n_pass == len(rows) else '!!'} {case['id']}: {n_pass}/{len(rows)}")
        for r in rows:
            if not r["pass"]:
                print(f"     FAIL {r['check']} → {r['detail']}")
        results.append(
            {
                "id": case["id"],
                "run_dir": runs[case["id"]],
                "passed": n_pass,
                "total": len(rows),
                "checks": rows,
            }
        )
    score = round(passed / total, 3) if total else 0.0
    print(f"\nscore: {passed}/{total} = {score}")
    if a.out:
        a.out.write_text(
            json.dumps(
                {
                    "scored_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "score": score,
                    "passed": passed,
                    "total": total,
                    "cases": results,
                },
                indent=2,
            )
        )
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
