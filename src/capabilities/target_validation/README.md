# Target Validation Capability

Answers: *Is gene X a viable drug target for disease Y?*

## What it composes

| Layer | Components |
|---|---|
| Retrieval | 9 acquisition nodes: 3 agents (`agents/retrieval/` — literature, genetics, omics) + 6 services (`services/retrieval/` — patent, clinical_trial, opentargets, functional, druggability, openfda) |
| Screening | `agents/screening/` — screening (two passes), knowledge_extraction, source_quality |
| Interpretation | `agents/interpretation/` — genetics_lens, biology_lens, safety_lens, clinical_lens, commercial_lens, regulatory_lens |
| Challenge | `agents/challenge/` — critic, reviewer |
| Synthesis | `agents/synthesis/` — experiment, gap_detection, investigator (MCP tool-calling gap-closer), report |
| Services | `services/evidence/`, `services/decision/`, `services/retrieval/`, `services/knowledge_graph/` |

## Entry points

- `workflow.py` — `build_graph(router, checkpointer)` and `run_pipeline(graph, initial_state, config)`
- HITL gate: `hitl_gate_node` in `workflow.py` pauses the graph via `interrupt()` after
  evidence screening, before reasoning
