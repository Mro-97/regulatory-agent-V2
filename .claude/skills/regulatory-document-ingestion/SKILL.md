---
name: regulatory-document-ingestion
description: Use when implementing or reviewing ingestion of EUR-Lex, Légifrance, ANSSI, CNIL, PDF, HTML, or API regulatory sources into the canonical JSON and indexing pipeline.
---

# regulatory-document-ingestion

## Pipeline

`source → fetch → validate → extract → clean → structure → normalize → version → persist → index`

## Responsibilities

- Use distinct source adapters where formats differ.
- Record source identity and retrieval metadata.
- Preserve originals where project policy requires.
- Detect extraction failures instead of indexing partial text.
- Never fabricate missing identifiers, dates or metadata.

## Canonical data

Maintain document identity, title, source, publication/effective dates, version, themes, chapters, articles, article text, validity intervals, citations and related texts.

## Versioning

A changed source must not automatically overwrite history. Compare hashes and relevant metadata, determine whether a new version exists, and preserve prior versions.

## Quality gates

Before indexing verify identifiers, non-empty coherent text, parseable dates, consistent validity intervals, source metadata and deterministic duplicate handling. Failed gates produce an explicit error or review state.

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
