"""Who is using each model server: the HTTP access log of its container, aggregated per client address.

vLLM never records who asks, but uvicorn writes every request to the container log
(``INFO:     192.0.2.5:57320 - "POST /v1/chat/completions HTTP/1.1" 200 OK``). ``Clients`` follows the endpoints ``Serving`` follows and,
from a thread (never on a request), asks the head Spark for the lines written since the last read (``remote/accesslog.py``, a
``docker logs --timestamps --since <cursor>``), every ~20 s while the Serving page or tool was used in the last two minutes and every two
minutes otherwise. Per client address it keeps minute buckets of requests per API kind for 24 hours (kinds: OpenAI chat, completions,
responses, embeddings, Anthropic messages, other, and *polling*: ``/health``, ``/v1/models``, ``/metrics``) and the errors (non-2xx).
Loopback addresses are the server's own probes: only counted. A compact copy lives in ``<data>/clients.json``.

One address is one device: programs of the same device are not told apart by the log. For this PC the application asks the operating
system instead (``psutil.net_connections``): the established connections to each server and the programs that own them.
Limits: the log only holds what the container wrote since it started; a client behind a proxy shows the proxy's address."""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional
from urllib.parse import urlsplit

from .config import norm_ip
from .hoard_link.atomic import read_json, write_json_atomic

log = logging.getLogger("prometheus.clients")

KEEP_S = 86400.0        # how long a request stays in memory
HOUR_S = 3600.0
KINDS = ("chat", "responses", "messages", "completions", "embeddings", "other", "poll")
INFERENCE = KINDS[:5]
ERR = len(KINDS)        # the error counter follows the kinds in a bucket
THIS_PC = "Este PC"

# ====================================================================================== parsing
# optional RFC 3339 stamp (docker --timestamps), then the uvicorn access line; IPv6 comes bracketed or bare (``::1:58586``)
_LINE = re.compile(
    r'^\s*(?:(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?)\s+)?(?:INFO:\s+)?'
    r'(?P<ip>\[[^\]\s]+\]|[0-9A-Fa-f:.]+?):(?P<port>\d{1,5}) - "(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<code>\d{3})')
_STAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$")


class Access(NamedTuple):
    ns: Optional[int]       # nanoseconds since the epoch (None: the line carried no stamp)
    ip: str
    method: str
    path: str
    code: int


