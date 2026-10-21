"""Step 5a: Vertex AI check-grounding (docs/KNOWLEDGE-PLAN.md section 3).

Discovery Engine ``groundingConfigs/default_grounding_config:check`` with the
draft's cited sources as facts. It returns ``supportScore`` (the share of the
answer's claims the facts support) and each claim with its citations. The agent
defers when the score is below ``min_support`` or any claim that needs checking
has no citation.

Auth: Application Default Credentials (``google.auth.default``) with the
cloud-platform scope. IAM: ``discoveryengine.groundingConfigs.check``, which is
in ``roles/discoveryengine.viewer`` (granted to the API and gateway accounts in
infra/envs/demo).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

ENDPOINT = (
    "https://discoveryengine.googleapis.com/v1/projects/{project}/locations/global/"
    "groundingConfigs/default_grounding_config:check"
)
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
#: Whether a fact must be cited for a claim (the API's own default).
CITATION_THRESHOLD = 0.6


@dataclass(frozen=True)
class Fact:
    text: str
    source_id: str  # the material's ref, e.g. "gl:<doc_id>"


@dataclass(frozen=True)
class GroundingResult:
    support_score: float
    claims: int = 0
    uncited_claims: int = 0


#: ``await grounder(answer, facts) -> GroundingResult``. Raises on any error.
GroundingChecker = Callable[[str, Sequence[Fact]], Awaitable[GroundingResult]]


def build_body(answer: str, facts: Sequence[Fact], threshold: float) -> dict[str, Any]:
    return {
        "answerCandidate": answer,
        "facts": [{"factText": f.text, "attributes": {"source_id": f.source_id}} for f in facts],
        "groundingSpec": {"citationThreshold": threshold},
    }


def parse_response(data: Mapping[str, Any]) -> GroundingResult:
    """``supportScore`` and the claims. A missing score is an error (the API
    always sets it on success)."""
    score = data.get("supportScore")
    if not isinstance(score, int | float) or isinstance(score, bool):
        raise ValueError("check-grounding returned no supportScore")
    claims = [c for c in data.get("claims") or [] if isinstance(c, Mapping)]
    needing = [c for c in claims if c.get("groundingCheckRequired", True) is not False]
    uncited = [c for c in needing if not c.get("citationIndices")]
    return GroundingResult(float(score), len(needing), len(uncited))


class VertexGroundingChecker:
    """The REST call. ``http`` (an ``httpx.AsyncClient``) and ``credentials`` are
    injectable; by default they are created on first use."""

    def __init__(
        self,
        project: str,
        *,
        http=None,
        credentials=None,
        citation_threshold: float = CITATION_THRESHOLD,
        timeout_s: float = 3.0,
    ):
        self._project = project
        self._url = ENDPOINT.format(project=project)
        self._http = http
        self._creds = credentials
        self._threshold = citation_threshold
        self._timeout = timeout_s
        self._lock = asyncio.Lock()

    async def _token(self) -> str:
        async with self._lock:
            if self._creds is None:
                import google.auth

                self._creds, _ = await asyncio.to_thread(google.auth.default, scopes=SCOPES)
            if not getattr(self._creds, "valid", False):
                from google.auth.transport.requests import Request

                await asyncio.to_thread(self._creds.refresh, Request())
            return self._creds.token

    def _client(self):
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def warm(self) -> None:
        """Fetch a token ahead of the first question (best effort)."""
        try:
            await self._token()
        except Exception:  # noqa: BLE001
            log.warning("grounding warm-up failed", extra={"event": "knowledge.ground_warm"})

    async def __call__(self, answer: str, facts: Sequence[Fact]) -> GroundingResult:
        token = await self._token()
        resp = await self._client().post(
            self._url,
            json=build_body(answer, facts, self._threshold),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Goog-User-Project": self._project,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"check-grounding HTTP {resp.status_code}")
        return parse_response(resp.json())
