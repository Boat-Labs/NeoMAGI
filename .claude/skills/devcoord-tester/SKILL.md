---
name: devcoord-tester
description: Acknowledge NeoMAGI PM devcoord instructions, report tester review progress, recover after restart, and submit gate review evidence through scripts/devcoord/coord.py. Use when acting as the tester teammate, when a PM opens a tester gate or sends PING, when tester review starts/pauses/finishes, when resuming after context loss or process restart, when a review report is committed and ready to register, or when the request mentions ack, heartbeat, recovery-check, or gate-review for validation and acceptance work.
---

# Devcoord Tester

This skill defines the tester teammate's devcoord write path.

## Hard rules

- Never review against an unpushed local backend state.
- Before acceptance, sync to the visible backend commit and confirm `git rev-parse HEAD`.
- Before any devcoord write, verify the current `HEAD` matches the gate `target_commit`; if not, sync or stop and report the mismatch instead of writing.
- Never edit `dev_docs/logs/phase1/*`, `dev_docs/logs/phase2/*`, or `dev_docs/progress/project_progress.md` directly.
- Never close gates yourself; tester can submit review evidence, PM owns `gate close`.
- Prefer grouped CLI commands (e.g., `command ack`, `gate review`). For machine-first payloads, use `apply <action> --payload-stdin`.

## Required actions

1. Before any write, confirm the review worktree is on the visible backend state by checking `git rev-parse --show-toplevel`, `git rev-parse --abbrev-ref HEAD`, and `git rev-parse HEAD`; only continue if `HEAD == target_commit`.
2. On `GATE_OPEN` or `PING`, send `command ack`.
3. On long reviews or test runs, send `event heartbeat`.
4. After restart or context loss, send `event recovery-check` before continuing.
5. When the review report is committed and visible, submit `gate review`.
6. After `gate review`, wait for PM to run `projection render -> projection audit -> gate close` unless PM issues a new gate or sync instruction.

## Role boundaries

- Tester may record: `command ack`, `event heartbeat`, `event recovery-check`, `gate review`.
- Tester must not record: `gate open`, `event state-sync-ok`, `event stale-detected`, `gate close`.
- If `STATE_SYNC_OK` has not been issued after a recovery event, remain in `WAIT`.

## Payload reference

See [references/payloads.md](references/payloads.md) for required fields and example JSON for each `apply` action.

## Error handling

- If `coord.py` rejects a payload (missing field, unknown action), fix the payload and retry — do not skip the write.
- If `HEAD != target_commit`, stop all work and report `blocked: HEAD mismatch` to the PM with both SHAs. Do not attempt devcoord writes or review work until the PM issues a new `target_commit` or `state-sync-ok`.
- If `STATE_SYNC_OK` has not been issued after a recovery event, remain in `WAIT` and do not proceed.
