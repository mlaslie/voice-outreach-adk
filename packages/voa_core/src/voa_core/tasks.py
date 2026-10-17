"""Async work handoff: Cloud Tasks in production, an in-memory queue in tests.

Every task is an authenticated (OIDC) HTTP POST to the private API. Named tasks
give ~24 h de-duplication, so retried webhooks don't double-enqueue: pass a
stable ``name`` (e.g. ``dial-<entry>-<attempt>``) whenever duplicates matter.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

log = logging.getLogger(__name__)
_NAME_OK = re.compile(r"[^A-Za-z0-9_-]")


class TaskQueue(Protocol):
    async def enqueue(
        self,
        queue: str,
        path: str,
        payload: dict[str, Any],
        *,
        name: str | None = None,
        delay_s: float = 0,
    ) -> bool:
        """Returns False when a task with this name already exists (duplicate)."""
        ...


@dataclass
class MemoryTaskQueue:
    """Records tasks; tests drain them by calling the API handlers directly."""

    tasks: list[dict[str, Any]] = field(default_factory=list)
    _names: set[str] = field(default_factory=set)

    async def enqueue(self, queue, path, payload, *, name=None, delay_s=0):
        if name and name in self._names:
            return False
        if name:
            self._names.add(name)
        self.tasks.append(
            {"queue": queue, "path": path, "payload": payload, "name": name, "delay_s": delay_s}
        )
        return True


class CloudTaskQueue:
    def __init__(self, *, project_id: str, region: str, target_base_url: str, invoker_sa: str):
        from google.cloud import tasks_v2

        self._tasks = tasks_v2
        self._client = tasks_v2.CloudTasksAsyncClient()
        self._project, self._region = project_id, region
        self._base = target_base_url.rstrip("/")
        self._sa = invoker_sa

    async def enqueue(self, queue, path, payload, *, name=None, delay_s=0):
        from google.api_core.exceptions import AlreadyExists
        from google.protobuf import timestamp_pb2

        parent = self._client.queue_path(self._project, self._region, queue)
        task: dict[str, Any] = {
            "http_request": {
                "http_method": self._tasks.HttpMethod.POST,
                "url": f"{self._base}{path}",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                "oidc_token": {"service_account_email": self._sa, "audience": self._base},
            }
        }
        if name:
            task["name"] = f"{parent}/tasks/{_NAME_OK.sub('-', name)[:500]}"
        if delay_s > 0:
            ts = timestamp_pb2.Timestamp()
            ts.FromDatetime(datetime.now(UTC) + timedelta(seconds=delay_s))
            task["schedule_time"] = ts
        try:
            await self._client.create_task(parent=parent, task=task)
            return True
        except AlreadyExists:
            log.info("duplicate task ignored", extra={"event": "task.duplicate", "queue": queue})
            return False
