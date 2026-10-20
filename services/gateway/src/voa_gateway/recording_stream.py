"""Stream a recording WAV from Cloud Storage with single-range support."""

from __future__ import annotations

import asyncio

from fastapi import Response
from fastapi.responses import StreamingResponse

CHUNK = 256 * 1024


async def stream_recording(project_id: str, uri: str, range_header: str = "") -> Response:
    from google.cloud import storage

    bucket, _, name = uri[5:].partition("/")
    blob = await asyncio.to_thread(
        lambda: storage.Client(project=project_id).bucket(bucket).get_blob(name)
    )
    if blob is None:
        return Response(status_code=404)
    size = blob.size
    start, end, status = 0, size - 1, 200
    if range_header.startswith("bytes="):
        a, _, b = range_header[6:].partition("-")
        try:
            start = int(a) if a else max(0, size - int(b or 0))
            end = min(int(b), size - 1) if a and b else end
        except ValueError:
            return Response(status_code=416)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206

    def chunks():
        pos = start
        while pos <= end:
            stop = min(pos + CHUNK - 1, end)
            # GCS ``end`` is inclusive (the last byte); see the API's recording route.
            yield blob.download_as_bytes(start=pos, end=stop)
            pos = stop + 1

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type="audio/wav", headers=headers)
