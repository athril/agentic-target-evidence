# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Download and OCR-extract USPTO abstract PDFs for patent evidence files.

Patent markdown files under results/data/<gene>/<disease_id>/<direction>/patents/*.md carry
two different identifiers: a "Patent ID" (often a granted patent number that
may not correspond to anything filed) and a "USPTO Patent Center" link whose
trailing path segment is the actual application number. The ODP Documents API
keys off the application number, not the Patent ID — always read it from the
USPTO Patent Center link.

Many pre-2010 USPTO filings have no text layer (scanned images), so this
falls back to OCR (pdf2image + pytesseract) when pdfplumber extracts nothing.

Usage:
    USPTO_API_KEY=... uv run --with pdfplumber --with pytesseract \
        --with pdf2image --with httpx scripts/uspto_abstract_ocr.py \
        results/data/PNPLA3/EFO_1001249/inhibit/patents/10774333.md
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from pathlib import Path

import httpx
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes

_ODP_BASE = "https://api.uspto.gov/api/v1"
_USPTO_LINK_RE = re.compile(
    r"USPTO Patent Center:\*\*\s*https://patentcenter\.uspto\.gov/applications/\s*(\d+)"
)


def extract_app_number(markdown_text: str) -> str | None:
    match = _USPTO_LINK_RE.search(markdown_text)
    return match.group(1) if match else None


def api_key() -> str:
    key = os.environ.get("USPTO_API_KEY", "")
    if not key:
        raise RuntimeError("USPTO_API_KEY is not set")
    return key


def fetch_documents(client: httpx.Client, app_number: str, key: str) -> list[dict]:
    url = f"{_ODP_BASE}/patent/applications/{app_number}/documents"
    resp = client.get(url, headers={"X-API-KEY": key})
    resp.raise_for_status()
    return resp.json().get("documentBag") or []


def find_abstract_doc(documents: list[dict]) -> dict | None:
    return next((d for d in documents if d.get("documentCode") == "ABST"), None) or next(
        (d for d in documents if d.get("documentCode") == "SPEC"), None
    )


def download_pdf(
    client: httpx.Client, app_number: str, document_identifier: str, key: str
) -> bytes:
    url = f"{_ODP_BASE}/download/applications/{app_number}/{document_identifier}.pdf"
    resp = client.get(url, headers={"X-API-KEY": key}, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    if text.strip():
        return text
    images = convert_from_bytes(pdf_bytes, dpi=300)
    return "\n".join(pytesseract.image_to_string(image) for image in images)


def process_patent_file(path: Path, client: httpx.Client, key: str) -> str:
    app_number = extract_app_number(path.read_text())
    if not app_number:
        raise ValueError(f"no USPTO Patent Center application number found in {path}")
    documents = fetch_documents(client, app_number, key)
    abstract_doc = find_abstract_doc(documents)
    if not abstract_doc:
        raise ValueError(f"no ABST/SPEC document found for application {app_number}")
    pdf_bytes = download_pdf(client, app_number, abstract_doc["documentIdentifier"], key)
    return extract_text(pdf_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths", nargs="+", type=Path, help="patent markdown file(s) or directories to glob (*.md)"
    )
    args = parser.parse_args()

    files: list[Path] = []
    for path in args.paths:
        files.extend(sorted(path.rglob("*.md")) if path.is_dir() else [path])

    key = api_key()
    exit_code = 0
    with httpx.Client(timeout=30) as client:
        for path in files:
            try:
                text = process_patent_file(path, client, key)
            except Exception as exc:
                print(f"{path}: ERROR - {exc}", file=sys.stderr)
                exit_code = 1
                continue
            print(f"=== {path} ===")
            print(text)
            print()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
