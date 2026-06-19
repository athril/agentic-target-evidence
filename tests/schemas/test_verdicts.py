# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Schema round-trip tests for LensVerdict and ValidationFlag.

Regression locks for the schema changes introduced in the lens-validation hardening plan.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from schemas.verdicts import LensVerdict, ValidationFlag

# ---------------------------------------------------------------------------
# ValidationFlag
# ---------------------------------------------------------------------------


class TestValidationFlag:
    def test_round_trip(self):
        f = ValidationFlag(
            lens="genetics",
            severity="high",
            rule_id="GENETICS-HI",
            claim_excerpt="haploinsufficiency based on LOEUF",
            message="LOEUF=0.759 does not support haploinsufficiency.",
        )
        d = f.model_dump(mode="json")
        f2 = ValidationFlag.model_validate(d)
        assert f2.rule_id == "GENETICS-HI"
        assert f2.severity == "high"
        assert f2.lens == "genetics"
        assert f2.claim_excerpt == "haploinsufficiency based on LOEUF"

    def test_severity_values(self):
        for sev in ("high", "medium", "low"):
            f = ValidationFlag(lens="biology", severity=sev, rule_id="X", message="m")
            assert f.severity == sev

    def test_frozen(self):
        f = ValidationFlag(lens="safety", severity="low", rule_id="R", message="m")
        with pytest.raises(ValidationError):
            f.rule_id = "new"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LensVerdict — schema_version + validation_flags
# ---------------------------------------------------------------------------


class TestLensVerdict:
    def _base(self, **kwargs) -> dict:
        run_id = uuid.uuid4()
        return {
            "run_id": str(run_id),
            "trace_id": "t1",
            "lens": "genetics",
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "overall_verdict": "support",
            "confidence": 0.85,
            "axes": [],
            "rationale": "Test.",
            "narrative": "Narrative.",
            **kwargs,
        }

    def test_default_schema_version_is_1_0(self):
        lv = LensVerdict(**self._base())
        assert lv.schema_version == "1.0"

    def test_validation_flags_defaults_to_empty(self):
        lv = LensVerdict(**self._base())
        assert lv.validation_flags == []

    def test_validation_flags_round_trip(self):
        flag_dict = {
            "lens": "genetics",
            "severity": "high",
            "rule_id": "GENETICS-HI",
            "claim_excerpt": "haploinsufficiency",
            "message": "LOEUF does not support this.",
        }
        lv = LensVerdict(**self._base(validation_flags=[flag_dict]))
        assert len(lv.validation_flags) == 1
        assert lv.validation_flags[0].rule_id == "GENETICS-HI"

    def test_full_round_trip_with_flags(self):
        """model_dump + model_validate round trip must preserve all flags."""
        f = ValidationFlag(lens="genetics", severity="high", rule_id="GENETICS-HI", message="m")
        lv = LensVerdict(**self._base(), validation_flags=[f.model_dump(mode="json")])
        dumped = lv.model_dump(mode="json")
        lv2 = LensVerdict.model_validate(dumped)
        assert len(lv2.validation_flags) == 1
        assert lv2.validation_flags[0].rule_id == "GENETICS-HI"
        assert lv2.validation_flags[0].severity == "high"

    def test_multiple_lenses_have_own_flags(self):
        """Different lens verdicts track their own validation flags independently."""
        gen_flag = ValidationFlag(
            lens="genetics", severity="high", rule_id="GENETICS-HI", message="m"
        )
        com_flag = ValidationFlag(
            lens="commercial", severity="high", rule_id="COMMERCIAL-PATENT", message="m"
        )

        gen_lv = LensVerdict(
            **{
                **self._base(),
                "lens": "genetics",
                "validation_flags": [gen_flag.model_dump(mode="json")],
            }
        )
        com_data = {
            **self._base(),
            "lens": "commercial",
            "validation_flags": [com_flag.model_dump(mode="json")],
        }
        com_lv = LensVerdict.model_validate(com_data)

        assert gen_lv.validation_flags[0].rule_id == "GENETICS-HI"
        assert com_lv.validation_flags[0].rule_id == "COMMERCIAL-PATENT"

    def test_from_dict_accepts_flags(self):
        data = self._base(
            validation_flags=[
                {
                    "lens": "biology",
                    "severity": "medium",
                    "rule_id": "BIOLOGY-POCKET",
                    "message": "No structural evidence.",
                }
            ]
        )
        lv = LensVerdict.from_dict(data)
        assert lv.validation_flags[0].rule_id == "BIOLOGY-POCKET"
