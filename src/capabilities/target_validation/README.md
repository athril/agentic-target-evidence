# Target Validation Capability

Answers: *Is gene X a viable drug target for disease Y?*

## What it composes

| Layer | Components |
|---|---|
| Retrieval | `agents/retrieval/` — literature, patent, clinical_trial, opentargets, genetics, omics, functional, druggability |
| Screening | `agents/screening/` — screening, knowledge_extraction |
| Interpretation | `agents/interpretation/` — genetics_lens, biology_lens, safety_lens, clinical_lens, commercial_lens |
| Challenge | `agents/challenge/` — critic, reviewer |
| Synthesis | `agents/synthesis/` — experiment, gap_detection, report |
| Services | `services/evidence/`, `services/decision/`, `services/retrieval/`, `services/knowledge_graph/` |

## Entry points

- `workflow.py` — `build_graph(router, checkpointer)` and `run_pipeline(graph, initial_state, config)`
- HITL gate: `hitl_gate_node` in `workflow.py` pauses the graph via `interrupt()` after
  evidence screening, before reasoning
