# Build from the repo root:  docker build -f spikes/voice/Dockerfile .
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY spikes/voice ./spikes/voice
COPY config/app.yaml ./config/app.yaml
RUN uv sync --frozen --no-dev --package voa-voice-spike \
 && useradd --uid 10001 --no-create-home app
USER app
ENV VOA_CONFIG_DIR=/app/config PORT=8080
# One worker: in-process call state must not be split across processes.
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn voice_spike.server:app --host 0.0.0.0 --port ${PORT} --workers 1 --ws-ping-interval 20"]
