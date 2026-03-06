# Devcoord Tester Payload Reference

All commands via `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`.

## ack

```json
{"milestone": "m8", "phase": "p1", "gate_id": "m8-g0", "target_commit": "<sha>", "commit": "<HEAD>", "task": "begin review of health endpoint"}
```

Required: `milestone`, `phase`, `gate_id`, `target_commit`, `commit`, `task`.

## heartbeat

```json
{"milestone": "m8", "phase": "p1", "gate_id": "m8-g0", "task": "running integration tests, 3/5 scenarios passed"}
```

Required: `milestone`, `phase`, `gate_id`, `task`.

## recovery-check

```json
{"milestone": "m8", "phase": "p1", "gate_id": "m8-g0", "last_seen_gate": "m8-g0", "task": "resuming review after restart"}
```

Required: `milestone`, `phase`, `gate_id`, `last_seen_gate`, `task`.

## gate-review

```json
{"milestone": "p2-m1", "phase": "p1", "gate_id": "p2-m1-g0", "result": "pass", "report_path": "dev_docs/reviews/phase2/p2-m1_g0_review.md", "report_commit": "<sha>", "target_commit": "<sha>"}
```

Required: `milestone`, `phase`, `gate_id`, `result`, `report_path`, `report_commit`, `target_commit`.
