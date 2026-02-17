FROM python:3.14-slim

WORKDIR /app

# System deps (ffmpeg for voice pipeline, curl for health checks, sqlite3 for memory)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates jq sqlite3 \
    bash tar gzip xz-utils \
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

# Copy application code
COPY core/ core/
COPY channels/ channels/
COPY skills/ skills/
COPY tools/ tools/
COPY voice/ voice/
COPY api/ api/

# CLI config directories
RUN mkdir -p /root/.config/himalaya /root/.config/khard /root/.config/vdirsyncer \
    /root/.local/share/vdirsyncer

EXPOSE 8000
CMD ["uv", "run", "python", "-m", "core.main"]
