"""voa-analytics: private, read-only summary API over the de-identified tables.

Cloud Run IAM verifies the caller's Google ID token (the service is
``--no-allow-unauthenticated``). Here we only check WHICH identity called: the
admin console's service account (``VOA__SERVICES__ADMIN_UI_SA``) or one listed in
``ANALYTICS_EXTRA_CALLERS`` (comma-separated). Skipped in ``dev``/``test``.

Endpoints:
  GET /health
  GET /analytics/summary?days=30&channel=all|voice|sms

Queries are fixed and parameterised (``WHERE day BETWEEN @start AND @end``) and
select only the columns the metrics use. Results are cached for 60 s per
(days, channel, today).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from voa_core.config import AppConfig, load_config
from voa_core.logging import bind, configure_logging, parse_cloud_trace_header

from . import metrics, schema

log = logging.getLogger("voa_analytics")

CACHE_SECONDS = 60.0

CALL_COLUMNS: tuple[str, ...] = (
    "call_id",
    "patient_key",
    "entry_id",
    "placed_via",
    "channel",
    "day",
    "initiated_at",
    "outcome",
    "call_status",
    "answered",
    "answered_by",
    "dob_result",
    "follow_up",
    "overridden",
    "override_direction",
    "review_status",
    "signed_off_at",
    "minutes_to_signoff",
    "sms_follow_up_status",
    "pain_score",
    "fever",
    "incision",
    "swelling",
    "swelling_urgent",
    "sleeping_well",
    "sugar_status",
)
REASON_COLUMNS: tuple[str, ...] = ("call_id", "day", "tier", "text")
QUESTION_COLUMNS: tuple[str, ...] = ("call_id", "day", "category", "priority")

Rows = list[dict[str, Any]]


class RowSource(Protocol):
    def fetch(self, start: date, end: date) -> tuple[Rows, Rows, Rows]:
        """(calls, reasons, questions) with ``day`` in [start, end]."""
        ...

    def refreshed_at(self) -> str | None:
        """When the data was last exported (ISO), or None if unknown."""
        ...


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


class BigQuerySource:
    """Reads the export's tables. Table and column names come from code only."""

    def __init__(self, project_id: str, dataset: str, client: Any = None):
        from google.cloud import bigquery

        self._bq = bigquery
        self.client = client or bigquery.Client(project=project_id)
        self.prefix = f"{project_id}.{dataset}"

    def _rows(self, sql: str, params: list[Any] | None = None) -> Rows:
        cfg = self._bq.QueryJobConfig(query_parameters=params or [])
        return [
            {k: _plain(v) for k, v in dict(r.items()).items()}
            for r in self.client.query(sql, job_config=cfg).result()
        ]

    def _range(self, table: schema.Table, cols: tuple[str, ...], start: date, end: date) -> Rows:
        unknown = set(cols) - set(table.column_names)
        if unknown:
            raise ValueError(f"not in {table.name}: {sorted(unknown)}")
        sql = (
            f"SELECT {', '.join(cols)} FROM `{self.prefix}.{table.name}` "  # noqa: S608
            "WHERE day BETWEEN @start AND @end"
        )
        params = [
            self._bq.ScalarQueryParameter("start", "DATE", start),
            self._bq.ScalarQueryParameter("end", "DATE", end),
        ]
        return self._rows(sql, params)

    def fetch(self, start: date, end: date) -> tuple[Rows, Rows, Rows]:
        return (
            self._range(schema.FACT_CALLS, CALL_COLUMNS, start, end),
            self._range(schema.FACT_REASONS, REASON_COLUMNS, start, end),
            self._range(schema.FACT_QUESTIONS, QUESTION_COLUMNS, start, end),
        )

    def refreshed_at(self) -> str | None:
        sql = f"SELECT MAX(exported_at) AS exported FROM `{self.prefix}.{schema.EXPORT_META.name}`"  # noqa: S608
        try:
            rows = self._rows(sql)
        except Exception:  # the job has not run yet: no table
            log.warning("no export metadata", extra={"event": "analytics.no_meta"}, exc_info=True)
            return None
        return rows[0]["exported"] if rows else None


# --------------------------------------------------------------------------- auth


def caller_email(request: Request) -> str | None:
    """The calling identity's e-mail from the (already verified) ID token.
    Decode only: Cloud Run IAM has checked the signature. Same as voa_api.auth."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = auth.split(" ", 1)[1].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims.get("email")
    except Exception:
        return None


def allowed_callers(cfg: AppConfig, environ: Mapping[str, str]) -> frozenset[str]:
    extra = [e.strip() for e in (environ.get("ANALYTICS_EXTRA_CALLERS") or "").split(",")]
    return frozenset(e for e in [cfg.services.admin_ui_sa, *extra] if e)


# --------------------------------------------------------------------------- app


@dataclass
class Ctx:
    cfg: AppConfig
    source: RowSource
    allowed: frozenset[str]
    clock: Callable[[], datetime]
    monotonic: Callable[[], float] = time.monotonic
    cache: dict[tuple, tuple[float, dict[str, Any]]] = field(default_factory=dict)

    def today(self) -> date:
        return self.clock().astimezone(ZoneInfo(self.cfg.site.timezone)).date()


def _ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def require_caller(request: Request, ctx: Ctx = Depends(_ctx)) -> None:  # noqa: B008
    if ctx.cfg.env in ("dev", "test"):
        return
    if caller_email(request) not in ctx.allowed:
        raise HTTPException(403, "caller not permitted")


def create_app(
    cfg: AppConfig | None = None,
    source: RowSource | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    env = dict(os.environ if environ is None else environ)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = cfg or load_config()
        configure_logging(c.log_level, c.gcp.project_id)
        src = source
        if src is None:
            dataset = (env.get("ANALYTICS_DATASET") or "").strip()
            if not dataset:
                raise RuntimeError("ANALYTICS_DATASET is not set")
            src = BigQuerySource(c.gcp.project_id, dataset)
        app.state.ctx = Ctx(
            cfg=c,
            source=src,
            allowed=allowed_callers(c, env),
            clock=clock or (lambda: datetime.now(UTC)),
            monotonic=monotonic or time.monotonic,
        )
        yield

    debug = (cfg.env if cfg else "") in ("dev", "test")
    app = FastAPI(
        title="voa-analytics",
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

    @app.get("/analytics/summary", dependencies=[Depends(require_caller)])
    async def summary(
        days: int = Query(30, ge=1, le=365),
        channel: Literal["all", "voice", "sms"] = "all",
        ctx: Ctx = Depends(_ctx),  # noqa: B008
    ):
        today = ctx.today()
        key = (days, channel, today)
        hit = ctx.cache.get(key)
        if hit and hit[0] > ctx.monotonic():
            return hit[1]
        start = today - timedelta(days=days - 1)
        calls, reasons, questions = await asyncio.to_thread(ctx.source.fetch, start, today)
        refreshed = await asyncio.to_thread(ctx.source.refreshed_at)
        body = {
            "refreshed_at": refreshed,
            **metrics.compute(
                calls,
                reasons,
                questions,
                start_day=start,
                end_day=today,
                channel=channel,
                today=today,
            ),
        }
        # Drop expired entries so the cache stays as small as the key space.
        now = ctx.monotonic()
        for k in [k for k, (exp, _) in ctx.cache.items() if exp <= now]:
            del ctx.cache[k]
        ctx.cache[key] = (now + CACHE_SECONDS, body)
        log.info(
            "analytics summary",
            extra={"event": "analytics.summary", "days": days, "rows": len(calls)},
        )
        return body

    return app


app = create_app()
