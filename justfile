# NeoMAGI development commands

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

# Initialize workspace with template files
init-workspace:
    @echo "TODO: implement workspace initialization"
