#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prepare the report for a reader who did not write it.

Usage:
    run scientific_review.py <run_dir>

Writes ``review_input.md``: the body as the reader will see it, with its tables filled in and
no figures. The reader follows references/scientific_review.md and records what they find in
``scientific_review.json``, which the release gate reads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_blocks import expand  # noqa: E402
from profile_claims import REVIEW_INPUT  # noqa: E402
from profile_schema import load_manifest, load_sources  # noqa: E402
from render import drop_generated_sections, substitute_anchors  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("--draft", default="draft.md")
    a = p.parse_args()
    m = load_manifest(a.run_dir)
    body = drop_generated_sections((a.run_dir / a.draft).read_text())
    body, _notes = expand(
        body, a.run_dir, m.gene, set(load_sources(a.run_dir)), figures=False, disease=m.disease
    )
    out = a.run_dir / REVIEW_INPUT
    out.write_text(substitute_anchors(body, a.run_dir))
    print(out)


if __name__ == "__main__":
    main()
