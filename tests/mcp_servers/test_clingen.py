# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ClinGen gene validity MCP tools.

ClinGen's GraphQL API is gone; gene-disease validity now comes from a bulk
JSON-LD dataset (see mcp_servers/clingen/tools.py module docstring). These
tests fake that dataset as a small in-memory tar.gz and patch the module's
`_ensure_cached` boundary, mirroring the pattern used for DepMap's bulk CSVs
in test_depmap.py. HGNC/MONDO resolution (a separate, already-tested module)
is patched directly rather than re-mocking its HTTP calls.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.clingen.tools as clingen_tools
from core.exceptions import MCPToolError
from mcp_servers.clingen.tools import ClinGenBundle, get_clingen_validity
from mcp_servers.ontology.tools import HGNCResult, MondoResult


@pytest.fixture(autouse=True)
def _reset_module_caches():
    clingen_tools._index = None
    clingen_tools._index_mtime = None
    clingen_tools._mondo_label_cache = {}
    yield
    clingen_tools._index = None
    clingen_tools._index_mtime = None
    clingen_tools._mondo_label_cache = {}


def _make_record(
    hgnc_id: str,
    classification: str,
    mondo_curie: str,
    evaluated_date: str | None = "2022-03-15T00:00:00.000Z",
    moi_curie: str | None = None,
) -> dict:
    # The shape of the published bulk file: gene and terms as IRIs inside the proposition.
    proposition = {
        "subjectGene": f"https://identifiers.org/{hgnc_id.lower()}",
        "objectCondition": f"http://purl.obolibrary.org/obo/{mondo_curie.replace(':', '_')}",
    }
    if moi_curie:
        proposition["modeOfInheritanceQualifier"] = (
            f"http://purl.obolibrary.org/obo/{moi_curie.replace(':', '_')}"
        )
    return {
        "classification": classification,
        "proposition": proposition,
        "contributions": (
            [{"activityType": "Evaluated", "date": evaluated_date}] if evaluated_date else []
        ),
    }