def parse_stamp(text: str) -> Optional[int]:
    """Nanoseconds since the epoch of an RFC 3339 stamp (any number of fraction digits; no zone means UTC)."""
    m = _STAMP.match((text or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        sec = int(datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None
    if zone and zone != "Z":
        sec -= (int(zone[1:3]) * 3600 + int(zone[4:6]) * 60) * (1 if zone[0] == "+" else -1)
    return sec * 10**9 + int(((frac or "") + "000000000")[:9])


def format_stamp(ns: int) -> str:
    """The RFC 3339 form ``docker logs --since`` takes."""
    sec, frac = divmod(int(ns), 10**9)
    return datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{frac:09d}Z"


def clean_ip(text: str) -> str:
    """A client address as the log wrote it, normalised ("[::1]" -> "::1"; IPv4-mapped IPv6 -> IPv4)."""
    ip = (text or "").strip().strip("[]").split("%", 1)[0].lower()
    if ip.startswith("::ffff:") and "." in ip:
        ip = ip[7:]
    return ip


def is_loopback(ip: str) -> bool:
    return ip == "::1" or ip.startswith("127.") or ip == "localhost"


def parse_line(line: str) -> Optional[Access]:
    """The request an access-log line records (None for any other line: engine messages, tracebacks, half lines)."""
    m = _LINE.match(line or "")
    if not m:
        return None
    ip = norm_ip(m["ip"])
    if not ip:
        return None
    ns = parse_stamp(m["ts"]) if m["ts"] else None
    if m["ts"] and ns is None:
        return None
    return Access(ns, ip, m["method"], m["path"], int(m["code"]))


_POLL_PATHS = ("/health", "/ping", "/metrics", "/version", "/load", "/is_sleeping", "/server_info", "/v1/models", "/models",
               # what other engines answer: programs that look for llama.cpp or Ollama probe these and get a 404 here
               "/props", "/v1/props", "/slots", "/api/version", "/api/tags", "/api/ps", "/v1/health")


def classify(method: str, path: str) -> str:
    """The API kind of a request: one of ``KINDS``."""
    p = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    if method in ("GET", "HEAD", "OPTIONS") and (p in _POLL_PATHS or p.startswith(("/v1/models/", "/models/"))):
        return "poll"
    if method == "POST" or p.startswith("/v1/responses/"):
        if p.endswith("/chat/completions"):
            return "chat"
        if p.endswith("/completions"):
            return "completions"
        if p.startswith("/v1/responses") or p == "/responses":
            return "responses"
        if p in ("/v1/messages", "/messages"):
            return "messages"
        if p.endswith("/embeddings"):
            return "embeddings"
    return "other"


# ====================================================================================== programs on this PC
# (text searched in command line, folder and program name, label); the first match wins
MARKERS = (("faustus", "Faustus"), ("odysseus", "Faustus"), ("codex", "Codex"), ("claude", "Claude Code"))


def _pretty_module(module: str) -> str:
    top = re.split(r"[.:]", module)[0]
    for suffix in ("_hoard", "-hoard"):
        if top.endswith(suffix) and len(top) > len(suffix):
            return top[:-len(suffix)].replace("_", " ").replace("-", " ").title() + "'s Hoard"
    return ""


def process_label(name: str, cmdline: list[str], cwd: str = "") -> str:
    """A name for a program from its executable, command line and folder: the Hoard apps by their module (``-m galton_hoard``), the
    assistants and Faustus by what their command line mentions, ``node``/``python`` by their script; the program name otherwise."""
    stem = re.sub(r"\.exe$", "", (name or "").strip(), flags=re.I) or "?"
    args = [str(a) for a in cmdline or []]
    if "-m" in args[:-1]:
        module = args[args.index("-m") + 1]
        pretty = _pretty_module(module)
        if pretty:
            return pretty
    hay = " ".join([stem, *args, cwd or ""]).lower()
    for marker, label in MARKERS:
        if marker in hay:
            return label
    script = next((a for a in args[1:] if re.search(r"\.(py|js|mjs|cjs|ts)$", a, re.I)), "")
    if script:
        return f"{stem} · {re.split(r'[\\\\/]', script)[-1]}"
    if "-m" in args[:-1]:
        return f"{stem} · -m {args[args.index('-m') + 1]}"
    return stem


def local_processes(targets: list[dict[str, Any]], psutil_mod: Any, resolve: Callable[[str], set[str]]) -> dict[str, Any]:
    """The established TCP connections of this PC to each target ``{key, host, port}``, grouped by program.

    Returns ``{error, by_key: {key: {processes: [{pid, exe, label, cmd, connections}], connections, local_ips}}}``. ``error`` is
    ``access_denied`` when the system refuses to list connections, ``unavailable`` without psutil; programs the system will not describe
    are listed without name."""
    wanted: dict[tuple[str, int], list[str]] = {}
    for t in targets:
        for ip in resolve(t["host"]):
            wanted.setdefault((clean_ip(ip), int(t["port"])), []).append(t["key"])
    by_key = {t["key"]: {"processes": {}, "connections": 0, "local_ips": set()} for t in targets}
    if psutil_mod is None:
        return {"error": "unavailable", "by_key": {k: _finish_local(v) for k, v in by_key.items()}}
    denied = getattr(psutil_mod, "AccessDenied", PermissionError)
    base_error = getattr(psutil_mod, "Error", Exception)
    try:
        conns = psutil_mod.net_connections(kind="tcp")
    except denied:
        return {"error": "access_denied", "by_key": {k: _finish_local(v) for k, v in by_key.items()}}
    except (base_error, OSError) as exc:
        return {"error": str(exc)[:120] or "unavailable", "by_key": {k: _finish_local(v) for k, v in by_key.items()}}
    cache: dict[Any, dict[str, Any]] = {}

    def describe(pid: Optional[int]) -> dict[str, Any]:
        if pid in cache:
            return cache[pid]
        info = {"pid": pid, "exe": "", "label": "", "cmd": ""}
        if pid is not None:
            name, cmd, cwd = "", [], ""
            proc = None
            try:
                proc = psutil_mod.Process(pid)
            except (base_error, OSError):
                pass
            if proc is not None:
                for attr in ("name", "cmdline", "cwd"):
                    try:
                        value = getattr(proc, attr)()
                    except (base_error, OSError):
                        continue
                    if attr == "name":
                        name = value or ""
                    elif attr == "cmdline":
                        cmd = list(value or [])
                    else:
                        cwd = value or ""
            if name or cmd:
                info.update(exe=name, label=process_label(name, cmd, cwd), cmd=" ".join(cmd)[:300])
        cache[pid] = info
        return info

    for c in conns:
        if getattr(c, "status", "") != "ESTABLISHED" or not getattr(c, "raddr", None):
            continue
        key_ = (clean_ip(c.raddr[0]), int(c.raddr[1]))
        for key in wanted.get(key_, ()):
            entry = by_key[key]
            entry["connections"] += 1
            if getattr(c, "laddr", None):
                entry["local_ips"].add(clean_ip(c.laddr[0]))
            pid = getattr(c, "pid", None)
            row = entry["processes"].get(pid)
            if row is None:
                row = entry["processes"][pid] = {**describe(pid), "connections": 0}
            row["connections"] += 1
    return {"error": None, "by_key": {k: _finish_local(v) for k, v in by_key.items()}}


def _finish_local(entry: dict[str, Any]) -> dict[str, Any]:
    rows = sorted(entry["processes"].values(), key=lambda r: (-r["connections"], r["label"] or "~", r["pid"] or 0))
    return {"processes": rows, "connections": entry["connections"], "local_ips": sorted(entry["local_ips"])}


def route_source(host: str) -> Optional[str]:
    """The address of this PC that the operating system would use to reach ``host`` (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except OSError:
        return None


def resolve_host(host: str) -> set[str]:
    try:
        return {clean_ip(i[4][0]) for i in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        return set()


# ====================================================================================== the collector
class _Ep:
    def __init__(self, info: dict[str, Any]):
        self.info = info
        self.ips: dict[str, dict[str, Any]] = {}        # client address -> {first, last, b: {minute: [counts per kind..., errors]}}
        self.loop: dict[int, int] = {}                  # minute -> requests from the loopback (the server's own probes)
        self.cursor: Optional[int] = None               # ns of the newest line read
        self.at_cursor: set[str] = set()                # lines already counted that carry exactly the cursor's stamp
        self.ok: Optional[bool] = None
        self.error = ""
        self.container = ""
        self.via = ""
        self.last_read = -1e18
        self.last_ok = 0.0
        self.backoff = 0.0
        self.skew = 0.0                                 # the Spark's clock minus ours
        self.prefer_sudo = False
        self.truncated = False
        self.inflight = False
        self.route_t = -1e18


class Clients:
    def __init__(self, endpoints: Callable[[], list[dict[str, Any]]], path: Path, *, runner: Callable[[str, dict[str, Any]], dict[str, Any]],
                 containers: Callable[[dict[str, Any]], list[str]] = lambda info: [], names: Callable[[], dict[str, str]] = dict,
                 spark_addrs: Callable[[], dict[str, str]] = dict, node_name: Callable[[str], str] = lambda n: n,
                 touched: Callable[[], float] = lambda: -1e18, psutil_mod: Any = ..., resolve: Callable[[str], set[str]] = resolve_host,
                 route: Callable[[str], Optional[str]] = route_source, clock: Callable[[], float] = time.time, fast_s: float = 20.0,
                 idle_s: float = 120.0, touch_s: float = 120.0, local_s: float = 2.0, refresh_s: float = 15.0, save_s: float = 60.0,
                 lookback_s: float = KEEP_S, max_lines: int = 20000):
        self._endpoints = endpoints
        self.path = Path(path)
        self.runner = runner
        self.containers = containers
        self.names = names
        self.spark_addrs = spark_addrs
        self.node_name = node_name
        self.touched = touched
        if psutil_mod is ...:
            try:
                import psutil as psutil_mod
            except ImportError:
                psutil_mod = None
        self.psutil = psutil_mod
        self.resolve = resolve
        self.route = route
        self.clock = clock
        self.fast_s, self.idle_s, self.touch_s, self.local_s, self.refresh_s, self.save_s = fast_s, idle_s, touch_s, local_s, refresh_s, save_s
        self.lookback_s, self.max_lines = lookback_s, max_lines
        self._lock = threading.RLock()
        self._eps: dict[str, _Ep] = {}
        self._listed = -1e18
        self._own_touch = -1e18
        self._local: dict[str, Any] = {"t": 0.0, "error": None, "by_key": {}}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="prometheus-clients")
        self._pc: dict[str, str] = {}                   # addresses of this PC as the servers see them -> how it was found
        raw = read_json(self.path, default={}) or {}
        self._store: dict[str, Any] = raw if isinstance(raw, dict) and isinstance(raw.get("endpoints"), dict) else {"endpoints": {}}
        for ip in self._store.get("this_pc") or []:
            if isinstance(ip, str):
                self._pc[ip] = "saved"
        self._dirty = False
        self._saved = -1e18

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="prometheus-clients", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.save(force=True)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def touch(self) -> None:
        self._own_touch = self.clock()

    def _last_touch(self) -> float:
        return max(self._own_touch, self.touched())

    @property
    def interval(self) -> float:
        return self.fast_s if self.clock() - self._last_touch() <= self.touch_s else self.idle_s

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                log.exception("clients collector")
            self._stop.wait(0.5)

    # ------------------------------------------------------------------ collecting
    def refresh_list(self) -> None:
        try:
            listed = self._endpoints()
        except Exception:  # noqa: BLE001 - keep the last list
            log.exception("endpoint list")
            return
        with self._lock:
            self._listed = self.clock()
            keys = set()
            for info in listed:
                key = info["recipe"]
                keys.add(key)
                if key in self._eps:
                    self._eps[key].info = info
                else:
                    self._eps[key] = self._restore(key, info)
            for key in [k for k in self._eps if k not in keys]:
                self._keep(self._eps.pop(key))

    def _restore(self, key: str, info: dict[str, Any]) -> _Ep:
        ep = _Ep(info)
        rec = self._store["endpoints"].get(key)
        if not isinstance(rec, dict):
            return ep
        cursor = rec.get("cursor")
        ep.cursor = int(cursor) if isinstance(cursor, (int, float)) else None
        for ip, r in (rec.get("ips") or {}).items():
            try:
                ep.ips[ip] = {"first": float(r["first"]), "last": float(r["last"]),
                              "b": {int(row[0]): [int(x) for x in row[1:1 + len(KINDS) + 1]] for row in r.get("b", [])}}
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        ep.loop = {int(m): int(n) for m, n in (rec.get("loop") or [])} if isinstance(rec.get("loop"), list) else {}
        return ep

    def _keep(self, ep: _Ep) -> None:
        """A server that stopped keeps its last summary on disk until it is older than the retention."""
        self._store["endpoints"][ep.info["recipe"]] = self._dump(ep)
        self._dirty = True

    def poll_once(self, *, wait: bool = False) -> None:
        """One round: refresh the endpoint list when stale, start the reads that are due (each in its own worker), take the snapshot of the
        local connections while someone looks, save when it is time. ``wait`` joins the reads (tests, no thread)."""
        now = self.clock()
        if now - self._listed >= self.refresh_s:
            self.refresh_list()
        interval = self.interval
        with self._lock:
            due = [ep for ep in self._eps.values() if not ep.inflight and now - ep.last_read >= max(interval, ep.backoff)]
            for ep in due:
                ep.inflight = True
        futures = [self._pool.submit(self._read, ep) for ep in due]
        if now - self._last_touch() <= self.touch_s and now - self._local["t"] >= self.local_s:
            self.local_tick()
        if wait:
            for f in futures:
                f.result()
        self.save()

    def _read(self, ep: _Ep) -> None:
        try:
            self._read_inner(ep)
        except Exception as exc:  # noqa: BLE001 - one broken endpoint must not stop the others
            log.exception("access log of %s", ep.info.get("recipe"))
            self._fail(ep, f"{exc.__class__.__name__}: {exc}"[:200])
        finally:
            ep.inflight = False

    def _read_inner(self, ep: _Ep) -> None:
        t0 = self.clock()
        ep.last_read = t0
        info = ep.info
        self._detect_route(ep, info)
        candidates = self.containers(info)
        if not candidates:
            self._fail(ep, "no_container", backoff=self.idle_s)
            return
        since = ep.cursor if ep.cursor is not None else int((t0 + ep.skew - self.lookback_s) * 1e9)
        args = {"containers": candidates, "since": format_stamp(since), "prefer_sudo": ep.prefer_sudo, "max_lines": self.max_lines}
        try:
            res = self.runner(info.get("head") or "", args)
        except Exception as exc:  # noqa: BLE001 - SparkError: unreachable, timeout...
            self._fail(ep, str(getattr(exc, "message", None) or exc)[:200], backoff=60.0)
            return
        if not isinstance(res, dict) or not res.get("ok"):
            err = str((res or {}).get("error") or "docker_failed")
            self._fail(ep, err, backoff=self.idle_s if err in ("no_container", "docker_denied") else 60.0)
            return
        remote_now = res.get("now")
        if isinstance(remote_now, (int, float)):
            ep.skew = float(remote_now) - self.clock()
        pc = clean_ip(str(res.get("ssh_client") or ""))
        if pc and not is_loopback(pc) and norm_ip(pc):
            self._add_pc(pc, "ssh")
        with self._lock:
            ep.container, ep.via, ep.prefer_sudo = str(res.get("container") or ""), str(res.get("via") or ""), res.get("via") == "sudo"
            ep.truncated = bool(res.get("truncated"))
        self.ingest(info["recipe"], res.get("lines") or [], now=self.clock())
        if isinstance(remote_now, (int, float)) and not (res.get("lines") or []):
            with self._lock:     # nothing new: everything up to a few seconds ago is read, so the next read starts there
                ep.cursor = max(ep.cursor or 0, int((float(remote_now) - 5.0) * 1e9))
                ep.at_cursor = set()
        with self._lock:
            ep.ok, ep.error, ep.last_ok, ep.backoff = True, "", self.clock(), 0.0

    def _fail(self, ep: _Ep, error: str, backoff: float = 0.0) -> None:
        with self._lock:
            ep.ok, ep.error, ep.backoff = False, error, backoff

    def _detect_route(self, ep: _Ep, info: dict[str, Any]) -> None:
        """The address this PC uses to reach the server (what the server's log shows for every program here)."""
        now = self.clock()
        if now - ep.route_t < 600:
            return
        ep.route_t = now
        host = urlsplit(info.get("base_url") or "").hostname
        if host:
            ip = self.route(host)
            if ip and not is_loopback(ip):
                self._add_pc(clean_ip(ip), "route")

    def _add_pc(self, ip: str, how: str) -> None:
        with self._lock:
            if ip not in self._pc or self._pc[ip] == "saved":
                self._pc[ip] = how
                self._dirty = True

    # ------------------------------------------------------------------ ingesting
    def ingest(self, key: str, lines: list[str], now: Optional[float] = None, *, info: Optional[dict[str, Any]] = None) -> int:
        """Count the access lines among ``lines`` (older than the cursor: skipped; stamped exactly at it: skipped when already counted).
        Returns the number of requests counted."""
        now = self.clock() if now is None else now
        with self._lock:
            ep = self._eps.get(key)
            if ep is None:
                ep = self._eps[key] = self._restore(key, info or {"recipe": key, "title": key, "base_url": "", "head": "", "nodes": []})
            horizon = now + ep.skew - KEEP_S
            counted, newest, at_newest = 0, ep.cursor, set(ep.at_cursor)
            for line in lines:
                a = parse_line(line)
                if a is None:
                    continue
                if a.ns is not None and ep.cursor is not None:
                    if a.ns < ep.cursor or (a.ns == ep.cursor and line in ep.at_cursor):
                        continue
                t = a.ns / 1e9 if a.ns is not None else now + ep.skew
                if t < horizon:
                    continue
                if a.ns is not None:
                    if newest is None or a.ns > newest:
                        newest, at_newest = a.ns, {line}
                    elif a.ns == newest:
                        at_newest.add(line)
                minute = int(t // 60)
                counted += 1
                if is_loopback(a.ip):
                    ep.loop[minute] = ep.loop.get(minute, 0) + 1
                    continue
                rec = ep.ips.setdefault(a.ip, {"first": t, "last": t, "b": {}})
                rec["first"], rec["last"] = min(rec["first"], t), max(rec["last"], t)
                row = rec["b"].setdefault(minute, [0] * (len(KINDS) + 1))
                kind = classify(a.method, a.path)
                row[KINDS.index(kind)] += 1
                if not 200 <= a.code < 300 and not (kind == "poll" and a.code == 404):
                    row[ERR] += 1      # a probe for another engine's route answered 404 is discovery, not a failure
            ep.cursor, ep.at_cursor = newest, at_newest
            self._prune(ep, now + ep.skew)
            if counted:
                self._dirty = True
            return counted

    def _prune(self, ep: _Ep, ref: float) -> None:
        cut = int((ref - KEEP_S) // 60)
        for ip in list(ep.ips):
            rec = ep.ips[ip]
            rec["b"] = {m: row for m, row in rec["b"].items() if m >= cut}
            if not rec["b"] and rec["last"] < ref - KEEP_S:
                del ep.ips[ip]
        ep.loop = {m: n for m, n in ep.loop.items() if m >= cut}

    # ------------------------------------------------------------------ this PC
    def local_tick(self) -> None:
        """Which programs of this PC hold connections to each server right now (cheap; the page asks every 2 s)."""
        with self._lock:
            targets = []
            for key, ep in self._eps.items():
                u = urlsplit(ep.info.get("base_url") or "")
                if u.hostname and u.port:
                    targets.append({"key": key, "host": u.hostname, "port": u.port})
        try:
            res = local_processes(targets, self.psutil, self.resolve)
        except Exception as exc:  # noqa: BLE001
            log.exception("local connections")
            res = {"error": f"{exc.__class__.__name__}", "by_key": {}}
        with self._lock:
            self._local = {"t": self.clock(), **res}
        for entry in res["by_key"].values():
            for ip in entry["local_ips"]:
                if not is_loopback(ip):
                    self._add_pc(ip, "connection")

    # ------------------------------------------------------------------ persistence
    def _dump(self, ep: _Ep) -> dict[str, Any]:
        return {"title": ep.info.get("title") or ep.info.get("recipe"), "cursor": ep.cursor,
                "ips": {ip: {"first": round(r["first"], 1), "last": round(r["last"], 1),
                             "b": [[m, *row] for m, row in sorted(r["b"].items())]} for ip, r in ep.ips.items()},
                "loop": [[m, n] for m, n in sorted(ep.loop.items())]}

    def save(self, *, force: bool = False) -> None:
        now = self.clock()
        with self._lock:
            if not self._dirty or (not force and now - self._saved < self.save_s):
                return
            endpoints = {k: v for k, v in self._store["endpoints"].items() if k not in self._eps}
            endpoints.update({k: self._dump(ep) for k, ep in self._eps.items()})
            for k in [k for k, v in endpoints.items() if not v["ips"] and not v["loop"]]:
                del endpoints[k]
            self._store["endpoints"] = endpoints
            data = {"version": 1, "this_pc": sorted(self._pc), "endpoints": endpoints}
            self._dirty = False
            self._saved = now
        try:
            write_json_atomic(self.path, data)
        except OSError:
            log.exception("saving %s", self.path)
            self._dirty = True

    # ------------------------------------------------------------------ views
    def identity(self, ip: str, names: Optional[dict[str, str]] = None) -> dict[str, Any]:
        """What is known about an address: the name the user gave, whether it is this PC or a Spark, and the label to show."""
        names = self.names() if names is None else names
        custom = str(names.get(ip) or "")
        sparks = self.spark_addrs()
        role, spark = "", ""
        if ip in sparks:
            role, spark = "spark", sparks[ip]
        if ip in self._pc:     # a Spark that is also this PC (the app running on a Spark) is still this PC
            role, spark = "this_pc", ""
        label = custom or (THIS_PC if role == "this_pc" else (self.node_name(spark) if role == "spark" else ip))
        return {"ip": ip, "name": custom, "role": role, "spark": spark, "label": label}

    def client_view(self, ip: str, rec: dict[str, Any], ref: float, names: dict[str, str]) -> dict[str, Any]:
        total = [0] * (len(KINDS) + 1)
        hour = [0] * (len(KINDS) + 1)
        edge = ref - HOUR_S
        for minute, row in rec["b"].items():
            for i, n in enumerate(row):
                total[i] += n
                if minute * 60 + 60 > edge:
                    hour[i] += n
        by_kind = {k: total[i] for i, k in enumerate(KINDS) if total[i]}
        inference = sum(total[:len(INFERENCE)])
        activity = "inferring" if inference else ("other" if total[KINDS.index("other")] else "polling")
        return {**self.identity(ip, names), "this_pc": ip in self._pc, "first": rec["first"], "last": rec["last"],
                "requests": sum(total[:len(KINDS)]), "requests_1h": sum(hour[:len(KINDS)]), "inference": inference, "inference_1h": sum(hour[:len(INFERENCE)]),
                "by_kind": by_kind, "kinds": [k for k in sorted((k for k in by_kind if k != "poll"), key=lambda k: -by_kind[k])],
                "errors": total[ERR], "errors_1h": hour[ERR], "activity": activity, "only_polling": activity == "polling"}

    def view(self, key: str) -> Optional[dict[str, Any]]:
        """The «Clientes» section of one endpoint (None when the endpoint is not followed)."""
        with self._lock:
            ep = self._eps.get(key)
            if ep is None:
                return None
            names = self.names()
            ref = self.clock() + ep.skew
            clients = [self.client_view(ip, rec, ref, names) for ip, rec in ep.ips.items()]
            rank = {"inferring": 0, "other": 1, "polling": 2}
            clients.sort(key=lambda c: (rank[c["activity"]], -c["requests_1h"], -c["last"], c["ip"]))
            probes = sum(ep.loop.values())
            local = self._local.get("by_key", {}).get(key)
            pc = {"ips": sorted(self._pc), "error": self._local.get("error"), "age_s": round(self.clock() - self._local["t"], 1) if self._local["t"] else None,
                  "connections": local["connections"] if local else 0, "processes": local["processes"] if local else []}
            return {"ok": ep.ok, "error": ep.error, "source": ep.via, "container": ep.container, "truncated": ep.truncated, "window_h": int(KEEP_S // 3600),
                    "age_s": round(self.clock() - ep.last_ok, 1) if ep.last_ok else None, "local_probes": probes, "clients": clients, "this_pc": pc}

    def snapshot(self, recipe: Optional[str] = None, *, poll: Optional[bool] = None) -> dict[str, dict[str, Any]]:
        """``{recipe: view}`` for every followed endpoint (or just ``recipe``). Without the collector thread (tests) the reads run here."""
        self.touch()
        running = bool(self._thread and self._thread.is_alive())
        if poll if poll is not None else not running:
            self.poll_once(wait=True)
        elif running and not self._eps and self.clock() - self._listed >= self.refresh_s:
            self.refresh_list()
        with self._lock:
            keys = [k for k in self._eps if not recipe or k == recipe]
        return {k: v for k in keys if (v := self.view(k)) is not None}
