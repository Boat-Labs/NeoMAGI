# Devcoord PM Payload Reference

All commands via `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`.

## open-gate

```json
{"milestone": "m8", "phase": "p1", "gate_id": "m8-g0", "roles": "backend,tester", "target_commit": "<sha>"}
```

Required: `milestone`, `phase`, `gate_id`, `roles`, `target_commit`.

## state-sync-ok

```json
{"milestone": "m8", "role": "backend", "gate_id": "m8-g0", "target_commit": "<sha>"}
```

Required: `milestone`, `role`, `gate_id`, `target_commit`.

## ping

```json
{"milestone": "m8", "role": "backend", "gate_id": "m8-g0", "reason": "no heartbeat for 20 min"}
```

Required: `milestone`, `role`, `gate_id`, `reason`.

## unconfirmed-instruction

```json
{"milestone": "m8", "role": "backend", "gate_id": "m8-g0", "instruction": "GATE_OPEN m8-g0", "ping_count": 2}
```

Required: `milestone`, `role`, `gate_id`, `instruction`, `ping_count`.

## stale-detected

```json
{"milestone": "m8", "role": "backend", "gate_id": "m8-g0", "ping_count": 3, "action": "escalate"}
```

Required: `milestone`, `role`, `gate_id`, `action`. Optional: `ping_count`.

## log-pending

```json
{"milestone": "m8", "gate_id": "m8-g0", "reason": "teammate ACK not yet received, will backfill next turn"}
```

Required: `milestone`, `gate_id`, `reason`.

## gate-close

```json
{"milestone": "p2-m1", "gate_id": "p2-m1-g0", "phase": "p1", "result": "pass", "review_commit": "<sha>", "review_path": "dev_docs/reviews/phase2/p2-m1_g0_review.md"}
```

Required: `milestone`, `gate_id`, `phase`, `result`, `review_commit`, `review_path`.

## milestone-close

```json
{"milestone": "m8"}
```

Required: `milestone`.

## render

```json
{"milestone": "m8"}
```

Required: `milestone`. Generates a phase-aware log projection under `dev_docs/logs/phase1/` or `dev_docs/logs/phase2/`, plus `dev_docs/progress/project_progress.md`.

## audit

```json
{"milestone": "m8"}
```

Required: `milestone`. Returns JSON with `reconciled` boolean.
