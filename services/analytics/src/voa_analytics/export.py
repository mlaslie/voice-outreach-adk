"""Cloud Run Job: Firestore ``calls`` + ``call_events`` -> de-identified BigQuery tables.

    python -m voa_analytics.export

Each run is a FULL refresh: every call is read, flattened and loaded with
WRITE_TRUNCATE, so a run is idempotent and a failed run is fixed by the next one.
That is the right trade at clinic scale (thousands of calls). A production
deployment with much larger volume would switch to an incremental export (calls
updated since the last run, MERGE into the tables) driven by ``updated_at``.

Environment:
  ANALYTICS_DATASET    BigQuery dataset id in the config's project (e.g. voa_analytics)
  ANALYTICS_HMAC_KEY   secret:// reference to the patient-key HMAC secret
  VOA_ENV, VOA__GCP__PROJECT_ID, VOA__SITE__TIMEZONE ... as for every service

Logs counts only: never a call id, a patient key or any row content.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from voa_core.config import load_config, resolve_secret
from voa_core.store import DocStore

from . import schema
from .flatten import flatten

log = logging.getLogger("voa_analytics.export")

CALLS, EVENTS = "calls", "call_events"


class ExportError(RuntimeError):
    """A configuration problem that makes the export impossible."""


class Loader(Protocol):
    def load(self, table: schema.Table, rows: Sequence[Mapping[str, Any]]) -> None:
        """Replace ``table``'s contents with ``rows`` (WRITE_TRUNCATE)."""
        ...


class BigQueryLoader:
    """Loads with explicit schemas and the fact tables' day partitioning."""

    def __init__(self, project_id: str, dataset: str, client: Any = None):
        from google.cloud import bigquery

        self._bq = bigquery
        self.client = client or bigquery.Client(project=project_id)
        self.project_id = project_id
        self.dataset = dataset

    def _schema(self, table: schema.Table) -> list[Any]:
        return [
            self._bq.SchemaField(c.name, c.type, mode=c.mode, description=c.description)
            for c in table.columns
        ]

    def _add_new_columns(self, ref: str, table: schema.Table) -> list[str]:
        """Add columns the code has and the existing table lacks (e.g. fact_calls
        ``language``), as an explicit schema update before the load. A WRITE_TRUNCATE
        load of a whole table may not carry schema_update_options, and the
        empty-table path below never loads at all, so neither would add them.
        Only NULLABLE columns can be added; returns the names added."""
        from google.api_core.exceptions import NotFound

        try:
            existing = self.client.get_table(ref)
        except NotFound:
            return []  # created by this load, with the full schema
        have = {f.name for f in existing.schema}
        new = [f for f in self._schema(table) if f.name not in have and f.mode == "NULLABLE"]
        if new:
            existing.schema = [*existing.schema, *new]
            self.client.update_table(existing, ["schema"])
            log.info(
                "analytics columns added",
                extra={"event": "analytics.columns_added", "table": table.name, "n": len(new)},
            )
        return [f.name for f in new]

    def load(self, table: schema.Table, rows: Sequence[Mapping[str, Any]]) -> None:
        bq = self._bq
        ref = f"{self.project_id}.{self.dataset}.{table.name}"
        self._add_new_columns(ref, table)
        config = bq.LoadJobConfig(
            schema=self._schema(table),
            write_disposition=bq.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bq.CreateDisposition.CREATE_IF_NEEDED,
            source_format=bq.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        if table.partition_field:
            config.time_partitioning = bq.TimePartitioning(
                type_=bq.TimePartitioningType.DAY, field=table.partition_field
            )
        if not rows:
            # A load needs at least one row; an empty table is truncated instead,
            # so deleted calls disappear from the analytics too.
            t = bq.Table(ref, schema=self._schema(table))
            if table.partition_field:
                t.time_partitioning = config.time_partitioning
            self.client.create_table(t, exists_ok=True)
            self.client.query(f"TRUNCATE TABLE `{ref}`").result()  # noqa: S608 - names from code
            return
        self.client.load_table_from_json(list(rows), ref, job_config=config).result()


async def read_all(store: DocStore) -> tuple[list[tuple[str, dict]], dict[str, dict]]:
    calls = await store.query(CALLS)
    events = {doc_id: doc for doc_id, doc in await store.query(EVENTS)}
    return calls, events


async def run_export(
    store: DocStore,
    loader: Loader,
    *,
    key: bytes,
    tz: str,
    now: datetime | None = None,
) -> dict[str, int]:
    """Read, flatten and load. Returns the row counts (also logged)."""
    if not key:
        raise ExportError("the analytics HMAC key is empty")
    calls, events = await read_all(store)
    flat = flatten(calls, events, key=key, tz=tz)
    newest = max((r["initiated_at"] for r in flat.calls if r.get("initiated_at")), default=None)
    meta = {
        "exported_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "calls": len(flat.calls),
        "reasons": len(flat.reasons),
        "questions": len(flat.questions),
        "newest_initiated_at": newest,
    }
    loader.load(schema.FACT_CALLS, flat.calls)
    loader.load(schema.FACT_REASONS, flat.reasons)
    loader.load(schema.FACT_QUESTIONS, flat.questions)
    # Written last: "data as of" only moves once every table is loaded.
    loader.load(schema.EXPORT_META, [meta])
    counts = {
        "calls": len(flat.calls),
        "reasons": len(flat.reasons),
        "questions": len(flat.questions),
        "skipped": flat.skipped,
        "timelines": len(events),
    }
    log.info("analytics export done", extra={"event": "analytics.export", **counts})
    return counts


def hmac_key(project_id: str, environ: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if environ is None else environ
    ref = (env.get("ANALYTICS_HMAC_KEY") or "").strip()
    if not ref:
        raise ExportError("ANALYTICS_HMAC_KEY is not set (expected secret://analytics-key)")
    try:
        value = resolve_secret(ref, project_id)
    except Exception as exc:  # never echo the value; the name is enough
        raise ExportError(f"could not read the analytics HMAC key: {type(exc).__name__}") from None
    if not value or not value.strip():
        raise ExportError("the analytics HMAC key secret is empty")
    return value.strip().encode("utf-8")


def dataset_name(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    ds = (env.get("ANALYTICS_DATASET") or "").strip()
    if not ds:
        raise ExportError("ANALYTICS_DATASET is not set")
    return ds


def main() -> int:
    from voa_core.logging import configure_logging
    from voa_core.store import FirestoreStore

    cfg = load_config()
    configure_logging(cfg.log_level, cfg.gcp.project_id)
    try:
        dataset = dataset_name()
        key = hmac_key(cfg.gcp.project_id)
    except ExportError as exc:
        log.error(str(exc), extra={"event": "analytics.export_config"})
        return 2
    store = FirestoreStore(cfg.gcp.project_id)
    loader = BigQueryLoader(cfg.gcp.project_id, dataset)
    asyncio.run(run_export(store, loader, key=key, tz=cfg.site.timezone))
    return 0


if __name__ == "__main__":
    sys.exit(main())
