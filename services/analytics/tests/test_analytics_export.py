"""Export job orchestration with a MemoryStore and a fake loader (no BigQuery)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from test_analytics_flatten import PHI, TIMELINE, phi_call
from voa_analytics import export, schema
from voa_core.store import MemoryStore

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


class FakeLoader:
    def __init__(self):
        self.loads: list[tuple[str, list[dict]]] = []

    def load(self, table, rows):
        for r in rows:
            assert tuple(r) == table.column_names
        self.loads.append((table.name, list(rows)))


def seeded() -> MemoryStore:
    store = MemoryStore()

    async def seed():
        await store.set("calls", "CA0001", phi_call())
        await store.set("call_events", "CA0001", TIMELINE)
        await store.set(
            "calls",
            "CA0002",
            {"initiated_at": "2026-10-14T15:00:00+00:00", "patient_id": "P-2", "channel": "voice"},
        )
        await store.set("calls", "CA0003", {"patient_id": "P-3"})  # no time: skipped

    asyncio.run(seed())
    return store


def test_run_export_loads_every_table_in_order(caplog):
    loader = FakeLoader()
    counts = asyncio.run(
        export.run_export(seeded(), loader, key=b"k", tz="America/New_York", now=NOW)
    )
    assert counts == {"calls": 2, "reasons": 2, "questions": 2, "skipped": 1, "timelines": 1}
    assert [name for name, _ in loader.loads] == [t.name for t in schema.TABLES]
    loaded = dict(loader.loads)
    assert {r["call_id"] for r in loaded["fact_calls"]} == {"CA0001", "CA0002"}
    assert loaded["export_meta"] == [
        {
            "exported_at": "2026-10-15T06:00:00+00:00",
            "calls": 2,
            "reasons": 2,
            "questions": 2,
            "newest_initiated_at": "2026-10-14T15:00:00+00:00",
        }
    ]
    blob = json.dumps(loader.loads)
    for value in PHI.values():
        assert value not in blob
    # Logs carry counts only.
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "CA0001" not in logged and PHI["mrn"] not in logged


def test_empty_store_still_truncates_every_table():
    loader = FakeLoader()
    counts = asyncio.run(export.run_export(MemoryStore(), loader, key=b"k", tz="UTC", now=NOW))
    assert counts["calls"] == 0
    assert [(n, len(rows)) for n, rows in loader.loads] == [
        ("fact_calls", 0),
        ("fact_reasons", 0),
        ("fact_questions", 0),
        ("export_meta", 1),
    ]


def test_empty_key_refused():
    with pytest.raises(export.ExportError):
        asyncio.run(export.run_export(MemoryStore(), FakeLoader(), key=b"", tz="UTC"))


def test_hmac_key_from_env(monkeypatch):
    monkeypatch.setenv("VOA_SECRET_ANALYTICS_KEY", "fictional-key")
    assert export.hmac_key("p", {"ANALYTICS_HMAC_KEY": "secret://analytics-key"}) == (
        b"fictional-key"
    )


@pytest.mark.parametrize("env", [{}, {"ANALYTICS_HMAC_KEY": "  "}])
def test_hmac_key_missing(env):
    with pytest.raises(export.ExportError, match="ANALYTICS_HMAC_KEY"):
        export.hmac_key("p", env)


def test_hmac_key_empty_secret(monkeypatch):
    monkeypatch.setattr(export, "resolve_secret", lambda ref, project: "")
    with pytest.raises(export.ExportError, match="empty"):
        export.hmac_key("p", {"ANALYTICS_HMAC_KEY": "secret://analytics-key"})


def test_hmac_key_unreadable(monkeypatch):
    def boom(ref, project):
        raise PermissionError("denied")

    monkeypatch.setattr(export, "resolve_secret", boom)
    with pytest.raises(export.ExportError, match="PermissionError"):
        export.hmac_key("p", {"ANALYTICS_HMAC_KEY": "secret://analytics-key"})


def test_dataset_required():
    with pytest.raises(export.ExportError):
        export.dataset_name({})
    assert export.dataset_name({"ANALYTICS_DATASET": "voa_analytics"}) == "voa_analytics"


def test_bigquery_loader_job_config():
    """The loader passes explicit schemas, WRITE_TRUNCATE and day partitioning."""

    class Job:
        def result(self):
            return None

    class Client:
        def __init__(self):
            self.calls = []

        def load_table_from_json(self, rows, ref, job_config):
            self.calls.append(("load", ref, job_config, rows))
            return Job()

        def create_table(self, table, exists_ok):
            self.calls.append(("create", table.table_id, exists_ok))

        def query(self, sql):
            self.calls.append(("query", sql))
            return Job()

    client = Client()
    loader = export.BigQueryLoader("proj-123", "voa_analytics", client=client)
    loader.load(
        schema.FACT_REASONS, [{"call_id": "c", "day": "2026-10-13", "tier": "x", "text": "y"}]
    )
    _, ref, cfg, rows = client.calls[0]
    assert ref == "proj-123.voa_analytics.fact_reasons"
    assert cfg.write_disposition == "WRITE_TRUNCATE"
    assert [f.name for f in cfg.schema] == list(schema.FACT_REASONS.column_names)
    assert cfg.time_partitioning.field == "day"

    client.calls.clear()
    loader.load(schema.FACT_CALLS, [])
    assert client.calls[0][0] == "create" and client.calls[0][2] is True
    assert client.calls[1] == ("query", "TRUNCATE TABLE `proj-123.voa_analytics.fact_calls`")
