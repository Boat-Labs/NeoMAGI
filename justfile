# NeoMAGI development commands

frontend_dir := "src/frontend"
beads_repo_dir := ".beads/dolt/NeoMAGI"

# Run linter checks
lint:
    uv run ruff check src/
    uv run python -m src.infra.complexity_guard check

# Auto-format code
format:
    uv run ruff format src/
    uv run ruff check --fix src/

# Show current complexity snapshot
complexity-report:
    uv run python -m src.infra.complexity_guard report

# Refresh ratchet baseline after an intentional cleanup pass
complexity-baseline:
    uv run python -m src.infra.complexity_guard write-baseline

# Start development server (default: safer for Telegram/WebSocket debugging)
dev:
    uv run uvicorn src.gateway.app:app --host 0.0.0.0 --port 19789 --timeout-graceful-shutdown 3

# Start development server with auto-reload
dev-reload:
    uv run uvicorn src.gateway.app:app --reload --host 0.0.0.0 --port 19789 --timeout-graceful-shutdown 3

# Initialize workspace with template files (idempotent)
init-workspace:
    uv run python -m src.infra.init_workspace

# Explicitly seed SOUL.md and import it as active v0 into DB.
# Default template: just init-soul
# Custom source: just init-soul --from dev_docs/prompts/personal/SOUL.zhiliang-personal.reference.md
init-soul *ARGS:
    uv run python -m src.backend.cli init-soul {{ARGS}}

# Drop all user data in the current DB schema and rebuild it from migrations.
# Usage: just reset-user-db YES
reset-user-db confirm="NO":
    @if [ "{{confirm}}" != "YES" ]; then \
        echo "Refusing to reset user DB. This will DROP the active schema and all user data."; \
        echo "Run: just reset-user-db YES"; \
        exit 1; \
    fi
    uv run python -m src.backend.cli reset-user-db --yes

# Refresh JSONL backup of beads issue data (ADR 0052)
beads-backup:
    bd backup --force

# Show beads backup status
beads-backup-status:
    bd backup status

# Preview what a restore would do (dry-run)
beads-restore-dry-run:
    bd backup restore --dry-run

# [DEPRECATED] Was: dolt pull. Dolt remote sync is retired (ADR 0052).
# Transition: prints warning, no-op. Will be removed after 2026-04-08.
beads-pull:
    @echo "DEPRECATED: 'just beads-pull' is retired. Dolt remote sync is no longer used."
    @echo "Recovery path: git pull --rebase (to get latest .beads/backup/*), then 'bd init && bd backup restore'."
    @echo "See ADR 0052 for details."

# [DEPRECATED] Was: dolt push. Dolt remote sync is retired (ADR 0052).
# Transition: prints warning then delegates to beads-backup. Will be removed after 2026-04-08.
beads-push:
    @echo "DEPRECATED: 'just beads-push' is retired. Use 'just beads-backup' instead."
    @echo "Delegating to 'just beads-backup'..."
    @echo "Backup auto-creates a local commit. Just run 'git push' afterwards."
    @echo "See ADR 0052 for details."
    bd backup --force

# Start frontend dev server
dev-frontend:
    cd {{frontend_dir}} && pnpm dev

# Build frontend for production
build-frontend:
    cd {{frontend_dir}} && pnpm build

# Type-check frontend (no emit)
check-frontend:
    cd {{frontend_dir}} && pnpm tsc -b --noEmit

# Install frontend dependencies
install-frontend:
    cd {{frontend_dir}} && pnpm install

# Add a shadcn/ui component (usage: just add-component button)
add-component name:
    cd {{frontend_dir}} && pnpm dlx shadcn@latest add {{name}}

# Preview production build
preview-frontend:
    cd {{frontend_dir}} && pnpm preview

# Run all tests
test:
    uv run pytest tests/ -v

# Run integration tests only (requires PostgreSQL or testcontainers)
test-integration:
    uv run pytest tests/ -v -m integration

# Run unit tests only (no DB required)
test-unit:
    uv run pytest tests/ -v -m "not integration"

# Run frontend tests
test-frontend:
    cd {{frontend_dir}} && pnpm test -- --run

# Run doctor diagnostic checks
doctor:
    uv run python -m src.backend.cli doctor

# Run doctor with deep checks (provider connectivity, etc.)
doctor-deep:
    uv run python -m src.backend.cli doctor --deep

# Backup truth-source data (DB tables + workspace memory files)
backup *ARGS:
    uv run python scripts/backup.py {{ARGS}}

# Restore from backup (8-step recovery sequence)
restore *ARGS:
    uv run python scripts/restore.py {{ARGS}}

# TRUNCATE + full reindex of memory_entries
reindex *ARGS:
    uv run python -m src.backend.cli reindex {{ARGS}}

# Reconcile SOUL.md projection from DB
reconcile:
    uv run python -m src.backend.cli reconcile

# Show row counts for governance tables (skills, wrappers, soul)
check-governance-tables:
    uv run python -m src.backend.cli check-governance-tables
