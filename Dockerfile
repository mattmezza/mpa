FROM python:3.14-slim

WORKDIR /app

# System deps (ffmpeg for voice pipeline, curl for health checks, sqlite3 for memory)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates jq sqlite3 \
    bash tar gzip xz-utils \
    golang build-essential pkg-config \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Himalaya CLI (pre-built Rust binary for email management)
RUN curl -sSL https://raw.githubusercontent.com/pimalaya/himalaya/master/install.sh | sh

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies (cached layer — only re-runs when lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

# Install CLI tools for contacts (khard = CardDAV client, vdirsyncer = sync daemon)
RUN uv pip install --system khard vdirsyncer[google]

# Create non-root user
RUN groupadd --gid 10001 mpa && \
    useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash mpa

# Copy application code
COPY core/ core/
COPY channels/ channels/
COPY skills/ skills/
COPY tools/ tools/
COPY voice/ voice/
COPY api/ api/

# Build CSS with Tailwind CSS v4 standalone CLI
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then TW_ARCH="linux-arm64"; else TW_ARCH="linux-x64"; fi && \
    curl -sSLo /tmp/tailwindcss "https://github.com/tailwindlabs/tailwindcss/releases/download/v4.1.18/tailwindcss-$TW_ARCH" && \
    chmod +x /tmp/tailwindcss && \
    /tmp/tailwindcss --input api/static/input.css --output api/static/style.css --minify && \
    rm /tmp/tailwindcss

# CLI config directories
RUN mkdir -p /home/mpa/.config/himalaya /home/mpa/.config/khard /home/mpa/.config/vdirsyncer \
    /home/mpa/.local/share/vdirsyncer /app/data \
    && chown -R mpa:mpa /home/mpa /app

# Build wacli
RUN corepack enable && \
    corepack prepare pnpm@9.15.2 --activate && \
    cd tools/wacli && \
    pnpm -s build

USER mpa

EXPOSE 8000
CMD ["uv", "run", "python", "-m", "core.main"]
