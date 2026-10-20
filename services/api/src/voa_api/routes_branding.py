"""/api/branding*: the staff consoles' runtime branding (docs/ADMIN-PLAN.md section 4).

One Firestore document, ``call_settings/branding``::

    {product_title, staff_org_name, colors: {token: "#rrggbb"},
     assets: {logo | logo_wide | favicon: {version, content_type, bytes}},
     updated_by, updated_at}

Only what an admin set is stored; anything absent is the built-in look (the UI's
``theme.css`` and its build-time ``NEXT_PUBLIC_*`` values). Images live in the
branding bucket at ``branding/<kind>``. Deleting the document (``DELETE``) is
"Reset to defaults".

This is staff-facing only. The organisation name patients hear and read is
``call_settings/organization`` (Admin → Display), which is part of the SMS
consent wording and is never changed from here.

SVG is refused: it can carry scripts. Uploads are checked by their magic bytes,
not the declared type.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from voa_core.store import now_iso

from .auth import acting_user
from .context import SETTINGS, Api
from .routes_calls import api_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(api_of)])

DOC_ID = "branding"

#: The colour tokens an admin may override, with today's values (ui/src/app/theme.css).
#: The UI maps each to its CSS variables (ui/src/lib/branding.ts).
DEFAULT_COLORS: dict[str, str] = {
    "brand": "#22873b",  # buttons, links, focus ring (--color-brand, --primary)
    "brand-deep": "#1d4d52",  # sidebar, dark buttons (--color-brand-deep)
    "accent": "#2ea84a",  # glows and highlights (--color-brand-bright)
    "emergency": "#e4002b",  # emergency / alert (--color-brand-red, --destructive)
    "urgent": "#e5554f",  # urgent (--color-brand-coral)
    "routine": "#ffcd00",  # routine, call-to-action yellow (--color-brand-yellow)
    "info": "#00749e",  # informational (--color-brand-blue)
    "background": "#f4f6f5",  # console background
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TEXT_MAX = 60

AssetKind = Literal["logo", "logo_wide", "favicon"]
KINDS: tuple[str, ...] = ("logo", "logo_wide", "favicon")
MAX_BYTES = {"logo": 512 * 1024, "logo_wide": 512 * 1024, "favicon": 128 * 1024}
# Declared type -> the type the magic bytes must show.
DECLARED = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/x-icon": "image/x-icon",
    "image/vnd.microsoft.icon": "image/x-icon",
}


# --------------------------------------------------------------------------- validation


def clean_text(value: str | None, what: str) -> str | None:
    """A title or name: at most 60 printable characters, no angle brackets.
    Empty (or None) means "back to the default"."""
    if value is None:
        return None
    if not value.isprintable() or "<" in value or ">" in value:
        raise ValueError(f"{what} may not contain control characters or < >")
    v = " ".join(value.split())
    if not v:
        return None
    if len(v) > TEXT_MAX:
        raise ValueError(f"{what} must be at most {TEXT_MAX} characters")
    return v


def clean_color(token: str, value: str | None) -> str | None:
    if token not in DEFAULT_COLORS:
        raise ValueError(f"unknown colour {token!r}; one of {', '.join(DEFAULT_COLORS)}")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not HEX_RE.match(value):
        raise ValueError(f"colour {token!r} must be #RRGGBB")
    return value.lower()


def sniff_image(data: bytes) -> str | None:
    """The image type from its first bytes: PNG, JPEG, WebP or ICO; else None (SVG too)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 6 and data[:4] == b"\x00\x00\x01\x00" and data[4:6] != b"\x00\x00":
        return "image/x-icon"
    return None


# --------------------------------------------------------------------------- image storage


class Blobs(Protocol):
    async def put(self, name: str, data: bytes, content_type: str) -> None: ...
    async def get(self, name: str) -> bytes | None: ...
    async def delete(self, name: str) -> None: ...


