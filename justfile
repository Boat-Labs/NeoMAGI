# NeoMAGI development commands

frontend_dir := "src/frontend"
beads_repo_dir := ".beads/dolt/NeoMAGI"

# Run linter checks
lint:
    uv run ruff check src/

# Auto-format code
format:
    uv run ruff format src/
    uv run ruff check --fix src/

# Start development server (default: safer for Telegram/WebSocket debugging)
dev:
    uv run uvicorn src.gateway.app:app --host 0.0.0.0 --port 19789 --timeout-graceful-shutdown 3

# Start development server with auto-reload
dev-reload:
    uv run uvicorn src.gateway.app:app --reload --host 0.0.0.0 --port 19789 --timeout-graceful-shutdown 3

# Initialize workspace with template files (idempotent)
init-workspace:
    uv run python -m src.infra.init_workspace

# Pull beads control-plane state from Dolt remote
beads-pull:
    cd {{beads_repo_dir}} && dolt pull origin main

# Push beads control-plane state to Dolt remote
beads-push:
    cd {{beads_repo_dir}} && dolt push origin main

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
