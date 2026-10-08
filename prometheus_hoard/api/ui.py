"""Routes for the bundled web UI and for programs that only read (Faustus, the hub).

``GET /api/overview`` is what the panel polls; ``GET /api/serving`` the live inference figures of the servers; ``GET /api/endpoints`` is the list of OpenAI-compatible servers the Sparks offer now (read
by Faustus to use the Sparks as its backend); file bytes travel on their own routes (download, raw preview, upload); everything else goes
through ``POST /api/ui/call`` with ``{name, arguments}``, which runs the same tool handlers the assistant uses, uncapped."""

from __future__ import annotations

import mimetypes
import time
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_tools import TOOLS_BY_NAME, tool_catalog
from ..errors import SparkError
from ..hoard_link import family
from .deps import services, tool

router = APIRouter(prefix="/api")


class CallBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    arguments: dict[str, Any] | None = None


@router.get("/overview")
def overview(request: Request, detail: bool = False):
    return services(request).overview(detail=detail)


@router.get("/endpoints")
def endpoints(request: Request):
    """Running inference servers, default first: ``{endpoints: [{recipe, title, base_url, models, max_model_len, nodes, default}]}``."""
    s = services(request)
    return {"endpoints": s.recipes.endpoints(), "nodes": [{"id": n.id, "name": n.conf["name"], "online": n.online} for n in s.cluster.nodes.values()]}


@router.get("/serving")
def serving(request: Request, recipe: str = "", series: bool = True):
    """Live figures of the running inference servers (tokens per second, queue, KV cache, latency) and the totals served:
    ``{endpoints: [...], totals: {...}, poll_s, now}``."""
    return services(request).serving.snapshot(recipe or None, series=series)


@router.get("/nodes/{node}/history")
def history(request: Request, node: str, minutes: int = 10):
    return services(request).history(node, max(1, min(minutes, 240)))


@router.get("/ui/tools")
def ui_tools():
    return {"tools": [t["name"] for t in tool_catalog()]}


@router.post("/ui/call")
def ui_call(request: Request, body: CallBody):
    if body.name not in TOOLS_BY_NAME:
        raise SparkError("not_found", f"Unknown tool: {body.name}")
    t0 = time.monotonic()
    ok, error = False, ""
    try:
        result = tool(request, body.name, body.arguments)
        ok = True
        return result
    except Exception as exc:  # noqa: BLE001 - re-raised: the app's handlers shape the response
        error = str(exc)[:200]
        raise
    finally:
        family.record_call(body.name, ok, int((time.monotonic() - t0) * 1000), caller="ui", error=error)


def _stream(request: Request, node: str, path: str, *, inline: bool):
    name, size, gen = services(request).files.open_stream(node, path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if inline and not (ctype.startswith(("image/", "video/", "audio/", "text/")) or ctype in ("application/json", "application/pdf")):
        ctype = "application/octet-stream"
    if ctype == "image/svg+xml":
        ctype = "text/plain"   # never render an SVG from a remote disk as a document of this origin
    disposition = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}", "Content-Length": str(size),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    return StreamingResponse(gen, media_type=ctype, headers=headers)


@router.get("/files/download")
def download(request: Request, node: str, path: str):
    return _stream(request, node, path, inline=False)


@router.get("/files/raw")
def raw(request: Request, node: str, path: str):
    return _stream(request, node, path, inline=True)


@router.post("/files/upload")
def upload(request: Request, node: str = Form(...), folder: str = Form(...), files: list[UploadFile] = File(...), overwrite: bool = Form(False)):
    s = services(request)
    out = []
    for f in files:
        out.append(s.files.upload(node, folder, f.filename or "file", f.file, overwrite=overwrite))
    return {"uploaded": out}


@router.get("/events")
def events(request: Request, after: float = 0.0):
    return {"events": services(request).recent_events(after), "now": time.time()}
