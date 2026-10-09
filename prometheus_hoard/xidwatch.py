"""Watcher for GPU driver errors in the kernel log of each Spark.

Every ``xid_interval_s`` seconds (60 by default; ``xid_watch`` switches it off) and for each enabled Spark it reads the kernel messages
written since the last scan, ``journalctl -k --since <cursor> --no-pager -o short-iso`` over SSH, and keeps the lines that report

* an NVIDIA ``Xid`` (``NVRM: Xid (PCI:...): 79, ...``) with its code,
* a full-chip reset (``NV_ERR_GPU_IN_FULLCHIP_RESET`` and any ``FULLCHIP_RESET``),
* a GSP error, timeout or failure (``GSP`` together with ``error`` / ``timeout`` / ``fail``).

It only warns and keeps evidence: events (Spark, time, the raw line, the Xid code) go to ``<data>/xid_events.json`` (bounded), the page and
the ``xid_events`` tool show them, and a notification goes out through the family hub when one is configured. It never restarts, resets
or changes anything on a Spark.

The cursor is the Spark's own clock (``date`` is read in the same command) minus a few seconds of overlap; lines already seen are
recognised by Spark and text, so the overlap never duplicates an event. A Spark whose journal cannot be read (SSH down, no permission)
is reported as such and its cursor stays where it was."""

from __future__ import annotations

import logging
import re
import shlex
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import SparkError
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.ids import new_id

log = logging.getLogger("prometheus.xid")

KEEP = 500                 # events kept
OVERLAP_S = 5              # how far before "now" the next scan starts
FIRST_WINDOW = "2 minutes ago"   # --since of the first scan of a Spark
LINE_MAX = 600
SKIP_POWER = ("off", "sleeping", "shutting_down", "restarting", "waking")

XID = re.compile(r"NVRM: Xid \((?P<pci>[^)]*)\):\s*(?P<code>\d+)")
FULLCHIP = re.compile(r"NV_ERR_GPU_IN_FULLCHIP_RESET|FULLCHIP_RESET")
GSP = re.compile(r"\bGSP\b")
GSP_BAD = re.compile(r"error|time[- ]?out|timed out|fail", re.IGNORECASE)
STAMP = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})(?:[.,]\d+)?(?P<tz>Z|[+-]\d{2}:?\d{2})?")

XID_NAMES = {
    13: "Graphics engine exception", 31: "GPU memory page fault (MMU)", 43: "GPU stopped processing", 45: "Channel killed by the driver (preemptive cleanup)",
    48: "Double-bit ECC error", 62: "Internal micro-controller halt", 63: "ECC page retirement / row remap event", 79: "GPU has fallen off the bus",
    94: "Contained ECC error", 95: "Uncontained ECC error", 119: "GSP RPC timeout", 120: "GSP error",
}


def classify(line: str) -> Optional[dict[str, Any]]:
    """``{kind, xid, pci}`` when the kernel line reports one of the watched problems, else None. Kinds: ``xid``, ``fullchip_reset``, ``gsp``."""
    m = XID.search(line)
    if m:
        code = int(m.group("code"))
        return {"kind": "xid", "xid": code, "pci": m.group("pci"), "meaning": XID_NAMES.get(code, "")}
    if FULLCHIP.search(line):
        return {"kind": "fullchip_reset", "xid": None, "pci": "", "meaning": "Full-chip reset of the GPU"}
    if GSP.search(line) and GSP_BAD.search(line):
        return {"kind": "gsp", "xid": None, "pci": "", "meaning": "GSP firmware error, timeout or failure"}
    return None


def parse_stamp(line: str) -> tuple[Optional[float], str]:
    """(epoch, local ``YYYY-MM-DD HH:MM:SS``) of a ``short-iso`` line; (None, "") when it has no stamp."""
    m = STAMP.match(line)
    if not m:
        return None, ""
    local = f"{m.group('date')} {m.group('time')}"
    tz = m.group("tz")
    try:
        stamp = datetime.strptime(local, "%Y-%m-%d %H:%M:%S")
        if tz == "Z":
            stamp = stamp.replace(tzinfo=timezone.utc)
        elif tz:
            digits = tz[1:].replace(":", "")
            offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
            stamp = stamp.replace(tzinfo=timezone(offset if tz[0] == "+" else -offset))
        return stamp.timestamp(), local        # a stamp without an offset is read as this PC's local time
    except ValueError:
        return None, ""


def journal_command(cursor: str) -> str:
    since = shlex.quote(cursor) if cursor else shlex.quote(FIRST_WINDOW)
    return (f"journalctl -k --since {since} --no-pager -o short-iso; rc=$?; echo \"@@RC=$rc\"; "
            "echo \"@@NOW=$(date '+%Y-%m-%d %H:%M:%S')\"")


