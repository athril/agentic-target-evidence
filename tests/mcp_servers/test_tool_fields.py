# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The trial, drug and label tools return a fixed set of fields.

Each model below is listed with every field it returns. A field added to one of them fails
here until it is listed, so a change to what these tools return is made on purpose.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from mcp_servers.clinicaltrials.tools import (
    ConditionTrialLandscape,
    ParticipationCriteria,
    StudyPlan,
    StudyRecordDates,
    TrialRecord,
)
from mcp_servers.openfda.labels import LabelSection, ParsedLabel
from mcp_servers.openfda.tools import (
    AdverseEventBundle,
    DrugLabelRecord,
    IndicationDrugLandscape,
    TopReaction,
)
from mcp_servers.opentargets.tools import KnownDrug, KnownDrugsBundle

FIELDS: dict[type[BaseModel], set[str]] = {
    # clinicaltrials_search_trials
    TrialRecord: {
        "nct_id", "title", "status", "phase", "conditions", "interventions", "enrollment",
        "start_date", "scope", "brief_summary", "sponsor", "design_details",
        "participation_criteria", "study_plan", "record_dates",
    },
    ParticipationCriteria: {
        "sex", "minimum_age", "maximum_age", "healthy_volunteers", "eligibility_criteria",
    },
    StudyPlan: {
        "study_type", "allocation", "intervention_model", "intervention_model_description",
        "primary_purpose", "masking", "number_of_arms",
    },
    StudyRecordDates: {
        "first_submit_date", "first_post_date", "last_update_submit_date",
        "last_update_post_date",
    },
    # clinicaltrials_count_condition_trials
    ConditionTrialLandscape: {
        "condition", "total_count", "active_count", "phase3_count", "recruiting_count",
        "mapping", "source_link", "text",
    },
    # opentargets_get_known_drugs
    KnownDrugsBundle: {"gene_id", "total_count", "drugs", "text"},
    KnownDrug: {
        "drug_id", "drug_name", "drug_type", "max_phase", "is_approved",
        "mechanism_of_action", "action_type", "disease_name", "disease_id", "trial_status",
        "approved_in_indication",
    },
    # openfda_search_drug_labels
    DrugLabelRecord: {
        "drug_name", "brand_names", "product_type", "indications_and_usage",
        "mechanism_of_action", "warnings", "boxed_warning", "contraindications",
        "adverse_reactions", "application_number", "source_link", "text",
    },
    # openfda_search_adverse_events
    AdverseEventBundle: {
        "drug_name", "total_reports", "serious_reports", "death_reports", "serious_rate",
        "death_rate", "top_reactions", "source_link", "text",
        # the exact search behind the counts, and the dataset's date
        "query", "data_last_updated",
    },
    TopReaction: {"reaction", "count"},
    # openfda_count_indication_drugs
    IndicationDrugLandscape: {
        "indication", "approved_drug_count", "drugs", "moa_examples", "mapping",
        "source_link", "text",
        # the search's total, and whether `drugs` holds only part of it
        "label_total", "capped",
    },
    # fetch_label: one drug's label, parsed
    ParsedLabel: {
        "drug_name", "brand_names", "product_type", "application_number", "set_id",
        "effective_date", "format", "boxed_warning", "boxed_titles", "warning_sections",
        "contraindications", "mechanism_of_action", "source_link",
    },
    LabelSection: {"number", "title", "text"},
}  # fmt: skip


@pytest.mark.parametrize("model", list(FIELDS), ids=lambda m: m.__name__)
def test_the_model_returns_exactly_its_listed_fields(model: type[BaseModel]) -> None:
    returned = set(model.model_fields)
    assert returned - FIELDS[model] == set(), "a field is returned that is not listed here"
    assert FIELDS[model] - returned == set(), "a listed field is no longer returned"


def test_a_trial_record_dumps_only_its_fields() -> None:
    """The dump the tool sends is the model's fields and nothing else, nested ones included."""
    dumped = TrialRecord(nct_id="NCT00000000", title="t").model_dump()
    assert set(dumped) == FIELDS[TrialRecord]
    assert set(dumped["participation_criteria"]) == FIELDS[ParticipationCriteria]
    assert set(dumped["study_plan"]) == FIELDS[StudyPlan]
    assert set(dumped["record_dates"]) == FIELDS[StudyRecordDates]
