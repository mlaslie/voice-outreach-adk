"""The company name patients hear and read (voice script, voicemail, texts).

Set in the admin console (``call_settings/organization``); falls back to
``site.org_name`` from config. Read through a short cache so a call or a text never
waits on Firestore twice. The name is part of the SMS consent wording and the A2P
10DLC brand registration: the admin console says so when it is changed.
"""

from __future__ import annotations

import re
import time

COLLECTION = "call_settings"
DOC_ID = "organization"
CACHE_S = 60.0
# Spoken and texted (GSM-7): letters, digits, spaces and common name punctuation;
# never braces (scripts are format strings).
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &'.,()/+-]{1,59}$")

_cache: dict[int, tuple[float, str]] = {}


def validate(name: str) -> str:
    clean = " ".join((name or "").split())
    if not NAME_RE.match(clean):
        raise ValueError(
            "Use 2-60 letters, digits, spaces and & ' . , ( ) / + - (it is spoken and texted)."
        )
    return clean


async def org_name(store, default: str, *, fresh: bool = False) -> str:
    """The name to use now: the admin console's setting, else ``default``."""
    hit = _cache.get(id(store))
    if not fresh and hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        doc = await store.get(COLLECTION, DOC_ID) or {}
        name = doc.get("org_name") or default
    except Exception:
        name = hit[1] if hit else default  # never fail a call or a text over this
    _cache[id(store)] = (time.monotonic(), name)
    return name


def cached(store, default: str) -> str:
    """The last name read for this store (synchronous callers), else ``default``."""
    hit = _cache.get(id(store))
    return hit[1] if hit else default


def forget(store) -> None:
    _cache.pop(id(store), None)
