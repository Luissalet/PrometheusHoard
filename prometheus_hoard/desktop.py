"""Remote desktop of the Sparks: a TigerVNC + XFCE session on demand, seen in the browser through a tunnel on the node's SSH connection.

Per Spark: the packages are installed once (a confirmed, detached job: ``sudo -n apt-get install``), a session is started (display, size,
keyboard and idle limit are settings) and stopped again; ``remote/desktop.py`` does the work on the Spark and a watchdog there ends the
session after ``desktop_idle_min`` minutes with no viewer. The VNC server only listens on 127.0.0.1 of the Spark. The browser talks to
``/api/desktop/<node>/ws``, which bridges to that port through a ``direct-tcpip`` channel (``Transport.open_tunnel``): no extra port is
opened on the Spark or on this PC. The per-node VNC password is random, kept in ``<data>/desktop-passwords.json`` and in
``~/.config/prometheus-desktop/passwd`` on the Spark (already obscured by ``vncpasswd -f``); the agent tools never return it, only the
page of this app asks for it."""

from __future__ import annotations

import os
import secrets
import shlex
import string
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from .cluster import Cluster, Node
from .errors import SparkError
from .hoard_link.atomic import read_json, write_json_atomic
from .jobs import Jobs
from .transport import run_script

PACKAGES = ("tigervnc-standalone-server", "tigervnc-common", "tigervnc-tools", "xfce4-session", "xfwm4", "xfce4-panel", "xfdesktop4", "xfce4-terminal",
            "xfce4-settings", "thunar", "dbus-x11")
PASSWORD_LEN = 8        # the VNC authentication only looks at the first 8 characters


def install_command(packages: tuple[str, ...] = PACKAGES) -> str:
    pkgs = " ".join(packages)
    flags = "-y --no-install-recommends"
    apt = "sudo -n env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get"     # sudo drops the environment: pass it explicitly
    return f"{apt} install {flags} {pkgs} || ({apt} update && {apt} install {flags} {pkgs})"


