---
name: multi-agent-orchestration
description: Use when designing, implementing, modifying, or debugging the orchestrator and specialized agent workflows of Regulatory Agent V2.
---

# multi-agent-orchestration

## Baseline agents

- Orchestrator: Llama 3.2 3B.
- Retriever: Mistral 7B.
- Temporal Agent: Qwen 2.5 7B.
- Conflict Agent: DeepSeek-R1 14B.
- Explainer: Qwen 2.5 7B.
- Citation Agent: Mistral 7B.

## Routing

Use the minimum sufficient workflow.

- Simple current question → Retriever → Explainer → Citation.
- Historical question → Retriever → Temporal → Explainer → Citation.
- Potential contradiction → Retriever → Temporal when needed → Conflict → Explainer → Citation.

Invoke Conflict selectively rather than on every request.

## Contracts

Agents should receive structured input and return structured output with provenance. Avoid parsing free-form text when schemas are possible.

## Rules

- The orchestrator must not invent evidence.
- Agents must not silently overwrite evidence.
- Preserve provenance through every handoff.
- Handle agent failure explicitly.
- Make routing observable in audit.
- Keep deterministic checks outside LLMs.

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
