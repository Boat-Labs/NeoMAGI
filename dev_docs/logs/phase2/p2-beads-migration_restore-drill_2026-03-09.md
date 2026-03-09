# Beads Git-JSONL Backup Migration — Restore Drill Report

- Date: 2026-03-09
- Plan: `dev_docs/plans/phase2/p2-beads_git-jsonl-backup-migration_2026-03-08.md` Slice D
- ADR: `decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md`

## Environment

- Source repo: `/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI` (main branch)
- Test dir: `/tmp/beads_restore_test_1772963131` (disposable, git init, no `.beads/dolt/`)
- bd version: as installed at drill time

## Procedure

### 1. Backup refresh (source repo)

```
$ bd backup --force
Backup complete: 118 issues, 1092 events, 0 comments, 109 deps, 389 labels, 11 config
```

### 2. Backup status

```
$ bd backup status
JSONL Backup:
  Last backup: 2026-03-08T09:45:00Z
  Counts: 118 issues, 1092 events, 0 comments, 109 deps, 389 labels, 11 config
Warning: dolt auto-push failed: ... fatal: remote 'origin' not found.
```

Note: The `dolt auto-push` warning confirms Dolt remote is unreachable — exactly the condition motivating this migration.

### 3. Git tracks backup changes

```
$ git status --short .beads/backup/
(clean — backup files already committed)
```

### 4. Disposable test directory setup

- Created `/tmp/beads_restore_test_1772963131` with `git init`
- Copied `.beads/backup/*.jsonl` and `backup_state.json` from source repo
- Confirmed `.beads/dolt/` does NOT exist

### 5. Init + dry-run restore

```
$ bd init
  ✓ bd initialized successfully!
  Backend: dolt
  Database: beads_restore_test_1772963131

$ bd backup restore --dry-run
! Dry run — no changes made
  Issues:       118
  Comments:     0
  Dependencies: 109
  Labels:       389
  Events:       1092
  Config:       11
```

### 6. Actual restore

```
$ bd backup restore
✓ Restore complete
  Issues:       118
  Comments:     0
  Dependencies: 109
  Labels:       389
  Events:       1092
  Config:       11
```

### 7. Verification

```
$ bd list --json --all | python3 -c "import sys, json; data=json.load(sys.stdin); print(f'Total issues (all statuses): {len(data)}')"
Total issues (all statuses): 118
```

Cross-check:

| Source | Count |
|--------|-------|
| Source repo `bd list --json --all` | 118 |
| `.beads/backup/issues.jsonl` lines | 118 |
| Restored `bd list --json --all` | 118 |

All three match.

## Limitations

- `bd list` (without `--all`) defaults to open issues only (showed 4); `--all` is required for total count verification.
- `bd init` derives the database name from the directory name; directory names with dots (e.g., `tmp.xxx`) cause Dolt to reject the name. Use alphanumeric-only directory names for restore.
- The `dolt auto-push` warning in `bd backup status` is cosmetic; it does not affect JSONL backup correctness.

## Conclusion

JSONL backup + restore path is verified: a clean environment with no pre-existing `.beads/dolt/` can fully recover all 118 issues from `.beads/backup/*.jsonl` via `bd init && bd backup restore`.
