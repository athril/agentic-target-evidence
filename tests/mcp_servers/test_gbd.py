# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the GBD (Global Burden of Disease, IHME) MCP tools.

There is no bundled GBD data (non-commercial license, operator-provided
extract) — these tests point GBD_DATA_PATH at a small synthetic CSV via
tmp_path, mirroring the SCImago fixture-index pattern in test_scimago.py.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import mcp_servers.gbd.tools as gbd_tools
from mcp_servers.gbd.tools import (
    GBDBundle,
    _normalize_name,
    get_disease_burden,
    reload_gbd_crosswalk,
)

_FIELDS = [
    "cause_id",
    "cause_name",
    "measure_name",
    "metric_name",
    "location_name",
    "year",
    "val",
    "upper",
    "lower",
]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(
    cause_id: str = "587",
    cause_name: str = "Type 2 diabetes mellitus",
    measure: str = "Prevalence",
    metric: str = "Number",
    location: str = "Global",
    year: str = "2021",
    val: str = "529000000",
    upper: str = "560000000",
    lower: str = "500000000",
) -> dict[str, str]:
    return {
        "cause_id": cause_id,
        "cause_name": cause_name,
        "measure_name": measure,
        "metric_name": metric,
        "location_name": location,
        "year": year,
        "val": val,
        "upper": upper,
        "lower": lower,
    }


@pytest.fixture(autouse=True)
def _fixture_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # GBD is gated behind GBD_ENABLED (non-commercial license, off by default);
    # enable it so these tests exercise the lookup path. The disabled path has
    # its own dedicated test below.
    monkeypatch.setenv("GBD_ENABLED", "true")
    csv_path = tmp_path / "gbd_extract.csv"
    monkeypatch.setenv("GBD_DATA_PATH", str(csv_path))
    _write_csv(
        csv_path,
        [
            _row(),
            _row(year="2020", val="510000000"),  # older year, should be superseded
            _row(location="United States", val="37000000"),  # non-Global, should be skipped
            _row(cause_id="999", cause_name="Some Other Cause"),
        ],
    )
    gbd_tools._index = None
    gbd_tools._index_by_id = None
    gbd_tools._index_mtime = None
    gbd_tools._index_path = None
    reload_gbd_crosswalk()
    yield
    gbd_tools._index = None
    gbd_tools._index_by_id = None
    gbd_tools._index_mtime = None
    gbd_tools._index_path = None
    reload_gbd_crosswalk()


def test_normalize_name_collapses_punctuation_and_case():
    assert _normalize_name("Type 2 Diabetes Mellitus") == _normalize_name(
        "type-2 diabetes, mellitus"
    )


async def test_get_disease_burden_disabled_returns_empty_bundle(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GBD_ENABLED", "false")
    bundle = await get_disease_burden("Type 2 diabetes mellitus")
    assert isinstance(bundle, GBDBundle)
    assert bundle.mapping == "none"
    assert bundle.records == []
    assert bundle.text == ""


async def test_get_disease_burden_no_data_path_returns_empty_bundle(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GBD_DATA_PATH", "")
    bundle = await get_disease_burden("Type 2 diabetes mellitus")
    assert bundle.mapping == "none"


async def test_get_disease_burden_exact_name_match():
    bundle = await get_disease_burden("Type 2 diabetes mellitus")
    assert bundle.mapping == "exact"
    assert bundle.cause_name == "Type 2 diabetes mellitus"
    assert bundle.total == 1
    assert bundle.records[0].location == "Global"
    assert bundle.records[0].year == 2021
    assert bundle.records[0].value == 529000000.0


async def test_get_disease_burden_prefers_global_and_latest_year():
    bundle = await get_disease_burden("type 2 diabetes MELLITUS")  # case/whitespace-insensitive
    assert bundle.mapping == "exact"
    assert len(bundle.records) == 1
    assert bundle.records[0].year == 2021
    assert bundle.records[0].location == "Global"


async def test_get_disease_burden_no_match_returns_empty_bundle():
    bundle = await get_disease_burden("Completely unrelated disease")
    assert bundle.mapping == "none"
    assert bundle.records == []


async def test_get_disease_burden_crosswalk_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    crosswalk_path = tmp_path / "crosswalk.yaml"
    crosswalk_path.write_text(
        "crosswalk:\n  MONDO:0005148: '587'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gbd_tools, "_CROSSWALK_PATH", crosswalk_path)
    reload_gbd_crosswalk()

    bundle = await get_disease_burden(
        "a name not present in cause_name at all", disease_id="MONDO:0005148"
    )
    assert bundle.mapping == "crosswalk"
    assert bundle.cause_name == "Type 2 diabetes mellitus"
    assert bundle.total == 1


async def test_get_disease_burden_text_formatting_number_metric():
    bundle = await get_disease_burden("Type 2 diabetes mellitus")
    assert "Type 2 diabetes mellitus (GBD)" in bundle.text
    assert "529,000,000 cases" in bundle.text
    assert "Global, 2021" in bundle.text


async def test_get_disease_burden_text_formatting_rate_metric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    csv_path = tmp_path / "gbd_rate.csv"
    monkeypatch.setenv("GBD_DATA_PATH", str(csv_path))
    _write_csv(csv_path, [_row(metric="Rate", val="6200")])
    gbd_tools._index = None
    gbd_tools._index_by_id = None
    gbd_tools._index_mtime = None

    bundle = await get_disease_burden("Type 2 diabetes mellitus")
    assert "rate 6,200 per 100k" in bundle.text
