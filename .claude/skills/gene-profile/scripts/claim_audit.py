#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""List the blocks that still need an audit against their cited abstracts.

Usage:
    run claim_audit.py <run_dir>

Writes ``audit_input.json``: each block citing a paper that has no current verdict in
``claim_audit.json``, with the cited abstracts and nothing else. A reader who did not write
the draft judges each one (references/claim_audit.md) and adds the verdicts to
``claim_audit.json``. Changing a block makes its verdict stale. Verdicts recorded since the
last run are dated here.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_profile import audit_items  # noqa: E402
from profile_claims import AUDIT_FILE, AUDIT_INPUT, stamp_verdicts  # noqa: E402
from profile_writer import load_writer_file  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("--draft", default="draft.md")
    a = p.parse_args()
    items = audit_items(a.run_dir, (a.run_dir / a.draft).read_text())
    done = load_writer_file(a.run_dir, AUDIT_FILE) or []
    if stamp_verdicts(done, date.today().isoformat()):
        (a.run_dir / AUDIT_FILE).write_text(json.dumps(done, indent=2, ensure_ascii=False))
    current = {str(e.get("id")) for e in done if isinstance(e, dict)}
    pending = [i for i in items if i["id"] not in current]
    (a.run_dir / AUDIT_INPUT).write_text(json.dumps(pending, indent=2, ensure_ascii=False))
    print(f"{len(items)} blocks · {len(pending)} to audit · {a.run_dir / AUDIT_INPUT}")


if __name__ == "__main__":
    main()