def _make_tar(records: dict[str, dict]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    path = Path(name)
    with tarfile.open(path, "w:gz") as tf:
        for name, record in records.items():
            data = json.dumps(record).encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _patch_ensure_cached(tar_path: Path):
    return patch.object(clingen_tools, "_ensure_cached", AsyncMock(return_value=tar_path))


def _patch_hgnc(hgnc_id: str | None):
    if hgnc_id is None:
        return patch.object(
            clingen_tools, "resolve_hgnc_symbol", AsyncMock(side_effect=MCPToolError("not found"))
        )
    return patch.object(
        clingen_tools,
        "resolve_hgnc_symbol",
        AsyncMock(return_value=HGNCResult(symbol="TRPC6", hgnc_id=hgnc_id)),
    )


def _patch_mondo(labels: dict[str, str]):
    async def fake_resolve(curie: str) -> MondoResult:
        if curie not in labels:
            raise MCPToolError(f"No MONDO term found for '{curie}'")
        return MondoResult(mondo_id=curie, label=labels[curie])

    return patch.object(clingen_tools, "resolve_mondo_term", AsyncMock(side_effect=fake_resolve))


async def test_get_clingen_validity_returns_bundle() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:12338", "Definitive", "MONDO:0001085"),
            "b--1.0.0.json": _make_record("HGNC:12338", "Limited", "MONDO:0005511"),
        }
    )
    try:
        with (
            _patch_hgnc("HGNC:12338"),
            _patch_ensure_cached(tar_path),
            _patch_mondo(
                {
                    "MONDO:0001085": "focal segmental glomerulosclerosis",
                    "MONDO:0005511": "nephrotic syndrome",
                }
            ),
        ):
            bundle = await get_clingen_validity("TRPC6")

        assert isinstance(bundle, ClinGenBundle)
        assert bundle.gene_symbol == "TRPC6"
        assert bundle.total == 2
        assert len(bundle.associations) == 2
        # Strongest classification should sort first
        assert bundle.associations[0].classification == "Definitive"
        assert bundle.associations[0].disease_label == "focal segmental glomerulosclerosis"
        assert bundle.associations[0].hgnc_id == "HGNC:12338"
        assert bundle.associations[0].report_date == "2022-03-15"
        assert "Definitive" in bundle.text
        assert "TRPC6" in bundle.text
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_empty_when_gene_not_in_dataset() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:99999", "Definitive", "MONDO:0001085"),
        }
    )
    try:
        with _patch_hgnc("HGNC:12338"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("TRPC6")

        assert bundle.total == 0
        assert bundle.associations == []
        assert "No ClinGen" in bundle.text
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_empty_when_symbol_not_resolvable() -> None:
    with _patch_hgnc(None):
        bundle = await get_clingen_validity("UNKNOWN")

    assert bundle.total == 0
    assert bundle.associations == []
    assert "No ClinGen" in bundle.text


async def test_get_clingen_validity_raises_when_bulk_download_fails() -> None:
    with (
        _patch_hgnc("HGNC:12338"),
        patch.object(
            clingen_tools, "_ensure_cached", AsyncMock(side_effect=MCPToolError("HTTP 503"))
        ),
        pytest.raises(MCPToolError, match="HTTP 503"),
    ):
        await get_clingen_validity("TRPC6")


async def test_get_clingen_validity_sorts_by_classification_strength() -> None:
    """Definitive > Strong > Moderate > Limited in sort order."""
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:1", "Limited", "MONDO:0000001"),
            "b--1.0.0.json": _make_record("HGNC:1", "Strong", "MONDO:0000002"),
            "c--1.0.0.json": _make_record("HGNC:1", "Moderate", "MONDO:0000003"),
        }
    )
    try:
        with _patch_hgnc("HGNC:1"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("GENE1")

        classifications = [a.classification for a in bundle.associations]
        assert classifications == ["Strong", "Moderate", "Limited"]
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_normalizes_no_known_disease_relationship() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:1", "NoKnownDiseaseRelationship", "MONDO:0000001"),
        }
    )
    try:
        with _patch_hgnc("HGNC:1"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("GENE1")

        assert bundle.associations[0].classification == "No Known Disease Relationship"
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_parses_mode_of_inheritance() -> None:
    """proposition.qualifierModeOfInheritance ('obo:HP_0000006') must resolve to
    a readable label + the raw HPO curie on the association."""
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record(
                "HGNC:12338", "Definitive", "MONDO:0001085", moi_curie="HP:0000006"
            ),
        }
    )
    try:
        with (
            _patch_hgnc("HGNC:12338"),
            _patch_ensure_cached(tar_path),
            _patch_mondo(
                {
                    "MONDO:0001085": "focal segmental glomerulosclerosis",
                }
            ),
        ):
            bundle = await get_clingen_validity("TRPC6")

        assoc = bundle.associations[0]
        assert assoc.mode_of_inheritance == "Autosomal dominant"
        assert assoc.mode_of_inheritance_curie == "HP:0000006"
        assert "Autosomal dominant" in bundle.text
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_mode_of_inheritance_absent_when_not_in_record() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:12338", "Definitive", "MONDO:0001085"),
        }
    )
    try:
        with (
            _patch_hgnc("HGNC:12338"),
            _patch_ensure_cached(tar_path),
            _patch_mondo(
                {
                    "MONDO:0001085": "focal segmental glomerulosclerosis",
                }
            ),
        ):
            bundle = await get_clingen_validity("TRPC6")

        assert bundle.associations[0].mode_of_inheritance is None
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_skips_digenic_multi_subject_records_safely() -> None:
    """Records with a list 'subject' (digenic) should not crash indexing; single-gene
    records in the same dataset are still found."""
    tar_path = _make_tar(
        {
            "digenic--1.0.0.json": {
                "subject": ["hgnc:1", "hgnc:2"],
                "classification": "Limited",
                "proposition": {"objectCondition": "obo:MONDO_0000009"},
                "contributions": [],
            },
            "single--1.0.0.json": _make_record("HGNC:1", "Definitive", "MONDO:0000001"),
        }
    )
    try:
        with _patch_hgnc("HGNC:1"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("GENE1")

        assert bundle.total == 2
        classifications = {a.classification for a in bundle.associations}
        assert classifications == {"Definitive", "Limited"}
    finally:
        tar_path.unlink()


@pytest.mark.asyncio
async def test_get_clingen_validity_reads_the_older_record_shape() -> None:
    record = {
        "subject": "hgnc:51",
        "classification": "Definitive",
        "proposition": {
            "objectCondition": "obo:MONDO_0000001",
            "qualifierModeOfInheritance": "obo:HP_0000006",
        },
        "contributions": [],
    }
    tar_path = _make_tar({"a.json": record})
    with (
        _patch_ensure_cached(tar_path),
        _patch_hgnc("HGNC:51"),
        _patch_mondo({"MONDO:0000001": "disease"}),
    ):
        bundle = await get_clingen_validity("ACAA1")
    assert bundle.total == 1
    assert bundle.associations[0].mode_of_inheritance_curie == "HP:0000006"


@pytest.mark.asyncio
async def test_a_file_with_no_readable_gene_raises_rather_than_reads_as_no_curation() -> None:
    """A format change must surface as an error: empty would claim no curation for any gene."""
    tar_path = _make_tar({"a.json": {"classification": "Definitive", "proposition": {}}})
    with _patch_ensure_cached(tar_path), _patch_hgnc("HGNC:51"), pytest.raises(MCPToolError):
        await get_clingen_validity("ACAA1")


@pytest.mark.asyncio
async def test_dosage_reads_columns_by_the_file_header(tmp_path, monkeypatch) -> None:
    path = tmp_path / "dosage.tsv"
    path.write_text(
        "#ClinGen Gene Curation Results\n"
        "#Gene Symbol\tGene ID\tHaploinsufficiency Score\tHaploinsufficiency Description\t"
        "Haploinsufficiency PMID1\tHaploinsufficiency PMID2\tTriplosensitivity Score\t"
        "Triplosensitivity Description\tDate Last Evaluated\tHaploinsufficiency Disease ID\n"
        "AAA1\t1\t3\tSufficient evidence\t111\t\t0\tNo evidence\t2020-06-24\tMONDO:1\n"
        "BBB2\t2\t40\tDosage sensitivity unlikely\t\t\t0\tNo evidence\t2015-12-17\t\n"
    )
    monkeypatch.setattr(clingen_tools, "_DOSAGE_CACHE_PATH", path)
    monkeypatch.setattr(clingen_tools, "_CACHE_TTL", 10**9)
    found = await clingen_tools.get_clingen_dosage("AAA1")
    assert found.curated and found.haploinsufficiency_score == "3"
    assert found.haploinsufficiency_pmids == ["111"]
    assert found.last_evaluated == "2020-06-24"
    assert (await clingen_tools.get_clingen_dosage("BBB2")).haploinsufficiency_score == "40"
    missing = await clingen_tools.get_clingen_dosage("CCC3")
    assert not missing.curated and missing.haploinsufficiency_score == ""


def _dosage_files(tmp_path, monkeypatch) -> None:
    """The two curation lists, in the files' own shapes (MECP2's real rows, 2026-04-28)."""
    gene = tmp_path / "gene.tsv"
    gene.write_text(
        "#ClinGen Gene Curation Results\n"
        "#Gene Symbol\tGene ID\tcytoBand\tGenomic Location\tHaploinsufficiency Score\t"
        "Haploinsufficiency Description\tTriplosensitivity Score\t"
        "Triplosensitivity Description\tDate Last Evaluated\n"
        "MECP2\t4204\tXq28\tchrX:154021573-154097717\t3\tSufficient evidence for dosage "
        "pathogenicity\t0\tNo evidence available\t2021-02-22\n"
    )
    region = tmp_path / "region.tsv"
    region.write_text(
        "#ClinGen Region Curation Results\n"
        "#ISCA ID\tISCA Region Name\tcytoBand\tGenomic Location\tHaploinsufficiency Score\t"
        "Haploinsufficiency Description\tHaploinsufficiency PMID1\tTriplosensitivity Score\t"
        "Triplosensitivity Description\tTriplosensitivity PMID1\tDate Last Evaluated\t"
        "Triplosensitivity Disease ID\n"
        "ISCA-46304\tXq28 region (includes MECP2)\tXq28\tchrX:154008529-154110279\t0\t"
        "No evidence available\t\t3\tSufficient evidence for dosage pathogenicity\t32043567\t"
        "2021-02-22\tMONDO:0010283\n"
        "ISCA-37436\t17p12 recurrent (HNPP/CMT1A) region (includes PMP22)\t17p12\t"
        "chr17:14194598-15519638\t3\tSufficient evidence for dosage pathogenicity\t20301566\t3\t"
        "Sufficient evidence for dosage pathogenicity\t20301384\t2016-05-26\t\n"
    )
    monkeypatch.setattr(clingen_tools, "_DOSAGE_CACHE_PATH", gene)
    monkeypatch.setattr(clingen_tools, "_REGION_CACHE_PATH", region)
    monkeypatch.setattr(clingen_tools, "_CACHE_TTL", 10**9)


@pytest.mark.asyncio
async def test_dosage_reports_the_region_curation_the_gene_row_lacks(tmp_path, monkeypatch) -> None:
    """MECP2's gene row scores triplosensitivity 0; the Xq28 region containing it is curated
    as triplosensitive with sufficient evidence. Reading only the gene list printed the 0 as
    an authoritative negative for MECP2 duplication."""
    _dosage_files(tmp_path, monkeypatch)

    dosage = await clingen_tools.get_clingen_dosage("MECP2")

    assert dosage.triplosensitivity_score == "0"
    assert dosage.regions_checked is True
    assert [r.isca_id for r in dosage.regions] == ["ISCA-46304"]
    region = dosage.regions[0]
    assert region.triplosensitivity_score == "3"
    assert region.disease_id == "MONDO:0010283"
    assert "32043567" in region.pmids
    # The region's identity travels with its score: it is not the gene's own.
    assert "ISCA-46304" in dosage.text


@pytest.mark.asyncio
async def test_regions_are_matched_by_coordinate_not_by_name(tmp_path, monkeypatch) -> None:
    """The names say "(includes MECP2)", but a gene can sit in a region that does not name
    it, so overlap is computed on the locus."""
    _dosage_files(tmp_path, monkeypatch)

    # PMP22's locus, passed for a gene with no row of its own.
    dosage = await clingen_tools.get_clingen_dosage("PMP22", locus="chr17:15229777-15265326")

    assert not dosage.curated
    assert [r.isca_id for r in dosage.regions] == ["ISCA-37436"]
    assert dosage.regions_checked is True


@pytest.mark.asyncio
async def test_no_locus_means_regions_were_not_checked(tmp_path, monkeypatch) -> None:
    _dosage_files(tmp_path, monkeypatch)

    dosage = await clingen_tools.get_clingen_dosage("NOSUCHGENE")

    assert dosage.regions == []
    assert dosage.regions_checked is False


@pytest.mark.asyncio
async def test_a_gene_outside_every_curated_region_reports_none(tmp_path, monkeypatch) -> None:
    _dosage_files(tmp_path, monkeypatch)

    dosage = await clingen_tools.get_clingen_dosage("ELSEWHERE", locus="chr1:1000-2000")

    assert dosage.regions == []
    assert dosage.regions_checked is True
