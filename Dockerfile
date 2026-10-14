FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI for the worker (Option A). Installed outside $HOME so a non-root user can run it.
RUN HOME=/opt/claude bash -c 'curl -fsSL https://claude.ai/install.sh | bash' \
    && ln -s "$(readlink -f /opt/claude/.local/bin/claude)" /usr/local/bin/claude \
    && chmod -R a+rX /opt/claude

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN chmod -R a+rX /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    BRAIN_DIR=/data/brain \
    CACHE_DIR=/data/cache \
    HOME=/tmp \
    DISABLE_AUTOUPDATER=1
