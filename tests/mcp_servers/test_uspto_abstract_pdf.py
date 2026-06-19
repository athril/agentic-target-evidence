# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for mcp_servers/uspto/abstract_pdf.py.

extract_text's OCR fallback depends on tesseract-ocr/poppler-utils, which are
not installed in CI (see Dockerfile vs. ci.yml) — pdf2image/pytesseract calls
are mocked at the module boundary rather than exercised for real.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.uspto.abstract_pdf import clean_abstract, extract_text, fetch_abstract_pdf

_DOCS_BASE = "https://api.uspto.gov/api/v1/patent/applications"


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USPTO_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests shouldn't pay the real proactive inter-request delay."""
    import mcp_servers.uspto.tools as tools_mod

    monkeypatch.setattr(
        tools_mod, "_ENDPOINT_REQUESTS_PER_SECOND", {"search": 1e6, "documents": 1e6}
    )
    monkeypatch.setattr(tools_mod, "_last_request_at", {})


# ---------------------------------------------------------------------------
# clean_abstract
# ---------------------------------------------------------------------------


def test_clean_abstract_strips_heading() -> None:
    text = "ABSTRACT\nA method for treating cancer."
    assert clean_abstract(text) == "A method for treating cancer."


def test_clean_abstract_strips_heading_with_disclosure_suffix() -> None:
    text = "ABSTRACT OF THE DISCLOSURE\nA method for treating cancer."
    assert clean_abstract(text) == "A method for treating cancer."


def test_clean_abstract_strips_docket_lines() -> None:
    text = "Docket No. 12345-US\nA method for treating cancer."
    assert clean_abstract(text) == "A method for treating cancer."


def test_clean_abstract_strips_page_markers() -> None:
    text = "A method for\n-1-\ntreating cancer.\n2"
    assert clean_abstract(text) == "A method for treating cancer."


def test_clean_abstract_collapses_whitespace_to_one_paragraph() -> None:
    text = "A method   for\ntreating\n\ncancer."
    assert clean_abstract(text) == "A method for treating cancer."


def test_clean_abstract_empty_input_returns_empty() -> None:
    assert clean_abstract("") == ""


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdf:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages

    def __enter__(self) -> _FakePdf:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def test_extract_text_uses_text_layer_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    import pdfplumber

    fake_pdf = _FakePdf([_FakePage("Hello world"), _FakePage("Second page")])
    monkeypatch.setattr(pdfplumber, "open", lambda _: fake_pdf)

    assert extract_text(b"fake-pdf-bytes") == "Hello world\nSecond page"


def test_extract_text_falls_back_to_ocr_when_text_layer_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pdf2image
    import pdfplumber
    import pytesseract

    fake_pdf = _FakePdf([_FakePage(""), _FakePage("   ")])
    monkeypatch.setattr(pdfplumber, "open", lambda _: fake_pdf)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", lambda data, dpi: ["img1", "img2"])
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image: f"OCR:{image}")

    assert extract_text(b"fake-pdf-bytes") == "OCR:img1\nOCR:img2"


def test_extract_text_falls_back_to_ocr_when_pdfplumber_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pdf2image
    import pdfplumber
    import pytesseract

    def _raise(_: object) -> None:
        raise ValueError("corrupt pdf")

    monkeypatch.setattr(pdfplumber, "open", _raise)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", lambda data, dpi: ["img1"])
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image: "OCR text")

    assert extract_text(b"fake-pdf-bytes") == "OCR text"


def test_extract_text_returns_empty_when_ocr_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import pdf2image
    import pdfplumber

    fake_pdf = _FakePdf([_FakePage("")])
    monkeypatch.setattr(pdfplumber, "open", lambda _: fake_pdf)

    def _raise(data: bytes, dpi: int) -> None:
        raise OSError("poppler not installed")

    monkeypatch.setattr(pdf2image, "convert_from_bytes", _raise)

    assert extract_text(b"fake-pdf-bytes") == ""


