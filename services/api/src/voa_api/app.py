"""voa-api: private service behind Cloud Run IAM (no public ingress for /api)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from voa_core.config import AppConfig, load_config
from voa_core.logging import bind, configure_logging, parse_cloud_trace_header

from . import routes_calls, routes_list, routes_tasks
from .context import Api

log = logging.getLogger("voa_api")


def create_app(cfg: AppConfig | None = None, api: Api | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = cfg or load_config()
        configure_logging(c.log_level, c.gcp.project_id)
        app.state.api = api or Api.build(c)
        yield

    debug = (cfg.env if cfg else "") in ("dev", "test")
    app = FastAPI(
        title="voa-api",
        lifespan=lifespan,
        docs_url="/docs" if debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if debug else None,
    )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        t, s = parse_cloud_trace_header(request.headers.get("x-cloud-trace-context"))
        with bind(trace=t, span_id=s):
            return await call_next(request)

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.include_router(routes_calls.router)
    app.include_router(routes_list.router)
    app.include_router(routes_tasks.router)
    try:
        from . import routes_sms

        app.include_router(routes_sms.router)
    except ImportError:  # SMS module lands in phase 5
        pass

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"event": "http.error", "path": request.url.path})
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app


app = create_app()
