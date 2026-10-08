"""The Sparks as live objects: a transport per node, a poller that probes them, rates computed between snapshots and a short history.

The poller probes every node every ``poll_s`` seconds while someone looked at the app in the last minute (``touch()``), and every
``idle_poll_s`` otherwise, so an idle app costs the Sparks nothing noticeable. A node that does not answer is shown as offline with
the reason; it is retried on the next round (the transport backs off for a few seconds after a failed connection)."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from .config import Settings
from .errors import SparkError
from .transport import SshTransport, Transport, run_script

log = logging.getLogger("prometheus.cluster")


def cpu_percent(prev: list[int], cur: list[int]) -> Optional[float]:
    """Busy share between two ``/proc/stat`` rows (user nice system idle iowait irq softirq steal ...)."""
    if not prev or not cur or len(prev) != len(cur):
        return None
    deltas = [c - p for c, p in zip(cur, prev)]
    total = sum(deltas[:8])
    if total <= 0:
        return None
    idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)
    return round(100.0 * (total - idle) / total, 1)


def parse_server(cmd: str) -> dict[str, Any]:
    """What an inference server serves, read from its command line (vLLM, SGLang, llama-server, Ollama)."""
    parts = cmd.split()
    info: dict[str, Any] = {"engine": "", "model": "", "port": None, "served_name": "", "max_len": None, "tp": None}
    low = cmd.lower()
    for engine in ("vllm", "sglang", "llama-server", "ollama", "trtllm"):
        if engine in low:
            info["engine"] = engine
            break

    def flag(*names: str) -> Optional[str]:
        for i, p in enumerate(parts):
            for n in names:
                if p == n and i + 1 < len(parts):
                    return parts[i + 1]
                if p.startswith(n + "="):
                    return p.split("=", 1)[1]
        return None

    port = flag("--port")
    info["port"] = int(port) if port and port.isdigit() else None
    info["served_name"] = flag("--served-model-name", "--alias") or ""
    info["model"] = flag("--model", "--model-path", *(("-m",) if info["engine"] == "llama-server" else ())) or ""
    if not info["model"] and info["engine"] == "vllm":
        for i, p in enumerate(parts):
            if p == "serve" and i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                info["model"] = parts[i + 1]
                break
    ml = flag("--max-model-len", "--context-length", "-c", "--ctx-size")
    info["max_len"] = int(ml) if ml and ml.isdigit() else None
    tp = flag("--tensor-parallel-size", "-tp", "--tp-size", "--tp")
    info["tp"] = int(tp) if tp and tp.isdigit() else None
    return info


class Node:
    def __init__(self, conf: dict[str, Any], transport: Transport):
        self.conf = conf
        self.transport = transport
        self.last: Optional[dict[str, Any]] = None       # last raw probe
        self.prev: Optional[dict[str, Any]] = None
        self.metrics: Optional[dict[str, Any]] = None    # last computed view
        self.error = ""
        self.error_since: Optional[float] = None
        self.history: deque[dict[str, Any]] = deque(maxlen=1800)
        self.probe_ms: Optional[int] = None
        self.power_state = ""                            # "", "restarting", "shutting_down", "sleeping", "off", "waking"
        self.power_since: Optional[float] = None
        self.mac = ""                                    # LAN MAC, remembered for wake-on-LAN

    @property
    def id(self) -> str:
        return self.conf["id"]

    @property
    def online(self) -> bool:
        return self.metrics is not None and not self.error

    def api_host(self) -> str:
        if self.conf.get("api_host"):
            return self.conf["api_host"]
        target = self.transport.target()
        return target.get("hostname") or self.conf["ssh"]

    def compute(self, raw: dict[str, Any]) -> dict[str, Any]:
        prev = self.last
        cpu = raw.get("cpu", {})
        mem = raw.get("memory", {})
        dt = (raw.get("time", 0) - prev.get("time", 0)) if prev else 0
        cpu_pct = cpu_percent(prev.get("cpu", {}).get("counters", []), cpu.get("counters", [])) if prev else None
        cores = []
        if prev:
            for p, c in zip(prev.get("cpu", {}).get("per_core", []), cpu.get("per_core", [])):
                cores.append(cpu_percent(p, c))
        net = {}
        for name, cur in (raw.get("net") or {}).items():
            before = (prev or {}).get("net", {}).get(name)
            rx = tx = None
            if before and dt > 0:
                rx = max(0.0, (cur["rx"] - before["rx"]) / dt)
                tx = max(0.0, (cur["tx"] - before["tx"]) / dt)
            net[name] = {"rx_bps": rx, "tx_bps": tx, "speed_mbps": cur.get("speed_mbps"), "v4": cur.get("v4", []), "up": cur.get("up"),
                         "mtu": cur.get("mtu"), "fabric": cur.get("fabric", False), "mac": cur.get("mac", "")}
        gpus = raw.get("gpu", {}).get("gpus", [])
        g0 = gpus[0] if gpus else {}
        total = mem.get("total", 0) or 0
        used = total - (mem.get("available", 0) or 0)
        gpu_mem = sum((a.get("mem_mb") or 0) for a in raw.get("gpu", {}).get("apps", [])) * 1024 * 1024
        servers = []
        for s in raw.get("servers", []):
            info = parse_server(s.get("cmd", ""))
            info.update({"pid": s.get("pid"), "rss": s.get("rss")})
            if info["engine"]:
                servers.append(info)
        view = {
            "hostname": raw.get("hostname"), "os": raw.get("os"), "kernel": raw.get("kernel"), "arch": raw.get("arch"),
            "uptime_s": raw.get("uptime_s"), "home": raw.get("home"), "user": raw.get("user"),
            "cpu": {"percent": cpu_pct, "cores": cpu.get("cores"), "per_core": cores, "load": cpu.get("load"), "temp_c": cpu.get("temp_c")},
            "memory": {"total": total, "used": used, "available": mem.get("available"), "cached": mem.get("cached"),
                       "gpu_apps": gpu_mem, "percent": round(100.0 * used / total, 1) if total else None},
            "gpu": {"name": g0.get("name"), "util": g0.get("util"), "temp_c": g0.get("temp_c"), "power_w": g0.get("power_w"),
                    "power_limit_w": g0.get("power_limit_w"), "clock_mhz": g0.get("clock_mhz"), "clock_max_mhz": g0.get("clock_max_mhz"),
                    "pstate": g0.get("pstate"), "driver": g0.get("driver"), "apps": raw.get("gpu", {}).get("apps", [])},
            "disks": raw.get("disks", []), "net": net, "top": raw.get("top", []), "servers": servers,
            "containers": raw.get("containers", []), "docker": raw.get("docker", False),
        }
        return view

    def sample(self, view: dict[str, Any], t: float) -> dict[str, Any]:
        fabric_rx = sum((n["rx_bps"] or 0) for n in view["net"].values() if n.get("fabric"))
        fabric_tx = sum((n["tx_bps"] or 0) for n in view["net"].values() if n.get("fabric"))
        lan = [n for n in view["net"].values() if not n.get("fabric")]
        return {"t": round(t, 1), "cpu": view["cpu"]["percent"], "gpu": view["gpu"]["util"], "mem": view["memory"]["percent"],
                "mem_used": view["memory"]["used"], "power": view["gpu"]["power_w"], "temp": view["gpu"]["temp_c"],
                "fabric_rx": round(fabric_rx), "fabric_tx": round(fabric_tx),
                "lan_rx": round(sum((n["rx_bps"] or 0) for n in lan)), "lan_tx": round(sum((n["tx_bps"] or 0) for n in lan))}


TransportFactory = Callable[[dict[str, Any], Settings], Transport]


def ssh_factory(conf: dict[str, Any], settings: Settings) -> Transport:
    return SshTransport(conf["id"], conf["ssh"], timeout=float(settings["ssh_timeout_s"]), host=conf.get("host", ""), proxy_jump=conf.get("proxy_jump", ""))


class Cluster:
    def __init__(self, settings: Settings, factory: TransportFactory = ssh_factory, *, clock: Callable[[], float] = time.time,
                 on_change: Optional[Callable[[str, dict[str, Any]], None]] = None):
        self.settings = settings
        self.factory = factory
        self.clock = clock
        self.on_change = on_change
        self.nodes: dict[str, Node] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_touch = 0.0
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="probe")
        self.rebuild()

    # ------------------------------------------------------------------ nodes
    def rebuild(self) -> None:
        """(Re)create the nodes from the settings, keeping the live state of the ones that did not change."""
        with self._lock:
            confs = [n for n in self.settings["nodes"]]
            fresh: dict[str, Node] = {}
            for conf in confs:
                old = self.nodes.get(conf["id"])
                if old and all(old.conf.get(k) == conf.get(k) for k in ("ssh", "host", "proxy_jump")):
                    old.conf = conf
                    fresh[conf["id"]] = old
                else:
                    if old:
                        old.transport.close()
                    fresh[conf["id"]] = Node(conf, self.factory(conf, self.settings))
            for node_id, old in self.nodes.items():
                if node_id not in fresh:
                    old.transport.close()
            self.nodes = fresh

    def node(self, node_id: str) -> Node:
        key = (node_id or "").strip().lower()
        with self._lock:
            if key in self.nodes:
                return self.nodes[key]
            for n in self.nodes.values():  # accept the display name or the SSH alias too
                if key in (n.conf["name"].lower(), n.conf["ssh"].lower()):
                    return n
        raise SparkError("not_found", f"There is no Spark called {node_id!r}.", hint="Known: " + ", ".join(self.nodes) or "none")

    def enabled(self) -> list[Node]:
        with self._lock:
            return [n for n in self.nodes.values() if n.conf.get("enabled", True)]

    # ------------------------------------------------------------------ probing
    def probe(self, node: Node) -> None:
        t0 = time.monotonic()
        try:
            raw = run_script(node.transport, "probe", {"models_dirs": self.settings["models_dirs"]}, timeout=20.0)
        except SparkError as exc:
            was_online = node.online
            node.error = exc.message
            node.error_since = node.error_since or self.clock()
            node.metrics = None
            node.last = None
            if node.power_state in ("restarting", "waking") and node.power_since and self.clock() - node.power_since > 600:
                node.power_state = ""
            if node.power_state in ("shutting_down",) and node.power_since and self.clock() - node.power_since > 15:
                node.power_state = "off"
            if was_online and self.on_change:
                self.on_change("offline", {"node": node.id, "error": exc.message})
            return
        view = node.compute(raw)
        was_online = node.online
        node.prev, node.last = node.last, raw
        node.metrics = view
        node.error = ""
        node.error_since = None
        node.probe_ms = int((time.monotonic() - t0) * 1000)
        if node.power_state in ("restarting", "waking", "off", "sleeping"):
            node.power_state = ""
            node.power_since = None
        lan = [(k, v) for k, v in (raw.get("net") or {}).items() if not v.get("fabric") and v.get("v4") and v.get("mac")]
        lan.sort(key=lambda kv: (not kv[0].startswith("en"), kv[0]))   # wired before Wi-Fi: wake-on-LAN needs the cable
        if lan:
            node.mac = lan[0][1]["mac"]
        node.history.append(node.sample(view, self.clock()))
        keep = self.clock() - 60 * int(self.settings["history_min"])
        while node.history and node.history[0]["t"] < keep:
            node.history.popleft()
        if not was_online and self.on_change:
            self.on_change("online", {"node": node.id})

    def probe_all(self) -> None:
        nodes = self.enabled()
        list(self._pool.map(self.probe, nodes))

    def touch(self) -> None:
        self._last_touch = time.monotonic()

    def _interval(self) -> float:
        active = time.monotonic() - self._last_touch < 60
        return float(self.settings["poll_s"] if active else self.settings["idle_poll_s"])

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="prometheus-poller", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.probe_all()
            except Exception:  # noqa: BLE001 - the poller must survive anything
                log.exception("probe round failed")
            wait = max(0.2, self._interval() - (time.monotonic() - started))
            # wake up early when someone starts looking (touch) after an idle period
            end = time.monotonic() + wait
            while not self._stop.is_set() and time.monotonic() < end:
                if time.monotonic() - self._last_touch < 1.0 and wait > float(self.settings["poll_s"]):
                    break
                self._stop.wait(0.25)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            for n in self.nodes.values():
                n.transport.close()

    # ------------------------------------------------------------------ views
    def node_view(self, node: Node, *, detail: bool = True) -> dict[str, Any]:
        target = node.transport.target()
        out: dict[str, Any] = {
            "id": node.id, "name": node.conf["name"], "ssh": node.conf["ssh"], "enabled": node.conf.get("enabled", True),
            "color": node.conf.get("color", ""), "host": target.get("hostname"), "user": target.get("user"), "api_host": node.api_host(),
            "online": node.online, "error": node.error, "error_since": node.error_since, "probe_ms": node.probe_ms,
            "power_state": node.power_state or ("on" if node.online else ("off" if node.error else "unknown")),
        }
        if node.metrics:
            m = node.metrics
            out.update({"hostname": m["hostname"], "os": m["os"], "kernel": m["kernel"], "uptime_s": m["uptime_s"], "home": m["home"],
                        "cpu": m["cpu"], "memory": m["memory"], "gpu": m["gpu"], "disks": m["disks"], "servers": m["servers"]})
            if detail:
                out.update({"net": m["net"], "top": m["top"], "containers": m["containers"], "docker": m["docker"]})
            else:
                out["fabric"] = {k: {"up": v["up"], "speed_mbps": v["speed_mbps"], "rx_bps": v["rx_bps"], "tx_bps": v["tx_bps"], "v4": v["v4"]}
                                 for k, v in m["net"].items() if v.get("fabric")}
        return out

    def fabric_peer_ip(self, src: Node, dst: Node) -> Optional[str]:
        """An address of ``dst`` on a CX7 subnet that ``src`` also has (a direct cable between them), or None."""
        if not src.metrics or not dst.metrics:
            return None

        def nets(node: Node) -> list[tuple[str, str]]:
            out = []
            for v in node.metrics["net"].values():
                if not v.get("fabric") or not v.get("up"):
                    continue
                for cidr in v.get("v4", []):
                    ip, _, prefix = cidr.partition("/")
                    if prefix == "24":
                        out.append((ip.rsplit(".", 1)[0], ip))
            return out

        src_subnets = {s for s, _ in nets(src)}
        for subnet, ip in nets(dst):
            if subnet in src_subnets:
                return ip
        return None
