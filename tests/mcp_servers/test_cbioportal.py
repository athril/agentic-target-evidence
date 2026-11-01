# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""cBioPortal connector: TCGA PanCancer Atlas studies only, mutations and GISTIC events of
the requested gene only, failures recorded per study."""

from __future__ import annotations

import httpx
import respx

from mcp_servers.cbioportal.tools import get_somatic_alterations

_B = "https://www.cbioportal.org/api"
_STUDIES = [
    {"studyId": "brca_tcga_pan_can_atlas_2018", "name": "Breast", "cancerTypeId": "brca",
     "sequencedSampleCount": 1066, "cnaSampleCount": 1070},
    {"studyId": "brca_mskcc_2019", "name": "Other terms", "cancerTypeId": "brca",
     "sequencedSampleCount": 10, "cnaSampleCount": 10},
]  # fmt: skip


@respx.mock
async def test_reads_pan_cancer_atlas_studies_and_only_the_gene() -> None:
    respx.get(f"{_B}/studies").mock(return_value=httpx.Response(200, json=_STUDIES))
    respx.get(f"{_B}/molecular-profiles/brca_tcga_pan_can_atlas_2018_mutations/mutations").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "sampleId": "S1",
                    "proteinChange": "E545K",
                    "proteinPosStart": 545,
                    "mutationType": "Missense_Mutation",
                    "entrezGeneId": 5290,
                },
            ],
        )  # fmt: skip
    )
    respx.post(
        f"{_B}/molecular-profiles/brca_tcga_pan_can_atlas_2018_gistic/discrete-copy-number/fetch"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"sampleId": "S2", "alteration": 2, "entrezGeneId": 5290},
                {"sampleId": "S3", "alteration": -2, "entrezGeneId": 5290},
                {"sampleId": "S4", "alteration": -2, "entrezGeneId": 1234},  # another gene
            ],
        )  # fmt: skip
    )
    b = await get_somatic_alterations(5290, "PIK3CA")
    [s] = b.studies
    assert s.study_id == "brca_tcga_pan_can_atlas_2018" and s.sequenced_samples == 1066
    assert [m.position for m in s.mutations] == [545]
    assert (s.amplified_samples, s.deep_deleted_samples) == (["S2"], ["S3"])
    assert "gene_list=PIK3CA" in b.source_link


@respx.mock
async def test_a_failing_study_keeps_its_counts_and_records_the_error() -> None:
    respx.get(f"{_B}/studies").mock(return_value=httpx.Response(200, json=_STUDIES[:1]))
    respx.get(f"{_B}/molecular-profiles/brca_tcga_pan_can_atlas_2018_mutations/mutations").mock(
        return_value=httpx.Response(500)
    )
    respx.post(
        f"{_B}/molecular-profiles/brca_tcga_pan_can_atlas_2018_gistic/discrete-copy-number/fetch"
    ).mock(return_value=httpx.Response(200, json=[]))
    [s] = (await get_somatic_alterations(5290)).studies
    assert s.error == "mutations HTTP 500" and s.sequenced_samples == 1066
