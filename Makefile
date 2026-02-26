.PHONY: help setup setup-hooks install install-dev sync lock lint format test run dev dev-agent dev-css dev-wa clean release css docs docs-dev

PYTHON := uv run python
UV := uv
TAILWIND := ./tailwindcss
CSS_IN := api/static/input.css
CSS_OUT := api/static/style.css

# Show available targets
help:
	@echo ""
	@echo "MPA — My Personal Agent"
	@echo ""
	@echo "  Setup & Dependencies:"
	@echo "    make setup        First-time setup (install, hooks, copy example configs)"
	@echo "    make setup-hooks  Install git pre-commit hooks (lint + format)"
	@echo "    make install      Install production dependencies"
	@echo "    make install-dev  Install all dependencies (including dev tools)"
	@echo "    make sync         Re-sync venv with lockfile"
	@echo "    make lock         Update lockfile after changing pyproject.toml"
	@echo ""
	@echo "  Development:"
	@echo "    make dev          Show instructions for running dev services"
	@echo "    make dev-agent    Run agent with auto-reload"
	@echo "    make dev-css      Run Tailwind CSS watcher"
	@echo "    make dev-wa       Build WhatsApp CLI (wacli)"
	@echo "    make docs-dev     Run docs site with hot reload"
	@echo ""
	@echo "  Quality:"
	@echo "    make lint         Lint with ruff"
	@echo "    make format       Format with ruff"
	@echo "    make test         Run tests"
	@echo ""
	@echo "  Build & Deploy:"
	@echo "    make run          Run the agent (production)"
	@echo "    make css          Build minified CSS"
	@echo "    make docs         Build documentation (static export)"
	@echo "    make clean        Remove venv and caches"
	@echo ""
	@echo "  Release:"
	@echo "    make release name=v0.1  Push and create a GitHub release"
	@echo ""

# First-time setup: create venv, install deps, copy example configs
setup: install-dev setup-hooks
	@test -f .env || cp .env.example .env
	@test -f config.yml || cp config.yml.example config.yml
	@echo "Done. Edit .env and config.yml with your secrets, then run: make run"

# Install git hooks (lint + format on commit)
setup-hooks:
	git config core.hooksPath .githooks

# Install production dependencies
install:
	$(UV) sync --no-dev

# Install all dependencies (including dev tools)
install-dev:
	$(UV) sync

# Update lockfile after changing pyproject.toml
lock:
	$(UV) lock

# Re-sync venv with lockfile
sync:
	$(UV) sync

# Lint
lint:
	$(UV) run ruff check .

# Format
format:
	$(UV) run ruff format .

# Run tests
test:
	$(UV) run pytest

# Run the agent (production)
run:
	$(UV) run python -m core.main

# Run in dev mode: instructions for running services in separate shells
dev:
	@echo ""
	@echo "Run each service in its own terminal (Ctrl-C stops each one cleanly):"
	@echo ""
	@echo "  1. Agent (auto-restart on code changes):"
	@echo "     make dev-agent"
	@echo ""
	@echo "  2. Tailwind CSS watcher:"
	@echo "     make dev-css"
	@echo ""
	@if [ -d tools/wacli ]; then \
		echo "  3. WhatsApp (wacli build):"; \
		echo "     make dev-wa"; \
		echo ""; \
	fi
	@echo "  Docs (optional):"
	@echo "     make docs-dev"
	@echo ""

# Dev: admin API with auto-reload on code changes (agent managed via UI)
dev-agent:
	PYTHONWARNINGS="ignore::UserWarning:multiprocessing.resource_tracker" \
	$(UV) run uvicorn core.main:app --reload --host 0.0.0.0 --port 8000 --log-level info \
		--reload-dir api --reload-dir core --reload-dir channels --reload-dir schema \
		--reload-dir skills --reload-dir tools --reload-dir voice

# Dev: Tailwind CSS watcher
dev-css:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --watch

# Dev: WhatsApp (wacli)
dev-wa:
	@if [ ! -x tools/wacli/dist/wacli ]; then \
		echo "Building wacli..."; \
		cd tools/wacli && pnpm -s build; \
	fi

# Remove venv and caches
clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Create a GitHub release and tag
release:
	@test -n "$(name)" || (echo "Usage: make release name=v0.x" && exit 1)
	git push
	gh release create "$(name)" --generate-notes --latest

# Build minified CSS (production)
css:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --minify

# Build documentation (static export)
docs:
	cd docs && npm ci && npm run build

# Dev: documentation with hot reload
docs-dev:
	cd docs && npm run dev
