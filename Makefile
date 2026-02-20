.PHONY: setup install install-dev sync lock lint format test run dev dev-agent dev-css dev-wa clean release css

PYTHON := uv run python
UV := uv
TAILWIND := ./tailwindcss
CSS_IN := api/static/input.css
CSS_OUT := api/static/style.css

# First-time setup: create venv, install deps, copy example configs
setup: install-dev
	@test -f .env || cp .env.example .env
	@test -f config.yml || cp config.yml.example config.yml
	@echo "Done. Edit .env and config.yml with your secrets, then run: make run"

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
	@gh release create "$(name)" --generate-notes --latest

# Build minified CSS (production)
css:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --minify
