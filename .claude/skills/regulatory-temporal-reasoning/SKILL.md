---
name: regulatory-temporal-reasoning
description: Use when implementing or reviewing regulatory versioning, validity intervals, historical questions, temporal filtering, or ChronosGuard-inspired logic.
---

# regulatory-temporal-reasoning

## Responsibilities

Treat time as a first-class dimension of regulatory retrieval and reasoning. Relevant fields include `publication_date`, `entry_into_force`, `version`, `valid_from` and `valid_to`.

## Core semantics

A version is applicable at date D when its validity interval contains D according to an explicitly defined boundary convention. Do not infer a historical version from publication date alone.

Project example:
- Version A: `2018-05-25` → `2026-08-02`
- Version B: `2026-08-03` → open-ended

## Procedure

1. Parse the target date.
2. Retrieve candidates.
3. Filter by validity interval.
4. Detect overlaps or gaps deterministically.
5. Preserve the selected version in evidence.
6. Generate only from temporally valid evidence.
7. Cite version and effective dates.

Overlapping intervals, gaps, impossible dates and duplicate versions are data-quality issues. Flag them; do not silently choose.

When adapting ChronosGuard, reuse compatible temporal concepts while preserving Qdrant and the canonical JSON model.

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
