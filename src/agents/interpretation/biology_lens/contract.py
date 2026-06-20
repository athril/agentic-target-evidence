# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="biology_lens",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "depmap_dependency_fraction",
        "depmap_is_common_essential",
        "depmap_is_strongly_selective",
        "depmap_lineage_breakdown",
        "depmap_mean_chronos",
        "depmap_selective_lineages",
        "depmap_std_chronos",
        "depmap_text",
        "ot_mouse_phenotype_count",
        "ot_mouse_phenotype_labels",
        "ot_mouse_text",
        "ot_tractability_text",
        "is_oncology_indication",
        "omics_expression_text",
        "regulatory_element_text",
        "bulk_tpm",
        "hpa_specificity",
        "disease_tissue",
        "disease_tissue_expression_note",
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["biology_lens"],
)
