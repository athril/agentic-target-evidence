# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Build the bundled SJR journal-rank index from a SCImago data file.

SCImago Journal & Country Rank (scimagojr.com) is the de-facto standard for
journal prestige (SJR score + quartile), but its own export endpoint sits
behind a Cloudflare JS challenge that blocks any non-browser client. The
`sjrdata` project (https://github.com/ikashnitsky/sjrdata, MIT-licensed
packaging) mirrors SCImago's own "freely available" data as a flat file, so
we build the bundled index from that mirror instead of scraping the live site.

Output is a gzipped JSON index keyed by normalized ISSN (8-digit, no hyphen)
and by normalized journal title, consumed by mcp_servers.scimago.tools at
runtime with no network access. Re-run this script yearly to refresh the
bundled data; it never runs as part of the application itself.

Usage:
    uv run --with pyarrow scripts/build_scimago_index.py \
        --input /path/to/sjr_journals-<year>.parquet --year 2025

    # or let it fetch the mirror directly:
    uv run --with pyarrow --with httpx scripts/build_scimago_index.py --year 2025
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import tempfile
from pathlib import Path

_MIRROR_URL = (
    "https://raw.githubusercontent.com/ikashnitsky/sjrdata/master/"
    "data-raw/sjr-journal/sjr_journals-2026.parquet"
)
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "mcp_servers" / "scimago" / "data"
_ISSN_RE = re.compile(r"^\d{7}[\dX]$")

# Type preference when an ISSN or title collides across rows (rare; mostly
# placeholder "-" ISSNs on conference proceedings) — keep the journal entry.
_TYPE_RANK = {"journal": 0, "trade journal": 1, "book series": 2, "conference and proceedings": 3}


def _normalize_issn(raw: str) -> str | None:
    code = raw.strip().replace("-", "").upper()
    return code if _ISSN_RE.match(code) else None


def _normalize_title(raw: str) -> str:
    title = raw.strip().lower()
    title = re.sub(r"^the\s+", "", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _fetch_mirror(dest: Path) -> None:
    import httpx

    print(f"Downloading {_MIRROR_URL} ...", file=sys.stderr)
    with httpx.stream("GET", _MIRROR_URL, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(65536):
                f.write(chunk)


def build_index(parquet_path: Path, year: int) -> dict:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    table = table.filter(pc.equal(table.column("year"), float(year)))
    if table.num_rows == 0:
        raise SystemExit(f"No rows found for year={year} in {parquet_path}")

    rows = table.to_pylist()

    by_issn: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    for row in rows:
        record = {
            "title": row["title"],
            "sjr": row["sjr"],
            "sjr_best_quartile": row["sjr_best_quartile"],
            "type": row["type"],
        }
        rank = _TYPE_RANK.get(row["type"], 9)

        for code in (row["issn"] or "").split(","):
            issn = _normalize_issn(code)
            if issn is None:
                continue
            existing = by_issn.get(issn)
            if existing is None or rank < existing["_rank"]:
                by_issn[issn] = {**record, "_rank": rank}

        norm_title = _normalize_title(row["title"])
        if norm_title:
            existing = by_title.get(norm_title)
            if existing is None or rank < existing["_rank"]:
                by_title[norm_title] = {**record, "_rank": rank}

    for table_ in (by_issn, by_title):
        for rec in table_.values():
            rec.pop("_rank", None)

    return {"year": year, "by_issn": by_issn, "by_title": by_title}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Local sjr_journals-*.parquet path")
    parser.add_argument("--year", type=int, required=True, help="SJR data year to extract")
    args = parser.parse_args()

    if args.input:
        parquet_path = args.input
    else:
        tmp = Path(tempfile.gettempdir()) / "sjr_journals_mirror.parquet"
        _fetch_mirror(tmp)
        parquet_path = tmp

    index = build_index(parquet_path, args.year)
    print(
        f"Built index: {len(index['by_issn'])} ISSN keys, "
        f"{len(index['by_title'])} title keys (year={args.year})",
        file=sys.stderr,
    )

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / f"scimago_{args.year}.json.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(index, f, separators=(",", ":"))
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
