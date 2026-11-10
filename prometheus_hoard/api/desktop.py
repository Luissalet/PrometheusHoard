"""The page of the remote desktop: the connection data and the WebSocket that carries the VNC stream.

``GET /api/desktop/{node}/session`` gives what the page needs to connect (the VNC password included, so it is not an agent tool).
``GET /api/desktop/{node}/clipboard`` reads the session's clipboard (an image comes back as base64 PNG) and
``POST /api/desktop/{node}/clipboard/image`` (body: the PNG bytes) puts an image on it; text normally travels inside the VNC stream.
``WS /api/desktop/{node}/ws`` bridges the browser to the VNC port on the Spark's loopback through an SSH ``direct-tcpip`` channel on the node's
existing connection. The request guard (``hoard_link.guard``) runs in front of it: the ``Host`` must be local or allowed and a browser's
``Origin`` must be this app's own page (any other origin is closed before the upgrade is accepted)."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..errors import SparkError
from .deps import services

router = APIRouter(prefix="/api/desktop")


@router.get("/{node}/session")
def session(request: Request, node: str):
    return services(request).desktop.session(node)


@router.get("/{node}/clipboard")
def clipboard(request: Request, node: str):
    return services(request).desktop.clipboard_get(node, include_image=True)


@router.post("/{node}/clipboard/image")
async def clipboard_image(request: Request, node: str):
    data = await request.body()
    return await asyncio.to_thread(services(request).desktop.clipboard_set_image, node, data)


def _pump_out(chan: Any, loop: asyncio.AbstractEventLoop, ws: WebSocket, stop: asyncio.Event) -> None:
    """Thread: VNC server -> browser."""
    chan.settimeout(1.0)
    try:
        while not stop.is_set():
            try:
                data = chan.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            asyncio.run_coroutine_threadsafe(ws.send_bytes(data), loop).result(timeout=30)
    except Exception:  # noqa: BLE001 - any failure ends the bridge
        pass
    finally:
        loop.call_soon_threadsafe(stop.set)


@router.websocket("/{node}/ws")
async def bridge(ws: WebSocket, node: str):
    svc = ws.app.state.services
    offered = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    await ws.accept(subprotocol="binary" if "binary" in offered else None)
    try:
        chan = await asyncio.to_thread(svc.desktop.open_bridge, node)
    except SparkError as exc:     # the page shows the reason carried by the close frame
        await ws.close(code=4404 if exc.code in ("unavailable", "not_found") else 4503, reason=exc.message[:120])
        return
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    reader = loop.run_in_executor(None, _pump_out, chan, loop, ws, stop)
    try:
        while not stop.is_set():
            receive = asyncio.ensure_future(ws.receive())
            waiter = asyncio.ensure_future(stop.wait())
            done, _ = await asyncio.wait({receive, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if receive not in done:
                receive.cancel()
                break
            waiter.cancel()
            msg = receive.result()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is None and msg.get("text") is not None:
                data = msg["text"].encode("utf-8")
            if data:
                await asyncio.to_thread(chan.sendall, data)
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    finally:
        stop.set()
        try:
            chan.close()
        except Exception:  # noqa: BLE001
            pass
        svc.desktop.close_bridge(node)
        await asyncio.gather(reader, return_exceptions=True)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 - already closed
            pass
