# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="literature",
    consumes={
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "population",
        "query",
        "model_phenotypes",  # MeSH phenotypes of the disease's animal models
    },
    produces=set(),  # payload carries Evidence list
    max_loops=3,
    skills=["pubmed_query_craft"],
)