# ---------------------------------------------------------------------------
# fetch_abstract_pdf
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_abstract_pdf_downloads_and_cleans(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_servers.uspto.abstract_pdf as abstract_pdf_mod

    respx.get(f"{_DOCS_BASE}/16574407/documents").mock(
        return_value=httpx.Response(
            200,
            json={
                "documentBag": [
                    {
                        "documentCode": "ABST",
                        "downloadOptionBag": [
                            {
                                "mimeTypeIdentifier": "PDF",
                                "downloadUrl": f"{_DOCS_BASE}/16574407/DOC1.pdf",
                            }
                        ],
                    }
                ]
            },
        )
    )
    respx.get(f"{_DOCS_BASE}/16574407/DOC1.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-fake")
    )
    monkeypatch.setattr(
        abstract_pdf_mod, "extract_text", lambda _: "ABSTRACT\nSome cancer therapy text."
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "16574407", "test-key")

    assert result == "Some cancer therapy text."


@respx.mock
async def test_fetch_abstract_pdf_falls_back_to_spec_when_no_abst() -> None:
    respx.get(f"{_DOCS_BASE}/16574407/documents").mock(
        return_value=httpx.Response(
            200,
            json={
                "documentBag": [
                    {
                        "documentCode": "SPEC",
                        "downloadOptionBag": [
                            {
                                "mimeTypeIdentifier": "PDF",
                                "downloadUrl": f"{_DOCS_BASE}/16574407/DOC2.pdf",
                            }
                        ],
                    }
                ]
            },
        )
    )
    respx.get(f"{_DOCS_BASE}/16574407/DOC2.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-fake")
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "16574407", "test-key")

    # No extract_text stub: real pdfplumber finds no text layer in "%PDF-fake"
    # and OCR (unmocked, no real PDF) raises internally — fetch_abstract_pdf
    # must swallow that and still return a string, not raise.
    assert result == ""


@respx.mock
async def test_fetch_abstract_pdf_no_abst_or_spec_returns_empty() -> None:
    respx.get(f"{_DOCS_BASE}/16574407/documents").mock(
        return_value=httpx.Response(200, json={"documentBag": [{"documentCode": "NOA"}]})
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "16574407", "test-key")

    assert result == ""


@respx.mock
async def test_fetch_abstract_pdf_no_pdf_download_option_returns_empty() -> None:
    respx.get(f"{_DOCS_BASE}/16574407/documents").mock(
        return_value=httpx.Response(
            200,
            json={
                "documentBag": [
                    {
                        "documentCode": "ABST",
                        "downloadOptionBag": [{"mimeTypeIdentifier": "MS_WORD"}],
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "16574407", "test-key")

    assert result == ""


@respx.mock
async def test_fetch_abstract_pdf_empty_app_number_returns_empty_without_calling_api() -> None:
    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "", "test-key")

    assert result == ""


@respx.mock
async def test_fetch_abstract_pdf_documents_list_failure_returns_empty() -> None:
    respx.get(f"{_DOCS_BASE}/16574407/documents").mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "16574407", "test-key")

    assert result == ""


@respx.mock
async def test_fetch_abstract_pdf_retries_once_on_download_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_servers.uspto.abstract_pdf as abstract_pdf_mod

    docs_route = respx.get(f"{_DOCS_BASE}/16574407/documents").mock(
        return_value=httpx.Response(
            200,
            json={
                "documentBag": [
                    {
                        "documentCode": "ABST",
                        "downloadOptionBag": [
                            {
                                "mimeTypeIdentifier": "PDF",
                                "downloadUrl": f"{_DOCS_BASE}/16574407/DOC1.pdf",
                            }
                        ],
                    }
                ]
            },
        )
    )
    responses = iter([httpx.Response(404), httpx.Response(200, content=b"%PDF-fake")])
    respx.get(f"{_DOCS_BASE}/16574407/DOC1.pdf").mock(side_effect=lambda req: next(responses))
    monkeypatch.setattr(abstract_pdf_mod, "extract_text", lambda _: "Recovered abstract text.")

    async with httpx.AsyncClient() as client:
        result = await fetch_abstract_pdf(client, "16574407", "test-key")

    assert result == "Recovered abstract text."
    assert docs_route.call_count == 2
