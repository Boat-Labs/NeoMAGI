# NeoMAGI development commands

frontend_dir := "src/frontend"

# Run linter checks
lint:
    uv run ruff check src/

# Auto-format code
format:
    uv run ruff format src/
    uv run ruff check --fix src/

# Start development server
dev:
    uv run uvicorn src.gateway.app:app --reload --host 0.0.0.0 --port 19789

# Initialize workspace with template files (idempotent)
init-workspace:
    uv run python -m src.infra.init_workspace

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
