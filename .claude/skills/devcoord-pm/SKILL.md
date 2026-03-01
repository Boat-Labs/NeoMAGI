---
name: devcoord-pm
description: Use when the agent is acting as the NeoMAGI PM and needs to drive devcoord gates, recovery, stale handling, audit, render, or gate closure through scripts/devcoord/coord.py.
---

# Devcoord PM

This skill is the PM-side operating contract for NeoMAGI devcoord after the control plane has already been initialized. One-off bootstrap such as `init` is outside the normal steady-state loop.

## Use this skill when

- issuing or advancing a gate
- translating teammate status into control-plane events
- handling recovery, timeout, stale, or append-first exceptions
- preparing review closure evidence
- rendering or auditing projections before gate close

## Hard rules

- Always write control-plane state through `uv run python scripts/devcoord/coord.py`.
- Prefer `apply <action> --payload-stdin` with JSON payloads.
- Never edit `dev_docs/logs/*`, `dev_docs/progress/project_progress.md`, or gate projections by hand.
- Never write devcoord state by calling `bd` directly.
- Treat repo-root `.beads` as the only default shared control plane.

## Workflow

1. Read `AGENTTEAMS.md`, `dev_docs/devcoord/beads_control_plane.md`, and the latest M7 plan/review if they are not already in context.
2. For every teammate status change, record the matching devcoord action first, then continue coordination.
3. If append-first cannot be satisfied in the same PM turn, immediately record `log-pending` and backfill on the next PM turn.
4. Before any `gate-close`, run `render`, then `audit`, and require `reconciled=true`.
5. Only close a gate after `gate-review` exists and the report commit/path are visible in the main repo.

## Command map

- Gate issue or phase handoff: `open-gate`
- Teammate ACK of `GATE_OPEN` or `PING`: `ack`
- Teammate progress update: `heartbeat`
- Backend phase result ready: `phase-complete`
- Restart or context loss: `recovery-check` then `state-sync-ok`
- Timeout handling: `ping`, `unconfirmed-instruction`, `stale-detected`
- Append-first exception: `log-pending`
- Review submitted: `gate-review`
- Projection refresh and reconciliation: `render`, `audit`
- Final gate decision: `gate-close`

## Output expectations

- Keep user-facing summaries short and evidence-first.
- When reporting state, point to `gate_state.md`, `watchdog_status.md`, `heartbeat_events.jsonl`, and review files instead of paraphrasing from memory.