class XidWatch:
    def __init__(self, nodes: Callable[[], list[Any]], path: Path, options: Callable[[], dict[str, Any]], *,
                 notifier: Optional[Callable[..., Any]] = None, clock: Callable[[], float] = time.time, timeout_s: float = 25.0):
        self._nodes = nodes
        self.path = Path(path)
        self.options = options
        self.notifier = notifier
        self.clock = clock
        self.timeout_s = timeout_s
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_round = -1e18
        raw = read_json(self.path, default={}) or {}
        raw = raw if isinstance(raw, dict) else {}
        self.events: list[dict[str, Any]] = [e for e in raw.get("events", []) if isinstance(e, dict) and e.get("line")][-KEEP:]
        self.cursors: dict[str, str] = {k: v for k, v in (raw.get("cursors") or {}).items() if isinstance(v, str)}
        self.status: dict[str, dict[str, Any]] = {}
        self._keys = {(e.get("node"), e["line"]) for e in self.events}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="prometheus-xid", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                opts = self.options()
                if not opts.get("enabled", True):
                    continue
                if self.clock() - self._last_round >= float(opts.get("interval_s", 60.0)):
                    self.scan_once()
            except Exception:  # noqa: BLE001
                log.exception("xid watcher")

    # ------------------------------------------------------------------ scanning
    def scan_once(self) -> list[dict[str, Any]]:
        """One round over the enabled Sparks; returns the events found for the first time."""
        found: list[dict[str, Any]] = []
        with self._scan_lock:
            self._last_round = self.clock()
            for node in self._nodes():
                if getattr(node, "power_state", "") in SKIP_POWER:
                    continue
                found += self._scan_node(node)
        return found

    def _scan_node(self, node: Any) -> list[dict[str, Any]]:
        nid = node.id
        name = node.conf.get("name", nid) if getattr(node, "conf", None) else nid
        cursor = self.cursors.get(nid, "")
        try:
            res = node.transport.run(journal_command(cursor), timeout=self.timeout_s)
        except SparkError as exc:
            self._set_status(nid, False, f"{exc.code}: {exc}"[:200])
            return []
        except Exception as exc:  # noqa: BLE001 - one Spark must not stop the round
            self._set_status(nid, False, f"{exc.__class__.__name__}: {exc}"[:200])
            return []
        lines = res.out.splitlines()
        rc, now_local = None, ""
        body: list[str] = []
        for ln in lines:
            if ln.startswith("@@RC="):
                rc = ln[5:].strip()
            elif ln.startswith("@@NOW="):
                now_local = ln[6:].strip()
            else:
                body.append(ln)
        err = (res.err or "").strip()
        if (rc not in (None, "0")) or "insufficient permissions" in err.lower() or "insufficient permissions" in res.out.lower():
            self._set_status(nid, False, (err or "journalctl failed")[-200:])
            return []
        new: list[dict[str, Any]] = []
        newest = ""
        for ln in body:
            line = ln.strip()[:LINE_MAX]
            if not line or line.startswith("-- "):
                continue
            hit = classify(line)
            epoch, local = parse_stamp(line)
            if local and local > newest:
                newest = local
            if not hit:
                continue
            key = (nid, line)
            with self._lock:
                if key in self._keys:
                    continue
                self._keys.add(key)
                event = {"id": new_id("xid"), "node": nid, "t": epoch if epoch is not None else self.clock(), "stamp": local, "seen": self.clock(),
                         "line": line, **hit, "notified": None}
                self.events.append(event)
            new.append(event)
        # next scan starts a few seconds before the Spark's clock now (or at the newest line when the clock was not read)
        nxt = ""
        if now_local:
            try:
                nxt = (datetime.strptime(now_local, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=OVERLAP_S)).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                nxt = ""
        elif newest:
            nxt = newest
        with self._lock:
            if nxt:
                self.cursors[nid] = nxt
            del self.events[:-KEEP]
            self._keys = {(e.get("node"), e["line"]) for e in self.events}
            self._save()
        self._set_status(nid, True, "")
        if new:
            log.warning("%s: %d GPU error line(s) in the kernel log, first: %s", nid, len(new), new[0]["line"][:200])
            self._notify(nid, name, new)
        return new

    def _set_status(self, node_id: str, ok: bool, error: str) -> None:
        with self._lock:
            self.status[node_id] = {"ok": ok, "error": error, "scanned": self.clock(), "cursor": self.cursors.get(node_id, "")}

    def _notify(self, node_id: str, name: str, events: list[dict[str, Any]]) -> None:
        if not self.notifier or not self.options().get("notify", True):
            return
        first = events[0]
        what = f"Xid {first['xid']}" if first["kind"] == "xid" else ("full-chip reset" if first["kind"] == "fullchip_reset" else "GSP error")
        title = f"{name}: GPU error in the kernel log ({what})"
        body = "\n".join(e["line"][:220] for e in events[:3]) + (f"\n+{len(events) - 3} more" if len(events) > 3 else "")
        try:
            res = self.notifier(title, body, priority="high", group="gpu-xid", dedupe_key=f"xid:{node_id}:{first['id']}")
            ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
        except Exception as exc:  # noqa: BLE001 - a notification must never stop the watcher
            log.warning("notification failed: %s", exc)
            ok = False
        with self._lock:
            for e in events:
                e["notified"] = ok
            self._save()

    def _save(self) -> None:
        write_json_atomic(self.path, {"events": self.events[-KEEP:], "cursors": self.cursors})

    # ------------------------------------------------------------------ read
    def snapshot(self, *, hours: float = 24.0, node: str = "", limit: int = 100) -> dict[str, Any]:
        opts = self.options()
        since = self.clock() - hours * 3600.0
        with self._lock:
            rows = [dict(e) for e in self.events if e["t"] >= since and (not node or e["node"] == node)]
            status = {k: dict(v) for k, v in self.status.items()}
        rows.sort(key=lambda e: e["t"], reverse=True)
        counts: dict[str, int] = {}
        for e in rows:
            counts[e["node"]] = counts.get(e["node"], 0) + 1
        return {"enabled": bool(opts.get("enabled", True)), "interval_s": float(opts.get("interval_s", 60.0)), "notify": bool(opts.get("notify", True)),
                "hours": hours, "total": len(rows), "counts": counts, "events": rows[:limit], "nodes": status, "now": self.clock()}

    def summary(self) -> dict[str, Any]:
        snap = self.snapshot(limit=1)
        return {"enabled": snap["enabled"], "count_24h": snap["total"], "by_node": snap["counts"], "last": snap["events"][0] if snap["events"] else None,
                "unreadable": [k for k, v in snap["nodes"].items() if not v["ok"]]}
