# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Abstract extraction from USPTO Documents API PDFs.

USPTO ODP search returns metadata only — no abstract text. The only
USPTO-native abstract source is the Documents API's ABST PDF, which is
frequently a scanned image (OCR is the norm, not a rare fallback). This
module fetches that PDF and extracts text: pdfplumber's text layer first,
falling back to tesseract OCR via pdf2image when the layer is empty.
"""

from __future__ import annotations

import io
import logging
import re

import httpx

_DOCUMENT_ABST_CODE = "ABST"
_DOCUMENT_SPEC_CODE = "SPEC"
_PDF_MIME = "PDF"
_OCR_DPI = 300
# Initial fetch + 1 retry. A retry re-lists the documents endpoint rather than
# re-using the download URL, since the signed URL expires ~5s after redirect.
_MAX_ABSTRACT_ATTEMPTS = 2

_HEADING_RE = re.compile(r"(?i)^abstract(\s+of\s+the\s+disclosure)?\s*:?$")
_DOCKET_RE = re.compile(r"(?i)^.*docket\s+no\.?.*$")
_PAGE_MARKER_RE = re.compile(r"^-?\d+-?$")

logger = logging.getLogger(__name__)


async def fetch_abstract_pdf(client: httpx.AsyncClient, app_num: str, key: str) -> str:
    """Fetch and extract the abstract PDF for a USPTO application.

    Looks up the ABST document (falling back to SPEC if absent), downloads
    its PDF option, and runs it through extract_text + clean_abstract.
    Returns "" on any failure — abstract retrieval must never crash the
    surrounding patent search.
    """
    if not app_num:
        return ""
    # Local import: tools.py imports fetch_abstract_pdf at module scope, so a
    # top-level import here would create a circular import.
    from mcp_servers.uspto.tools import _ODP_DOCS_URL, _odp_get

    docs_url = _ODP_DOCS_URL.format(app_num=app_num)
    for attempt in range(_MAX_ABSTRACT_ATTEMPTS):
        try:
            resp = await _odp_get(client, docs_url, key, endpoint="documents")
            if resp.status_code != 200:
                return ""

            docs = resp.json().get("documentBag") or []
            doc = next(
                (d for d in docs if d.get("documentCode") == _DOCUMENT_ABST_CODE),
                next((d for d in docs if d.get("documentCode") == _DOCUMENT_SPEC_CODE), None),
            )
            if not doc:
                return ""

            options = doc.get("downloadOptionBag") or []
            pdf_opt = next((o for o in options if o.get("mimeTypeIdentifier") == _PDF_MIME), None)
            if not pdf_opt:
                return ""

            # Download immediately — signed URL expires within 5s of redirect.
            dl_resp = await client.get(
                pdf_opt["downloadUrl"], headers={"X-API-KEY": key}, follow_redirects=True
            )
            if dl_resp.status_code == 200:
                return clean_abstract(extract_text(dl_resp.content))

            if attempt < _MAX_ABSTRACT_ATTEMPTS - 1:
                logger.debug(
                    "Abstract PDF download failed for %s: HTTP %s (retrying)",
                    app_num,
                    dl_resp.status_code,
                )
                continue
            logger.debug(
                "Abstract PDF download failed for %s: HTTP %s", app_num, dl_resp.status_code
            )
            return ""
        except Exception as exc:
            if attempt < _MAX_ABSTRACT_ATTEMPTS - 1:
                logger.debug("Abstract PDF fetch failed for %s (retrying): %r", app_num, exc)
                continue
            logger.debug("Abstract PDF fetch failed for %s: %r", app_num, exc)
            return ""
    return ""


def extract_text(pdf_bytes: bytes) -> str:
    """Extract text from a PDF: text layer first, OCR fallback if empty."""
    import pdfplumber

    parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    parts.append(page_text)
    except Exception as exc:
        logger.debug("pdfplumber text-layer extraction failed: %r", exc)

    text = "\n".join(parts).strip()
    if text:
        return text
    return _ocr_pdf(pdf_bytes)


def _ocr_pdf(pdf_bytes: bytes) -> str:
    """Rasterize each page and OCR it — the path taken for scanned PDFs."""
    import pdf2image
    import pytesseract

    try:
        images = pdf2image.convert_from_bytes(pdf_bytes, dpi=_OCR_DPI)
    except Exception as exc:
        logger.debug("pdf2image rasterization failed: %r", exc)
        return ""

    parts: list[str] = []
    for image in images:
        try:
            parts.append(pytesseract.image_to_string(image))
        except Exception as exc:
            logger.debug("pytesseract OCR failed: %r", exc)
    return "\n".join(parts).strip()


def clean_abstract(text: str) -> str:
    """Strip headings/docket lines/page markers and collapse to one paragraph."""
    if not text:
        return ""

    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            _HEADING_RE.match(stripped)
            or _DOCKET_RE.match(stripped)
            or _PAGE_MARKER_RE.match(stripped)
        ):
            continue
        kept.append(stripped)

    return " ".join(" ".join(kept).split())
