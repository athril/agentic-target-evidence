# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""One FDA label parsed once, tested on the recorded shapes of real labels.

``tests/fixtures/fda_labels/`` holds openFDA drug/label results (US government works) trimmed
to the sections the parser reads. They cover current (PLR) labels with numbered warnings
(sotorasib, cetuximab, olaparib, encorafenib, panitumumab, ivacaftor, abacavir, tovorafenib)
and old labels with no numbered section at all (procainamide, carbamazepine).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.openfda.labels import ParsedLabel, ingredients, normalize, parse_label
from mcp_servers.openfda.tools import fetch_label

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fda_labels"
_LABELS_URL = "https://api.fda.gov/drug/label.json"


def label(name: str) -> ParsedLabel:
    return parse_label(json.loads((_FIXTURES / f"{name}.json").read_text()))


# ── warnings ────────────────────────────────────────────────────────────────────────


def test_numbered_warning_sections_are_listed_with_titles() -> None:
    assert [(s.number, s.title) for s in label("sotorasib").warning_sections] == [
        ("5.1", "Hepatotoxicity"),
        ("5.2", "Interstitial Lung Disease (ILD)/Pneumonitis"),
    ]
    titles = [s.title for s in label("ivacaftor").warning_sections]
    assert titles[0] == "Transaminase (ALT or AST) Elevations"
    assert "Cataracts" in titles
    # "In Study 20020408, 4% of patients …" opens the body, not the title
    assert "Infusion Reactions" in [s.title for s in label("panitumumab").warning_sections]


def test_old_label_warnings_split_at_their_own_headings() -> None:
    assert [s.title for s in label("procainamide").warning_sections] == [
        "Mortality",
        "Blood Dyscrasias",
    ]
    carba = [s.title for s in label("carbamazepine").warning_sections]
    assert "SJS/TEN and HLA-B*1502 Allele" in carba
    assert "Aplastic Anemia and Agranulocytosis" in carba
    # sentences that open with capitalised words are not headings
    assert not any(t.startswith(("South Asians", "Many", "Over")) for t in carba)


def test_boxed_warning_titles() -> None:
    assert label("abacavir").boxed_titles == ["HYPERSENSITIVITY REACTIONS"]
    assert "HLA-B*1502" in label("carbamazepine").boxed_warning


def test_contraindications_and_mechanism_are_kept_as_written() -> None:
    assert label("abacavir").contraindications
    parsed = parse_label(
        {
            "mechanism_of_action": ["12.1 Mechanism of Action Druga inhibits KIN1 ( 12.2 )."],
            "openfda": {"generic_name": ["DRUGA"]},
        }
    )
    assert parsed.mechanism_of_action == "12.1 Mechanism of Action Druga inhibits KIN1."


def test_the_label_keeps_its_identity() -> None:
    soto = label("sotorasib")
    assert (soto.drug_name, soto.application_number, soto.effective_date) == (
        "SOTORASIB",
        "NDA214665",
        "2025-01-22",
    )
    assert soto.source_link.endswith(soto.set_id)


# ── text ────────────────────────────────────────────────────────────────────────────


def test_spaced_label_hyphens_are_joined() -> None:
    assert normalize("the HLA ‑ B*5701 allele") == "the HLA-B*5701 allele"
    assert normalize("KRAS G12C -mutated NSCLC ( 1.1 )") == "KRAS G12C-mutated NSCLC"


def test_combination_names_match_whatever_their_order() -> None:
    assert ingredients("ELEXACAFTOR, IVACAFTOR, TEZACAFTOR") == ingredients(
        "ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR"
    )
    assert ingredients("DABRAFENIB MESYLATE") == ingredients("DABRAFENIB")
    assert ingredients("LERODALCIBEP-LIGA") == ingredients("LERODALCIBEP")


# ── fetching by name ────────────────────────────────────────────────────────────────


def _result(name: str, app: str, rx: bool = True, effective: str = "20250101") -> dict:
    return {
        "set_id": f"set-{name}-{app}",
        "effective_time": effective,
        "openfda": {
            "generic_name": [name],
            "brand_name": [f"{name} BRAND"],
            "product_type": ["HUMAN PRESCRIPTION DRUG" if rx else "HUMAN OTC DRUG"],
            "application_number": [app],
        },
    }


@respx.mock
async def test_the_originator_label_is_found_by_name_in_one_request() -> None:
    route = respx.get(_LABELS_URL).mock(
        return_value=httpx.Response(200, json={"results": [_result("SOTORASIB", "NDA214665")]})
    )
    found = await fetch_label("SOTORASIB")
    assert found is not None and found.application_number == "NDA214665"
    assert route.call_count == 1
    assert "openfda.application_number:NDA*" in str(route.calls[0].request.url.params["search"])


