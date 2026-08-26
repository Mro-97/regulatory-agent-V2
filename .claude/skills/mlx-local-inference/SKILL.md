---
name: mlx-local-inference
description: Use when implementing, configuring, optimizing, testing, or troubleshooting local LLM inference with MLX on Apple Silicon for Regulatory Agent V2.
---

# mlx-local-inference

## Responsibilities

- Keep all model inference local and MLX-based.
- Baseline model roles: Llama 3.2 3B for orchestration; Mistral 7B for retrieval/citation; Qwen 2.5 7B for temporal filtering/explanation; DeepSeek-R1 14B for conflict detection.
- Account for the 24 GB of unified memory on `m4pro2`, shared with Qdrant, PostgreSQL and Redis. Concurrent model residency is not an option: one model at a time.
- Prefer controlled model lifecycle management.
- Keep generation settings explicit and reproducible where correctness matters.
- Separate model output from deterministic validation logic.

## Procedure

1. Identify model role and memory footprint.
2. Check expected memory use.
3. Verify the repository’s actual MLX/MLX-LM interface.
4. Implement a small adapter.
5. Add timeouts, error handling and structured output constraints.
6. Measure latency and memory before optimizing.
7. Never use an external inference provider as fallback.

## Reliability

LLMs must not be the sole authority for temporal validity, citation existence, hashes, queue state, permissions or audit integrity. Enforce these properties deterministically.

## Project context

This skill operates inside **Regulatory Agent V2**, a local regulatory-watch and AI assistance system for industrial users. The whole system runs on a **single machine**: `m4pro2` — Mac Mini M4 Pro, 24 GB of unified memory. The distributed three-machine architecture has been abandoned.

Every service listens on `127.0.0.1` only:

- **FastAPI** `:8000` — single entry point; also serves the web interface (chat + validation panels).
- **Qdrant** `:6333` — vector store.
- **Redis** `:6379` — cache and validation queues.
- **PostgreSQL** `:5432` — audit, metadata, history.
- **Orchestrator, agents, Watcher and audit** — local modules on the same host.

Models are loaded on demand, **one at a time**: Llama 3.2 3B (routing), Mistral 7B (Retriever / Citation), Qwen 2.5 7B (Temporal / Explainer), DeepSeek-R1 14B (Conflict, ~20 % of requests, 8-10 GB in 4-bit — load only when genuinely needed, then unload).

The project requires local inference with MLX and is designed around regulatory documents, version history, exact citations, human validation and auditability.

## Non-negotiable constraints

1. Never introduce an external AI inference API.
2. Do not silently replace MLX with another inference stack.
3. Preserve the single-machine memory discipline: one model resident at a time, lazy loading, explicit unloading. Never assume a second machine is available, and never bind a service to `0.0.0.0`.
4. Preserve the canonical regulatory JSON model and temporal semantics.
5. Treat regulatory answers as evidence-backed outputs, not unsupported legal conclusions.
6. Prefer deterministic, testable components around LLM calls.
7. Do not invent regulatory facts, versions, dates, articles or citations.
8. When a requirement is ambiguous, identify the ambiguity instead of silently changing a project invariant.

## Working rules

- Inspect the existing repository before proposing structural changes.
- Reuse compatible project abstractions.
- Keep changes modular and reversible.
- Explain cross-component consequences before making architectural changes.
- Add or update tests for behavior that can affect regulatory correctness.
- Keep secrets and credentials out of source code and logs.
