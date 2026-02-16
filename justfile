# NeoMAGI Frontend Development Commands

frontend_dir := "src/frontend"

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
