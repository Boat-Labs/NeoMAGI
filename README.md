# NeoMAGI

[中文](README_ch.md) | [Deutsch](README_de.md)

NeoMAGI is an open-source personal agent project.

The product idea is straightforward: build an agent that can carry memory over time, represent the user's information interests, and gradually move from hosted model APIs toward more local and user-controlled model stacks.

## Product Position

NeoMAGI is not trying to be a generic chatbot shell.

The intended direction is a long-term partner-style AI that:
- remembers useful context across time
- acts in service of the user rather than platform incentives
- can expand its capabilities in a controlled and auditable way
- keeps a practical migration path from commercial APIs to local models

## Principles

- Think deeply, implement simply.
- Prefer the smallest useful closed loop.
- Avoid unnecessary abstraction and dependency sprawl.
- Treat governance, rollback, and scope boundaries as product features, not only engineering details.

## What's Built

- **Multi-channel interaction**: WebSocket (WebChat) and Telegram, with channel-agnostic dispatch
- **Persistent memory**: PostgreSQL-backed hybrid search (vector + keyword), session-aware scope resolution, anti-drift compression
- **Growth governance**: explicit, verifiable, rollback-capable evolution — every capability change is proposed, evaluated, applied, and auditable
- **Skill objects**: a runtime experience layer that captures reusable task knowledge, so the agent doesn't start from zero each time
- **Procedure runtime**: deterministic multi-step execution with steering, checkpoints, and resume
- **Multi-agent execution**: bounded handoff between agents under a single principal, with governed context exchange
- **Multi-provider models**: OpenAI and Gemini, with per-run routing and atomic budget gating
- **Operational reliability**: startup preflight checks, runtime diagnostics, structured backup and restore

## Current Status

Phase 1 (foundation) is complete: session continuity, persistent memory, model migration, Telegram channel, operational reliability, and development governance across 7 milestones.

Phase 2 (explicit growth and verifiable evolution) is actively being built:
- **P2-M1** (Explicit Growth & Builder Governance): complete — growth governance kernel, skill objects runtime, wrapper tools, growth cases
- **P2-M2** (Procedure Runtime & Multi-Agent Execution): core complete — procedure runtime, multi-agent handoff, ProcedureSpec governance adapter
- **P2-M2d** (Memory Source Ledger Prep): next — DB append-only ledger writer, dual-write with parity checks
- **P2-M3** (Principal & Memory Safety): planned — WebChat auth, canonical user identity, memory visibility policy, shared-space safety skeleton

Phase 3 directions (candidate, not yet active): governed self-evolution workflow.

## Tech Stack

- **Language**: Python 3.12+ (async/await)
- **Backend**: FastAPI + WebSocket
- **Storage**: PostgreSQL 17 + pgvector
- **LLM**: OpenAI SDK, Gemini — per-run provider routing
- **Embedding**: Ollama (preferred) → OpenAI (fallback)
- **Tooling**: uv, pnpm (frontend), just, ruff, pytest

## Documentation

- Design entry: `design_docs/index.md`
- Phase 2 roadmap: `design_docs/phase2/roadmap_milestones_v1.md`
- Phase 2 architecture index: `design_docs/phase2/index.md`
- Domain glossary: `design_docs/GLOSSARY.md`
- Module boundaries: `design_docs/modules.md`
- Runtime prompt model: `design_docs/system_prompt.md`
- Memory architecture: `design_docs/memory_architecture_v2.md`
- Procedure runtime: `design_docs/procedure_runtime.md`
- Skill objects: `design_docs/skill_objects_runtime.md`
- Phase 1 archive: `design_docs/phase1/index.md`
- Repo governance: `AGENTS.md`, `CLAUDE.md`, `AGENTTEAMS.md`

## Status Note

Expect active iteration.

Names, boundaries, and implementation details may continue to change as the product direction becomes sharper and more of the system is validated through real use.
