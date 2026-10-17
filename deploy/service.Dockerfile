# One image recipe for every Python service in the workspace.
#   docker build -f deploy/service.Dockerfile --build-arg SERVICE=gateway .
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/uv
ARG SERVICE
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    VOA_CONFIG_DIR=/app/config VOA_SERVICE=${SERVICE}
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
COPY spikes ./spikes
# Only non-identifying config ships in the image; the rest arrives as VOA__* env vars.
COPY config/app.yaml ./config/app.yaml
COPY config/campaigns ./config/campaigns
RUN uv sync --frozen --no-dev --package voa-${SERVICE} \
 && useradd --uid 10001 --no-create-home app
USER app
ENV PORT=8080
# One worker per instance: call and run state must not be split across processes (review C-8).
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn voa_${VOA_SERVICE}.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --ws-ping-interval 20 --timeout-graceful-shutdown 8"]
