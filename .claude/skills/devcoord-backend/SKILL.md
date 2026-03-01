---
name: devcoord-backend
description: Acknowledge NeoMAGI PM devcoord instructions and report backend phase progress through scripts/devcoord/coord.py. Use when acting as the backend teammate or when the request mentions GATE_OPEN, PING, ack, heartbeat, phase-complete, or recovery-check for backend work.
---

# Devcoord Backend

This skill defines the backend teammate's devcoord write path.

## Use this skill when

- acting as the NeoMAGI backend teammate
- a PM issues `GATE_OPEN`, `WAIT`, `RESUME`, or `PING`
- backend work starts, resumes, blocks, or completes
- context is compressed or the process is restarted

## Hard rules

- Only operate from your own worktree and branch.
- Before any devcoord write, verify `git rev-parse HEAD` matches the PM `target_commit`; if it does not, stop and report the mismatch instead of writing.
- Never edit `dev_docs/logs/*` or `dev_docs/progress/project_progress.md` directly.
- Never call `bd` directly for control-plane writes.
- Do not start a new phase without a valid `GATE_OPEN` and `target_commit`.
- Prefer `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`.
- Compute `HEAD` once in the same shell block you use for the write payload, and reuse that verified SHA for `commit` fields.

## Required actions

1. Before any write, run `git rev-parse --show-toplevel`, `git rev-parse --abbrev-ref HEAD`, and `git rev-parse HEAD`; only continue if `HEAD == target_commit`.
2. On `GATE_OPEN` or `PING`, send `ack`.
3. On long-running work, send `heartbeat` at least every 15 minutes and at meaningful interrupt points.
4. After `commit + push`, send `phase-complete` and include the current `branch`.
5. After context loss or restart, send `recovery-check` before doing any coding.

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
- `commit` for `ack` and `phase-complete`, taken from the verified current `HEAD`
