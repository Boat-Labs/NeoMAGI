---
name: devcoord-pm
description: PM-side operating contract for NeoMAGI devcoord — gate lifecycle, teammate coordination, recovery sync, timeout escalation, projection rendering, audit, and gate closure. Drive all control-plane state through scripts/devcoord/coord.py. Use when acting as the PM or when the request mentions open-gate, state-sync-ok, ping, unconfirmed-instruction, stale-detected, log-pending, render, audit, gate-review, or gate-close.
---

# Devcoord PM

PM-side operating contract for NeoMAGI devcoord after the control plane has been initialized. One-off bootstrap (`init`) is outside the normal steady-state loop.

## Hard rules

- Always write control-plane state through `uv run python scripts/devcoord/coord.py`.
- Prefer grouped CLI commands for human/debug use (e.g., `gate open`, `projection audit`). For machine-first payloads, use `apply <action> --payload-stdin`.
- Never edit `dev_docs/logs/phase1/*`, `dev_docs/logs/phase2/*`, `dev_docs/progress/project_progress.md`, or gate projections by hand.
- Never write devcoord state by calling `bd` directly.
- Treat repo-root `.beads` as the only default shared control plane.

## Role boundaries

PM may record: `gate open`, `event state-sync-ok`, `command send --name PING`, `event unconfirmed-instruction`, `event stale-detected`, `event log-pending`, `gate close`, `milestone close`, `projection render`, `projection audit`.

PM must not record teammate actions: `command ack`, `event heartbeat`, `event phase-complete`, `event recovery-check`, `gate review`.

## Workflow

1. Verify familiarity with `AGENTTEAMS.md`, `dev_docs/devcoord/beads_control_plane.md`, and the latest milestone plan/review; read only if not already in context.
2. For every teammate status change, record the matching devcoord action first, then continue coordination.
3. If append-first cannot be satisfied in the same PM turn, immediately record `event log-pending` and backfill on the next turn.
4. When spawning Claude Code teammate actions, include `target_commit` and require the teammate to verify `git rev-parse HEAD == target_commit` before any devcoord write.
5. Before any `gate close`, run `projection render`, then `projection audit`, and require `reconciled=true`.
6. Only close a gate after `gate review` exists and the report commit/path are visible in the main repo.

## Command map

| Grouped CLI | Purpose |
|-------------|---------|
| `gate open` | Gate issue or phase handoff |
| `event state-sync-ok` | PM confirms teammate recovery state is consistent |
| `command send --name PING` | Liveness check requiring teammate ACK |
| `event unconfirmed-instruction` | Escalation when teammate fails to ACK after ping |
| `event stale-detected` | Timeout escalation after repeated ping failures |
| `event log-pending` | Append-first exception — deferred logging marker |
| `gate close` | Final gate decision after review + audit |
| `milestone close` | Close entire milestone |
| `projection render` | Refresh projection files from control plane |
| `projection audit` | Reconciliation check (returns `reconciled` boolean) |

Legacy flat commands (e.g., `open-gate`, `ping`, `render`) remain available as compatibility aliases but are not the canonical path.

## Payload reference

See [references/payloads.md](references/payloads.md) for required fields and example JSON for each `apply` action.

## Error handling

- If `coord.py` rejects a payload, fix and retry — do not skip the control-plane write.
- If `projection audit` returns `reconciled=false`, inspect the reported discrepancies, fix the underlying records, re-run `projection render` then `projection audit` again. Do not proceed to `gate close` until `reconciled=true`.
- If a teammate reports `blocked: HEAD mismatch`, issue a new `target_commit` via `event state-sync-ok` or provide a corrected gate instruction.

## Output expectations

- Keep user-facing summaries short and evidence-first.
- When reporting state, point to `gate_state.md`, `watchdog_status.md`, `heartbeat_events.jsonl`, and review files instead of paraphrasing from memory.
