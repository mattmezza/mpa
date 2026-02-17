FROM python:3.14-slim

WORKDIR /app

# System deps (ffmpeg for future voice pipeline, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates jq \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies (cached layer — only re-runs when lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY core/ core/
COPY channels/ channels/
COPY skills/ skills/
COPY tools/ tools/
COPY voice/ voice/
COPY api/ api/

# CLI config directories (for future himalaya/khard/vdirsyncer)
RUN mkdir -p /root/.config/himalaya /root/.config/khard /root/.config/vdirsyncer

EXPOSE 8000
CMD ["uv", "run", "python", "-m", "core.main"]