class MemoryBlobs:
    """Images in process memory: tests and local runs without a bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, name, data, content_type):
        self.objects[name] = (data, content_type)

    async def get(self, name):
        hit = self.objects.get(name)
        return hit[0] if hit else None

    async def delete(self, name):
        self.objects.pop(name, None)


class GcsBlobs:
    def __init__(self, project: str, bucket: str) -> None:
        self.project, self.bucket = project, bucket
        self._bucket = None

    def _b(self):
        if self._bucket is None:
            from google.cloud import storage

            self._bucket = storage.Client(project=self.project).bucket(self.bucket)
        return self._bucket

    async def put(self, name, data, content_type):
        def go():
            self._b().blob(name).upload_from_string(data, content_type=content_type)

        await asyncio.to_thread(go)

    async def get(self, name):
        def go():
            blob = self._b().get_blob(name)
            return None if blob is None else blob.download_as_bytes()

        return await asyncio.to_thread(go)

    async def delete(self, name):
        from google.api_core.exceptions import NotFound

        def go():
            try:
                self._b().blob(name).delete()
            except NotFound:
                pass

        await asyncio.to_thread(go)


def blobs(request: Request) -> Blobs:
    """The branding bucket; in dev/test without one, process memory. Tests may set
    ``app.state.branding_blobs`` to a fake."""
    state = request.app.state
    found = getattr(state, "branding_blobs", None)
    if found is not None:
        return found
    api: Api = state.api
    bucket = api.cfg.services.branding_bucket
    if bucket:
        state.branding_blobs = GcsBlobs(api.cfg.gcp.project_id, bucket)
    elif api.cfg.env in ("dev", "test"):
        state.branding_blobs = MemoryBlobs()
    else:
        raise HTTPException(503, "Image storage for branding is not configured.")
    return state.branding_blobs


def object_name(kind: str) -> str:
    return f"branding/{kind}"


# --------------------------------------------------------------------------- reads


def public(doc: dict | None) -> dict[str, Any]:
    """What GET returns: the overrides, today's colour defaults, per-field
    ``is_default`` flags and asset versions (never image bytes)."""
    doc = doc or {}
    colors = {k: v for k, v in (doc.get("colors") or {}).items() if k in DEFAULT_COLORS}
    raw_assets = doc.get("assets") or {}
    assets = {
        k: (
            {
                "version": int(raw_assets[k].get("version", 1)),
                "content_type": raw_assets[k].get("content_type"),
                "bytes": raw_assets[k].get("bytes"),
            }
            if isinstance(raw_assets.get(k), dict)
            else None
        )
        for k in KINDS
    }
    flags = {
        "product_title": not doc.get("product_title"),
        "staff_org_name": not doc.get("staff_org_name"),
        "colors": {k: k not in colors for k in DEFAULT_COLORS},
        **{k: assets[k] is None for k in KINDS},
    }
    everything = (
        flags["product_title"]
        and flags["staff_org_name"]
        and not colors
        and all(a is None for a in assets.values())
    )
    return {
        "product_title": doc.get("product_title") or None,
        "staff_org_name": doc.get("staff_org_name") or None,
        "colors": colors,
        "default_colors": dict(DEFAULT_COLORS),
        "assets": assets,
        "is_default": {**flags, "all": everything},
        "updated_by": doc.get("updated_by"),
        "updated_at": doc.get("updated_at"),
    }


@router.get("/branding")
async def get_branding(request: Request):
    api: Api = request.app.state.api
    return public(await api.store.get(SETTINGS, DOC_ID))


# --------------------------------------------------------------------------- writes


class BrandingPatch(BaseModel):
    """Partial update: a field left out is unchanged; "" or null resets it."""

    model_config = ConfigDict(extra="forbid")
    product_title: str | None = Field(default=None, max_length=400)
    staff_org_name: str | None = Field(default=None, max_length=400)
    colors: dict[str, str | None] | None = None


def _stamp(doc: dict, request: Request, api: Api) -> dict:
    doc["updated_by"] = acting_user(request)
    doc["updated_at"] = now_iso(api.now())
    return doc


@router.put("/branding")
async def put_branding(body: BrandingPatch, request: Request):
    api: Api = request.app.state.api
    sent = body.model_fields_set
    try:
        text = {
            k: clean_text(getattr(body, k), k.replace("_", " "))
            for k in ("product_title", "staff_org_name")
            if k in sent
        }
        colors = {k: clean_color(k, v) for k, v in (body.colors or {}).items()}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    def apply(cur: dict | None) -> dict:
        doc = dict(cur or {})
        for k, v in text.items():
            if v is None:
                doc.pop(k, None)
            else:
                doc[k] = v
        merged = dict(doc.get("colors") or {})
        for k, v in colors.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        doc["colors"] = merged
        return _stamp(doc, request, api)

    doc = await api.store.transact(SETTINGS, DOC_ID, apply)
    log.info(
        "branding changed",
        extra={"event": "branding.updated", "fields": sorted(sent), "by": acting_user(request)},
    )
    return public(doc)


@router.delete("/branding")
async def reset_branding(request: Request):
    """Reset to defaults: delete the document and every uploaded image."""
    api: Api = request.app.state.api
    store = blobs(request)
    for kind in KINDS:
        try:
            await store.delete(object_name(kind))
        except Exception:
            log.exception("branding image delete failed", extra={"event": "branding.delete_failed"})
            raise HTTPException(502, "Could not delete the uploaded images.") from None
    await api.store.delete(SETTINGS, DOC_ID)
    log.info("branding reset", extra={"event": "branding.reset", "by": acting_user(request)})
    return public(None)


class AssetUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_type: str = Field(max_length=100)
    # 512 KB of image is ~700 KB of base64.
    data_base64: str = Field(max_length=800_000)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(404, "Unknown image; one of logo, logo_wide, favicon.")
    return kind


@router.put("/branding/assets/{kind}")
async def put_asset(kind: str, body: AssetUpload, request: Request):
    api: Api = request.app.state.api
    _kind(kind)
    declared = body.content_type.split(";")[0].strip().lower()
    if "svg" in declared:
        raise HTTPException(
            415, "SVG is not accepted (it can carry scripts). Use PNG, JPEG, WebP or ICO."
        )
    if declared not in DECLARED:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or ICO image.")
    payload = body.data_base64
    if payload.startswith("data:"):
        payload = payload.partition(",")[2]
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "data_base64 is not valid base64.") from None
    if not data:
        raise HTTPException(422, "The image is empty.")
    limit = MAX_BYTES[kind]
    if len(data) > limit:
        raise HTTPException(413, f"The image is larger than {limit // 1024} KB.")
    actual = sniff_image(data)
    if actual is None:
        raise HTTPException(415, "Not a PNG, JPEG, WebP or ICO image (SVG is not accepted).")
    if actual != DECLARED[declared]:
        raise HTTPException(415, f"The file is {actual}, not {declared}.")

    await blobs(request).put(object_name(kind), data, actual)
    floor = int(api.now().timestamp())

    def apply(cur: dict | None) -> dict:
        doc = dict(cur or {})
        assets = dict(doc.get("assets") or {})
        prev = int((assets.get(kind) or {}).get("version", 0))
        # Monotonic across resets, so a cached ?v= never shows an old image.
        assets[kind] = {"version": max(prev + 1, floor), "content_type": actual, "bytes": len(data)}
        doc["assets"] = assets
        return _stamp(doc, request, api)

    doc = await api.store.transact(SETTINGS, DOC_ID, apply)
    log.info(
        "branding image uploaded",
        extra={
            "event": "branding.asset",
            "kind": kind,
            "bytes": len(data),
            "by": acting_user(request),
        },
    )
    return public(doc)


@router.get("/branding/assets/{kind}")
async def get_asset(kind: str, request: Request):
    api: Api = request.app.state.api
    _kind(kind)
    doc = await api.store.get(SETTINGS, DOC_ID) or {}
    meta = (doc.get("assets") or {}).get(kind)
    if not isinstance(meta, dict):
        raise HTTPException(404, "No image set.")
    data = await blobs(request).get(object_name(kind))
    if data is None:
        raise HTTPException(404, "Image missing.")
    ctype = meta.get("content_type") or sniff_image(data) or "application/octet-stream"
    return Response(
        content=data,
        media_type=ctype,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