@respx.mock
async def test_a_drug_sold_only_as_generics_falls_back_to_any_label() -> None:
    """Procainamide has no NDA label, only ANDAs: the gene-keyword search never found one."""
    respx.get(_LABELS_URL).mock(
        side_effect=[
            httpx.Response(404, json={"error": {"code": "NOT_FOUND"}}),
            httpx.Response(
                200, json={"results": [_result("PROCAINAMIDE HYDROCHLORIDE", "ANDA088636")]}
            ),
        ]
    )
    found = await fetch_label("PROCAINAMIDE")
    assert found is not None and found.application_number == "ANDA088636"


@respx.mock
async def test_a_combination_does_not_stand_for_its_ingredient() -> None:
    """Quinidine's NDA search finds dextromethorphan and quinidine; its own label is generic."""
    respx.get(_LABELS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={"results": [_result("DEXTROMETHORPHAN AND QUINIDINE SULFATE", "NDA021879")]},
            ),
            httpx.Response(
                200,
                json={
                    "results": [
                        _result("DEXTROMETHORPHAN AND QUINIDINE SULFATE", "NDA021879"),
                        _result("QUINIDINE SULFATE", "ANDA088072"),
                    ]
                },
            ),
        ]
    )
    found = await fetch_label("QUINIDINE")
    assert found is not None and found.drug_name == "QUINIDINE SULFATE"


@respx.mock
async def test_a_combination_is_found_by_its_brand() -> None:
    respx.get(_LABELS_URL).mock(
        side_effect=[
            httpx.Response(404, json={}),
            httpx.Response(
                200,
                json={"results": [_result("ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR", "NDA212273")]},
            ),
        ]
    )
    found = await fetch_label("ELEXACAFTOR, IVACAFTOR, TEZACAFTOR", ["TRIKAFTA"])
    assert found is not None and found.application_number == "NDA212273"


@respx.mock
async def test_no_label_is_none_and_a_failed_request_raises() -> None:
    respx.get(_LABELS_URL).mock(return_value=httpx.Response(404, json={}))
    assert await fetch_label("NAPORAFENIB") is None
    respx.get(_LABELS_URL).mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(MCPToolError):
        await fetch_label("NAPORAFENIB")


@respx.mock
async def test_a_brand_listed_combination_does_not_stand_for_its_ingredient() -> None:
    """Ivacaftor's brand list names Orkambi; its own label is Kalydeco's."""
    orkambi = _result("LUMACAFTOR AND IVACAFTOR", "NDA206038", effective="20260324")
    orkambi["openfda"]["brand_name"] = ["ORKAMBI"]
    kalydeco = _result("IVACAFTOR", "NDA203188", effective="20260101")
    respx.get(_LABELS_URL).mock(
        return_value=httpx.Response(200, json={"results": [orkambi, kalydeco]})
    )
    found = await fetch_label("IVACAFTOR", ["ORKAMBI", "KALYDECO"])
    assert found is not None and found.drug_name == "IVACAFTOR"


@respx.mock
async def test_a_label_of_other_ingredients_is_no_label() -> None:
    """A procaine search finds penicillin G benzathine and procaine: not procaine's label."""
    respx.get(_LABELS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    _result("PENICILLIN G BENZATHINE AND PENICILLIN G PROCAINE", "NDA050138")
                ]
            },
        )
    )
    assert await fetch_label("PROCAINE") is None


@respx.mock
async def test_a_label_openfda_has_not_harmonised_is_found_by_its_product_data() -> None:
    """Repatha's and Linzess's labels carry no openfda block, so no generic name."""
    repatha = {
        "set_id": "repatha",
        "effective_time": "20260819",
        "spl_product_data_elements": ["Repatha evolocumab evolocumab proline"],
        "description": ["Evolocumab is a human monoclonal IgG2 antibody."],
        "indications_and_usage": ["1 INDICATIONS AND USAGE Repatha is indicated to reduce X."],
    }
    respx.get(_LABELS_URL).mock(return_value=httpx.Response(200, json={"results": [repatha]}))
    found = await fetch_label("EVOLOCUMAB", ["REPATHA"])
    assert found is not None and found.drug_name == "EVOLOCUMAB"


@respx.mock
async def test_a_brand_of_another_drug_is_no_label() -> None:
    """Levonordefrin's application lists Bicillin among its products; its label is not that."""
    bicillin = _result("PENICILLIN G BENZATHINE AND PENICILLIN G PROCAINE", "NDA050138")
    bicillin["openfda"]["brand_name"] = ["BICILLIN C-R"]
    respx.get(_LABELS_URL).mock(return_value=httpx.Response(200, json={"results": [bicillin]}))
    assert await fetch_label("LEVONORDEFRIN", ["BICILLIN C-R"]) is None