class Desktop:
    def __init__(self, cluster: Cluster, jobs: Jobs, passwords_path, *, on_event: Optional[Callable[[str, dict[str, Any]], None]] = None):
        self.cluster = cluster
        self.jobs = jobs
        self.path = passwords_path
        self.on_event = on_event
        self._lock = threading.RLock()
        self._viewers: dict[str, int] = {}

    # ------------------------------------------------------------------ settings and passwords
    def options(self) -> dict[str, Any]:
        s = self.cluster.settings
        display = s["desktop_display"]
        return {"display": display, "port": 5900 + int(display[1:]), "geometry": s["desktop_geometry"], "idle_min": s["desktop_idle_min"],
                "keymap": s["desktop_keymap"]}

    def _password(self, node: Node) -> str:
        with self._lock:
            data = read_json(self.path, default={}) or {}
            if not data.get(node.id):
                alphabet = string.ascii_letters + string.digits
                data[node.id] = "".join(secrets.choice(alphabet) for _ in range(PASSWORD_LEN))
                write_json_atomic(self.path, data)
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            return data[node.id]

    # ------------------------------------------------------------------ remote calls
    def _call(self, node: Node, op: str, extra: Optional[dict[str, Any]] = None, *, timeout: float = 30.0) -> dict[str, Any]:
        args = {**self.options(), "op": op, "packages": list(PACKAGES), **(extra or {})}
        return run_script(node.transport, "desktop", args, timeout=timeout)

    def _view(self, node: Node, raw: dict[str, Any]) -> dict[str, Any]:
        opts = self.options()
        return {"node": node.id, "name": node.conf["name"], "online": True, "installed": bool(raw.get("installed")), "missing": raw.get("missing") or [],
                "running": bool(raw.get("running")), "viewers": int(raw.get("viewers") or 0), "display": raw.get("display", opts["display"]),
                "port": raw.get("port", opts["port"]), "geometry": opts["geometry"], "idle_min": opts["idle_min"], "uptime_s": raw.get("uptime_s"),
                "sudo_ok": raw.get("sudo_ok"), "watchdog": bool(raw.get("watchdog")), "install_job": self._install_job(node.id), "bridges": self._viewers.get(node.id, 0)}

    def _install_job(self, node_id: str) -> Optional[dict[str, Any]]:
        mine = [j for j in self.jobs.list(node=node_id, limit=300) if j["kind"] == "desktop_install"]
        return mine[0] if mine else None

    def status(self, node_id: str = "") -> dict[str, Any]:
        """One Spark, or every enabled one when ``node_id`` is empty: installed? running? display, viewers, the install job if any."""
        self.cluster.touch()
        if node_id:
            return self._status_one(self.cluster.node(node_id))
        nodes = self.cluster.enabled()
        with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
            return {"nodes": list(pool.map(self._status_one, nodes)), **self.options()}

    def _status_one(self, node: Node) -> dict[str, Any]:
        if not node.online:
            self.cluster.probe(node)
        if not node.online:
            return {"node": node.id, "name": node.conf["name"], "online": False, "installed": False, "running": False, "error": node.error or "offline",
                    "missing": [], "viewers": 0}
        try:
            return self._view(node, self._call(node, "status"))
        except SparkError as exc:
            return {"node": node.id, "name": node.conf["name"], "online": True, "installed": False, "running": False, "missing": [], "viewers": 0,
                    "error": exc.message}

    # ------------------------------------------------------------------ actions
    def install(self, node_id: str, *, confirm: bool = False) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        if not confirm:
            raise SparkError("confirm_required", f"Installing the remote desktop adds about {len(PACKAGES)} packages (TigerVNC and XFCE) to {node.conf['name']} with apt.",
                             hint="Repeat with confirm=true if the user wants the remote desktop on that Spark.")
        st = self._call(node, "status")
        if st.get("installed"):
            return {"installed": True, "message": f"{node.conf['name']} already has the remote desktop packages.", "job": None}
        if st.get("sudo_ok") is False:
            raise SparkError("forbidden", f"{node.conf['name']}: sudo asks for a password, so the packages cannot be installed from here.",
                             hint="Allow it once on that Spark: echo \"$USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get\" | sudo tee /etc/sudoers.d/prometheus-apt, or install: sudo apt install " + " ".join(PACKAGES))
        running = [j for j in self.jobs.active("desktop_install") if j["node"] == node.id]
        if running:
            raise SparkError("busy", f"The install on {node.conf['name']} is already running.", hint="Follow it with job_get.")
        job = self.jobs.start(node.id, install_command(), kind="desktop_install", title=f"Remote desktop packages on {node.conf['name']}",
                              meta={"packages": list(PACKAGES), "missing": st.get("missing") or []})
        self._event("desktop.install", node)
        return {"installed": False, "job": job, "message": "Installing in the background; follow the job, then start the desktop."}

    def start(self, node_id: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        if not node.online:
            raise SparkError("unreachable", f"{node.conf['name']} is not answering: it may be off.")
        st = self._call(node, "status")
        if not st.get("installed"):
            raise SparkError("unavailable", f"{node.conf['name']} does not have the remote desktop packages yet.",
                             hint="Run desktop_install (confirm=true) first.", missing=st.get("missing") or [])
        password = self._password(node)
        d = "~/.config/prometheus-desktop"
        res = node.transport.run(f"umask 077 && mkdir -p {d} && V=$(command -v vncpasswd || command -v tigervncpasswd) && "
                                 f"$V -f > {d}/passwd.new && mv {d}/passwd.new {d}/passwd", timeout=20.0, stdin=password + "\n")
        if res.rc != 0:
            raise SparkError("remote_failed", f"{node.conf['name']}: could not store the session password ({res.err.strip()[:200]}).")
        out = self._call(node, "start", timeout=60.0)
        self._event("desktop.start", node)
        return self._view(node, out) | {"started": bool(out.get("started")), "already": bool(out.get("already"))}

    def stop(self, node_id: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        if not node.online:
            raise SparkError("unreachable", f"{node.conf['name']} is not answering: its desktop is gone with it.")
        out = self._call(node, "stop", timeout=40.0)
        self._event("desktop.stop", node)
        return self._view(node, out) | {"stopped": True}

    # ------------------------------------------------------------------ browser side
    def session(self, node_id: str) -> dict[str, Any]:
        """What the page needs to connect (the password included: only the UI route returns this)."""
        node = self.cluster.node(node_id)
        st = self._status_one(node)
        if not st.get("running"):
            raise SparkError("unavailable", f"{node.conf['name']} has no desktop running.", hint="Start it first (Escritorio > Iniciar).")
        return {**st, "password": self._password(node), "ws": f"/api/desktop/{node.id}/ws"}

    def open_bridge(self, node_id: str) -> Any:
        node = self.cluster.node(node_id)
        if not node.online:
            raise SparkError("unreachable", f"{node.conf['name']} is not answering.")
        chan = node.transport.open_tunnel("127.0.0.1", self.options()["port"])
        with self._lock:
            self._viewers[node.id] = self._viewers.get(node.id, 0) + 1
        return chan

    def close_bridge(self, node_id: str) -> None:
        with self._lock:
            self._viewers[node_id] = max(0, self._viewers.get(node_id, 0) - 1)

    def _event(self, kind: str, node: Node) -> None:
        if self.on_event:
            self.on_event(kind, {"node": node.id, "name": node.conf["name"]})
