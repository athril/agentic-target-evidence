# Knowledge Graph Service

Manages the **biomedical knowledge graph** — typed nodes and edges over genes, diseases, variants,
pathways, drugs, cell types, biomarkers, phenotypes, and publications.

**Naming note:** "graph" here means the biomedical data graph, not orchestration. Orchestration
graphs live in `capabilities/*/workflow.py`.

## Node / Edge taxonomy

See `src/schemas/knowledge_graph.py` for the canonical `NodeType`, `EdgeType`, `GraphNode`,
and `GraphEdge` models.

| EdgeType | Meaning | Primary source |
|---|---|---|
| `ASSOCIATED_WITH` | Epidemiological/genetic association | OpenTargets, GWAS |
| `CAUSES` | Direct causal relationship | OpenTargets curated |
| `EXPRESSED_IN` | Gene expressed in tissue/cell type | GTEx |
| `ACTIVATES` / `INHIBITS` | Functional interaction | Reactome, STRING |
| `SUPPORTS` / `CONTRADICTS` | Claim-level evidence relationship | Evidence extraction |

## Modules

- `builder.py` — `EvidenceGraph` / `build_evidence_graph`: builds in-memory graph from Evidence + CoreClaims (relocated from `services/evidence/graph_builder.py`).
- `ingest.py` — stubs for ingesting OpenTargets/GTEx/Reactome edges into Postgres.
- `export.py` — stub for exporting a run's subgraph artifact to `results/data/`.
- `query.py` — stubs for neighborhood traversal and edge queries consumed by lenses + report.

## Future work (out of scope for now)

- Postgres `GraphNodeRow` / `GraphEdgeRow` models + Alembic migration.
- Implementing `ingest`, `export`, `query`.
- Wiring KG traversal into biology/genetics lenses and the report agent.
