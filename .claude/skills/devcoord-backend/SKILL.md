---
name: devcoord-backend
description: Use when the agent is acting as the NeoMAGI backend teammate and must acknowledge PM instructions or report backend phase progress through scripts/devcoord/coord.py.
---

# Devcoord Backend

This skill defines the backend teammate's devcoord write path.

## Use this skill when

- a PM issues `GATE_OPEN`, `WAIT`, `RESUME`, or `PING`
- backend work starts, resumes, blocks, or completes
- context is compressed or the process is restarted

## Hard rules

- Only operate from your own worktree and branch.
- Never edit `dev_docs/logs/*` or `dev_docs/progress/project_progress.md` directly.
- Never call `bd` directly for control-plane writes.
- Do not start a new phase without a valid `GATE_OPEN` and `target_commit`.
- Prefer `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`.

## Required actions

1. On `GATE_OPEN` or `PING`, send `ack`.
2. On long-running work, send `heartbeat` at least every 15 minutes and at meaningful interrupt points.
3. After `commit + push`, send `phase-complete` and include the current `branch`.
4. After context loss or restart, send `recovery-check` before doing any coding.

## Role boundaries

- Backend may record: `ack`, `heartbeat`, `phase-complete`, `recovery-check`.
- Backend must not record: `open-gate`, `state-sync-ok`, `ping`, `stale-detected`, `gate-close`.
- If a PM asks for a phase you are not authorized to enter, stop and wait.

## Payload checklist

- `milestone`
- `phase`
- `gate_id`
- `target_commit` when known from PM
- `last_seen_gate` for `recovery-check`
- `task` in one concrete sentence
- `branch` for `heartbeat` and `phase-complete`
- `commit` for `ack` and `phase-complete`
