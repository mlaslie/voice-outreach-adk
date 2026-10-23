"""Where the console keeps export profiles (docs/EHR-EXPORT-PLAN.md §4.4).

Firestore:

* ``settings/ehr_export``: the pointer doc ``{active_version, draft_version,
  next_version}``;
* ``settings/ehr_export/versions/{n}`` (a subcollection of that doc):
  ``{version, status: draft|active|retired, profile (JSON), problems,
  saved_by, saved_at, activated_by, activated_at, retired_at, note}``.

The API writes them (``voa_api.routes_ehr_export``); the export service reads
only the active version (:func:`active_profile`), at the start of each run.
"""

from __future__ import annotations

from typing import Any

from voa_core.store import DocStore

from .profile import Profile, ProfileError, parse_profile

SETTINGS, POINTER = "settings", "ehr_export"
#: A subcollection path: DocStore collections may be nested ("a/b/c").
VERSIONS = f"{SETTINGS}/{POINTER}/versions"
DRAFT, ACTIVE, RETIRED = "draft", "active", "retired"


async def pointer(store: DocStore) -> dict[str, Any]:
    return await store.get(SETTINGS, POINTER) or {}


async def get_version(store: DocStore, n: int | None) -> dict[str, Any] | None:
    if n is None:
        return None
    return await store.get(VERSIONS, str(int(n)))


async def active_profile(store: DocStore) -> Profile | None:
    """The active profile, or None when there is none (or it no longer parses,
    which activation prevents)."""
    doc = await get_version(store, (await pointer(store)).get("active_version"))
    if not doc:
        return None
    try:
        return parse_profile(doc.get("profile"))
    except ProfileError:
        return None
