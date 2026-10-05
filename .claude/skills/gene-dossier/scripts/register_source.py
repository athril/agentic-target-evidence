# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add a citable source to a run's sources.json — metadata is always fetched, never typed.

Usage (from the repo root):
    S=.claude/skills/gene-dossier/scripts
    uv run python $S/register_source.py <run_dir> pmid 41616795 40455255
    uv run python $S/register_source.py <run_dir> nct NCT05213624
    uv run python $S/register_source.py <run_dir> web https://example.com/press --publisher "Acme Inc." [--tier 3]
    uv run python $S/register_source.py <run_dir> abstract 41616795     # print an abstract (read-only)

Prints the citation key(s) to use in draft.md, e.g. ``[@pmid:41616795]``.
A PMID / NCT ID that does not exist fails loudly and is NOT registered.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date
from html import unescape
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dossier_schema import Source, load_sources, save_sources  # noqa: E402

load_dotenv()

from mcp_servers.pubmed import tools as pubmed  # noqa: E402

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


async def pmid_sources(pmids: list[str]) -> list[Source]:
    d = await pubmed._rate_limited_get(
        f"{_EUTILS}/esummary.fcgi", {"db": "pubmed", "id": ",".join(pmids)}
    )
    out = []
    for pmid in pmids:
        s = d.get("result", {}).get(pmid)
        if not s or s.get("error") or not s.get("title"):
            raise SystemExit(f"PMID {pmid} not found in PubMed — not registered")
        year = (s.get("pubdate") or "")[:4]
        out.append(
            Source(
                key=f"pmid:{pmid}",
                kind="article",
                tier=1,
                title=s["title"],
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                accessed=date.today().isoformat(),
                authors=[a["name"] for a in s.get("authors", [])][:6],
                venue=s.get("source", ""),
                year=int(year) if year.isdigit() else None,
                pmid=pmid,
                doi=next((a["value"] for a in s.get("articleids", []) if a["idtype"] == "doi"), ""),
                retrieved_via="NCBI E-utilities esummary",
                verified=True,
            )
        )
    return out


async def nct_sources(ids: list[str]) -> list[Source]:
    out = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        for nct in ids:
            r = await c.get(
                f"https://clinicaltrials.gov/api/v2/studies/{nct}",
                params={
                    "fields": "protocolSection.identificationModule,"
                    "protocolSection.sponsorCollaboratorsModule.leadSponsor"
                },
            )
            if r.status_code != 200:
                raise SystemExit(f"{nct} not found on ClinicalTrials.gov (HTTP {r.status_code})")
            p = r.json()["protocolSection"]
            out.append(
                Source(
                    key=f"nct:{nct}",
                    kind="trial",
                    tier=2,
                    title=p["identificationModule"]["briefTitle"],
                    url=f"https://clinicaltrials.gov/study/{nct}",
                    accessed=date.today().isoformat(),
                    nct_id=nct,
                    venue=(p.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get(
                        "name", ""
                    ),
                    retrieved_via="ClinicalTrials.gov API v2",
                    verified=True,
                )
            )
    return out


async def web_source(url: str, publisher: str, tier: int, title: str | None) -> Source:
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 gene-dossier"}
    ) as c:
        r = await c.get(url)
    if r.status_code >= 400:
        raise SystemExit(f"{url} returned HTTP {r.status_code} — not registered")
    m = _TITLE_RE.search(r.text[:200_000])
    page_title = re.sub(r"\s+", " ", unescape(m.group(1))).strip() if m else ""
    slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^https?://(www\.)?", "", str(r.url)).lower()).strip(
        "-"
    )[:60]
    return Source(
        key=f"web:{slug}",
        kind="web",
        tier=tier,  # type: ignore[arg-type]
        title=page_title or title or str(r.url),
        url=str(r.url),
        accessed=date.today().isoformat(),
        venue=publisher,
        retrieved_via="HTTP GET (page title from response)",
        verified=bool(page_title or title),
    )


async def amain(a: argparse.Namespace) -> None:
    if a.kind == "abstract":
        for pmid in a.ids:
            ab = await pubmed.fetch_abstract(pmid)
            print(f"PMID {pmid} — {ab.title}\n{ab.abstract}\n")
        return
    if a.kind == "pmid":
        new = await pmid_sources(a.ids)
    elif a.kind == "nct":
        new = await nct_sources(a.ids)
    else:
        new = [await web_source(u, a.publisher or "", a.tier, a.title) for u in a.ids]
    sources = load_sources(a.run_dir)
    for s in new:
        sources[s.key] = s
        print(f"[@{s.key}]  {s.title[:100]}")
    save_sources(a.run_dir, sources)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("kind", choices=["pmid", "nct", "web", "abstract"])
    p.add_argument("ids", nargs="+")
    p.add_argument("--publisher", help="web: publishing organisation")
    p.add_argument("--title", help="web: fallback title if the page has no <title>")
    p.add_argument(
        "--tier",
        type=int,
        choices=[2, 3],
        default=3,
        help="web: 3 = company/press/news (default), 2 = regulator/agency page",
    )
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
