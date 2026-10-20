"""Summary endpoint with an injected row source (no BigQuery)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime

from analytics_fixtures import CALLS, QUESTIONS, REASONS
from fastapi.testclient import TestClient
from voa_analytics import app as app_mod
from voa_core.config import load_config

UI_SA = "ui@example.iam.gserviceaccount.com"
EXTRA = "looker@example.iam.gserviceaccount.com"
# Thursday 15 Oct 2026, 10:00 in New York
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self):
        self.fetches: list[tuple[date, date]] = []

    def fetch(self, start, end):
        self.fetches.append((start, end))
        return CALLS, REASONS, QUESTIONS

    def refreshed_at(self):
        return "2026-10-15T13:45:00+00:00"


def client(env="test", source=None, clock=None, mono=None):
    cfg = load_config(
        environ={
            "VOA_ENV": env,
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__UI_SA": UI_SA,
        }
    )
    src = source or FakeSource()
    a = app_mod.create_app(
        cfg,
        src,
        clock=clock or (lambda: NOW),
        monotonic=mono,
        environ={"ANALYTICS_EXTRA_CALLERS": f" {EXTRA} , "},
    )
    c = TestClient(a)
    c.__enter__()
    return c, src


def bearer(email: str) -> dict:
    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return {"authorization": f"Bearer {part({'alg': 'RS256'})}.{part({'email': email})}.sig"}


def test_health():
    c, _ = client()
    assert c.get("/health").json() == {"ok": True}


def test_summary_returns_metrics():
    c, src = client()
    r = c.get("/analytics/summary", params={"days": 4})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"refreshed_at", "range", "outreach", "clinical", "workload"}
    assert body["refreshed_at"] == "2026-10-15T13:45:00+00:00"
    # 4 days ending today (clinic-local): 12..15 Oct
    assert src.fetches == [(date(2026, 10, 12), date(2026, 10, 15))]
    assert body["range"] == {
        "start_day": "2026-10-12",
        "end_day": "2026-10-15",
        "channel": "all",
        "calls": 7,
    }
    assert body["outreach"]["patients_reached"] == 3
    assert body["workload"]["override"]["n"] == 3


def test_summary_channel_and_validation():
    c, _ = client()
    assert (
        c.get("/analytics/summary", params={"days": 4, "channel": "sms"}).json()["range"]["calls"]
        == 1
    )
    assert c.get("/analytics/summary", params={"days": 0}).status_code == 422
    assert c.get("/analytics/summary", params={"days": 366}).status_code == 422
    assert c.get("/analytics/summary", params={"channel": "fax"}).status_code == 422


def test_summary_is_cached_for_60_seconds():
    t = [1000.0]
    c, src = client(mono=lambda: t[0])
    c.get("/analytics/summary")
    c.get("/analytics/summary")
    assert len(src.fetches) == 1
    c.get("/analytics/summary", params={"channel": "voice"})  # a different key
    assert len(src.fetches) == 2
    t[0] += 61
    c.get("/analytics/summary")
    assert len(src.fetches) == 3


def test_unknown_caller_forbidden_outside_test():
    c, src = client(env="demo")
    assert c.get("/analytics/summary").status_code == 403
    assert c.get("/analytics/summary", headers=bearer("intruder@example.org")).status_code == 403
    assert src.fetches == []


def test_ui_and_extra_callers_allowed():
    c, _ = client(env="demo")
    assert c.get("/analytics/summary", headers=bearer(UI_SA)).status_code == 200
    assert c.get("/analytics/summary", headers=bearer(EXTRA)).status_code == 200


def test_health_needs_no_caller_check():
    c, _ = client(env="demo")
    assert c.get("/health").status_code == 200


def test_bigquery_source_parameterised_query():
    class Row(dict):
        pass

    class Job:
        def __init__(self, rows):
            self.rows = rows

        def result(self):
            return self.rows

    class Client:
        def __init__(self):
            self.sql = []

        def query(self, sql, job_config):
            self.sql.append((sql, job_config))
            return Job([Row(call_id="c1", day=date(2026, 10, 13))])

    bq = Client()
    src = app_mod.BigQuerySource("proj-123", "voa_analytics", client=bq)
    calls, _, _ = src.fetch(date(2026, 10, 1), date(2026, 10, 14))
    assert calls == [{"call_id": "c1", "day": "2026-10-13"}]
    sql, cfg = bq.sql[0]
    assert sql.endswith("WHERE day BETWEEN @start AND @end")
    assert "`proj-123.voa_analytics.fact_calls`" in sql
    assert "patient_key" in sql and "*" not in sql
    assert [(p.name, p.type_, p.value) for p in cfg.query_parameters] == [
        ("start", "DATE", date(2026, 10, 1)),
        ("end", "DATE", date(2026, 10, 14)),
    ]
