.PHONY: setup install install-dev sync lock lint format test run clean release

PYTHON := uv run python
UV := uv

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

# Remove venv and caches
clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Create a GitHub release and tag
release:
	@test -n "$(name)" || (echo "Usage: make release name=v0.x" && exit 1)
	@gh release create "$(name)" --generate-notes --latest
