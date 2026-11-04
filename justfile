# Mecha task runner. `just --list` shows all recipes.

set windows-shell := ["pwsh", "-NoLogo", "-Command"]

default:
    @just --list

# Install Python and frontend dependencies, plus the gitleaks pre-commit hook
setup:
    uv sync
    pnpm -C web install
    uvx pre-commit install

# Run FastAPI (7878) and Vite (5173) together; the browser opens Vite with the session token
[unix]
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    pnpm -C web dev &
    uv run mecha serve --dev

# Build the frontend into src/mecha/static and the Python sdist + wheel
build: build-web
    uv build
    uv run python scripts/check_wheel.py dist

# Build only the frontend
build-web:
    pnpm -C web build

# Run all tests
test:
    uv run pytest
    pnpm -C web test

# Lint, format check and type check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    pnpm -C web lint
    pnpm -C web typecheck

# Apply formatters
fmt:
    uv run ruff format .
    uv run ruff check --fix .
    pnpm -C web format

# Scan the working tree and history for secrets
secrets:
    gitleaks git --redact --verbose .
    gitleaks dir --redact .

# Everything CI runs
ci: lint test build

# Build and serve the packaged frontend
serve: build-web
    uv run mecha serve
