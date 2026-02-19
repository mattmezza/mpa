.PHONY: setup install install-dev sync lock lint format test run dev dev-agent dev-css dev-wa clean release css cssd

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

# Run the agent
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
	@if [ -d tools/wa-bridge ]; then \
		echo "  3. WhatsApp bridge:"; \
		echo "     make dev-wa"; \
		echo ""; \
	fi

# Dev: agent with auto-restart on Python file changes
dev-agent:
	$(UV) run watchfiles --filter python 'python -m core.main' api core channels schema skills tools voice

# Dev: Tailwind CSS watcher
dev-css:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --watch

# Dev: WhatsApp bridge
dev-wa:
	@if [ ! -d tools/wa-bridge/node_modules ]; then \
		npm --prefix tools/wa-bridge install; \
	fi
	npm --prefix tools/wa-bridge run start

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

# Watch CSS files and rebuild on change (development)
cssd:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --watch
