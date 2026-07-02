.PHONY: help setup setup-hooks install install-dev sync lock lint format test run repl dev dev-agent dev-css dev-wa kokoro clean release css docs docs-dev

PORT := 8001
PYTHON := uv run python
UV := uv
TAILWIND := ./tailwindcss
CSS_IN := api/static/input.css
CSS_OUT := api/static/style.css

# Pinned upstream wacli (github.com/openclaw/wacli). Keep in sync with Dockerfile.
WACLI_VERSION := v0.11.0

# Kokoro offline TTS model (github.com/thewh1teagle/kokoro-onnx releases).
# Keep the version + paths in sync with the Dockerfile and KokoroConfig.
KOKORO_DIR := models/kokoro
KOKORO_BASE_URL := https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0

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
	@echo "    make kokoro       Install Kokoro offline TTS + download its model (local runs)"
	@echo ""
	@echo "  Development:"
	@echo "    make repl         Chat with the agent from the terminal (no Telegram)"
	@echo "                      (make repl AGENT=<slug> to test a specific agent)"
	@echo "                      (make repl YOLO=1 to auto-approve all permissions)"
	@echo "    make dev          Show instructions for running dev services"
	@echo "    make dev-agent    Run agent with auto-reload"
	@echo "    make dev-css      Run Tailwind CSS watcher"
	@echo "    make dev-wa       Install WhatsApp CLI (wacli) from upstream"
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
	@echo "  Website (humux.dev marketing site):"
	@echo "    make www-dev       Local dev server (port 8080)"
	@echo "    make www-build     Build production CSS"
	@echo "    make www-watch     Watch Tailwind CSS changes"
	@echo "    make www-deploy    Deploy to Cloudflare Pages"
	@echo "    make www-release name=www-v0.1  Tag & deploy new version"
	@echo ""
	@echo "  Release (MPA):"
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

# Local REPL — chat with the agent from the terminal (no Telegram)
repl:
	$(PYTHON) -m core.repl $(if $(AGENT),--agent $(AGENT),) $(if $(YOLO),--yolo,)

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
	@echo "  3. WhatsApp (wacli install):"
	@echo "     make dev-wa"
	@echo ""
	@echo "  Docs (optional):"
	@echo "     make docs-dev"
	@echo ""

# Dev: admin API with auto-reload on code changes (agent managed via UI)
dev-agent:
	PYTHONWARNINGS="ignore::UserWarning:multiprocessing.resource_tracker" \
	$(UV) run uvicorn core.main:app --reload --host 0.0.0.0 --port $(PORT) --log-level info \
		--reload-dir api --reload-dir core --reload-dir channels --reload-dir schema \
		--reload-dir skills --reload-dir tools --reload-dir voice

# Dev: Tailwind CSS watcher
dev-css:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --watch

# Dev: WhatsApp (wacli) — install the pinned upstream binary into $GOBIN/~/go/bin.
# Needs Go + a C toolchain (CGO, sqlite_fts5). Override WACLI_BIN to point the
# agent elsewhere; otherwise core/wacli.py resolves it from PATH or ~/go/bin.
dev-wa:
	@command -v wacli >/dev/null 2>&1 || test -x "$(HOME)/go/bin/wacli" || { \
		echo "Installing wacli $(WACLI_VERSION) from github.com/openclaw/wacli..."; \
		CGO_ENABLED=1 CGO_CFLAGS="-Wno-error=missing-braces" \
			go install -tags sqlite_fts5 github.com/openclaw/wacli/cmd/wacli@$(WACLI_VERSION); \
	}
	@wacli version 2>/dev/null || "$(HOME)/go/bin/wacli" version

# Install the Kokoro offline TTS extra + download its model for local (non-Docker)
# runs. The Docker image bundles these via INSTALL_KOKORO. After this, set
# voice.backend: kokoro and restart. Idempotent — existing files are kept.
kokoro:
	$(UV) sync --extra kokoro
	@mkdir -p $(KOKORO_DIR)
	@test -f $(KOKORO_DIR)/kokoro-v1.0.onnx || \
		curl -fL $(KOKORO_BASE_URL)/kokoro-v1.0.onnx -o $(KOKORO_DIR)/kokoro-v1.0.onnx
	@test -f $(KOKORO_DIR)/voices-v1.0.bin || \
		curl -fL $(KOKORO_BASE_URL)/voices-v1.0.bin -o $(KOKORO_DIR)/voices-v1.0.bin
	@echo "Kokoro ready in $(KOKORO_DIR). Set voice.backend: kokoro and restart the agent."

# Website (www/) — marketing site for humux
.PHONY: www-dev www-build www-watch www-deploy www-release

www-dev:
	cd www && make dev

www-build:
	cd www && make build

www-watch:
	cd www && make watch

www-deploy:
	cd www && make deploy

www-release:
	@test -n "$(name)" || (echo "Usage: make www-release name=www-v0.1" && exit 1)
	cd www && git add -A && git commit -m "www: $(name)" || true
	git tag "$(name)"
	git push origin "$(name)"
	@echo "Tag $(name) pushed. GHA workflow www-deploy.yml will build and deploy."

# Also support: make release name=www-v0.1 (legacy)
release:
	@test -n "$(name)" || (echo "Usage: make release name=v0.x (for mpa) or make www-release name=www-v0.x (for website)" && exit 1); \
	case "$(name)" in www-*) make www-release name=$(name) ;; *) make mpa-release name=$(name) ;; esac

mpa-release:
	@test -n "$(name)" || (echo "Usage: make mpa-release name=v0.x" && exit 1)
	ver=$$(echo "$(name)" | sed 's/^v//'); \
	case "$$ver" in *.*.*) ;; *.*) ver="$$ver.0" ;; *) ver="$$ver.0.0" ;; esac; \
	sed -i.bak -E "s/^version = \".*\"/version = \"$$ver\"/" pyproject.toml && rm -f pyproject.toml.bak; \
	echo "Set pyproject.toml version to $$ver"
	@git diff --quiet pyproject.toml || (git add pyproject.toml && git commit -m "chore: bump version to $(name)")
	git push
	gh release create "$(name)" --generate-notes --latest

# Remove venv and caches
clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Build minified CSS (production)
css:
	$(TAILWIND) --input $(CSS_IN) --output $(CSS_OUT) --minify

# Build documentation (static export)
docs:
	cd docs && npm ci && npm run build

# Dev: documentation with hot reload
docs-dev:
	cd docs && npm run dev
