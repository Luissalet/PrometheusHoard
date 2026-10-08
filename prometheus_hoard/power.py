"""Power menu of a Spark, as on Windows: lock, sleep, shut down, restart, and turn on (wake-on-LAN).

Shutting down, restarting and sleeping need root on the Spark: the app runs ``sudo -n systemctl …`` (never asks for a password) and,
when sudo needs one, falls back to plain ``systemctl``, which polkit allows only to some sessions. When both refuse, the answer says how
to allow it once (a sudoers line). Turning on sends a wake-on-LAN packet to the wired MAC address the Spark had the last time it answered;
it works only if the Spark's firmware has wake-on-LAN enabled.

Before a shutdown, restart or sleep, the models the Spark serves are unloaded (their ``stop.sh``) so they do not come back half-started;
``keep_models`` skips that."""

from __future__ import annotations

import socket
import time
from typing import Any, Callable, Optional

from .cluster import Cluster, Node
from .errors import SparkError
from .hoard_link.atomic import read_json, write_json_atomic
from .recipes import Recipes

ACTIONS = ("lock", "sleep", "shutdown", "restart", "wake")
SYSTEMCTL = {"sleep": "suspend", "shutdown": "poweroff", "restart": "reboot"}
SUDOERS_HINT = ("Allow it once on each Spark with: echo \"$USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl\" | sudo tee /etc/sudoers.d/prometheus "
                "(or a NOPASSWD line for your user).")


def magic_packet(mac: str) -> bytes:
    clean = mac.replace(":", "").replace("-", "").lower()
    if len(clean) != 12:
        raise SparkError("invalid", f"{mac!r} is not a MAC address.")
    return bytes.fromhex("ff" * 6 + clean * 16)


def send_wol(mac: str, broadcasts: tuple[str, ...] = ("255.255.255.255",)) -> None:
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for addr in broadcasts:
            for port in (9, 7):
                try:
                    s.sendto(packet, (addr, port))
                except OSError:
                    pass


class Power:
    def __init__(self, cluster: Cluster, recipes: Recipes, macs_path, *, wol: Callable[[str, tuple[str, ...]], None] = send_wol,
                 on_event: Optional[Callable[[str, dict[str, Any]], None]] = None):
        self.cluster = cluster
        self.recipes = recipes
        self.macs_path = macs_path
        self.wol = wol
        self.on_event = on_event
        self.macs: dict[str, dict[str, Any]] = read_json(macs_path, default={}) or {}

    def remember(self) -> None:
        changed = False
        for n in self.cluster.enabled():
            if n.mac and self.macs.get(n.id, {}).get("mac") != n.mac:
                lan = [v for v in (n.metrics or {}).get("net", {}).values() if not v.get("fabric") and v.get("mac") == n.mac]
                ip = lan[0]["v4"][0] if lan and lan[0].get("v4") else ""
                self.macs[n.id] = {"mac": n.mac, "ip": ip, "seen": time.time()}
                changed = True
        if changed:
            write_json_atomic(self.macs_path, self.macs)

    def _targets(self, node: str) -> list[Node]:
        if node in ("all", "*", "cluster"):
            return self.cluster.enabled()
        return [self.cluster.node(node)]

    def act(self, node: str, action: str, *, confirm: bool = False, keep_models: bool = False) -> dict[str, Any]:
        if action not in ACTIONS:
            raise SparkError("invalid", f"Unknown power action {action!r}.", hint="One of: " + ", ".join(ACTIONS))
        targets = self._targets(node)
        if action in ("shutdown", "restart", "sleep") and not confirm:
            names = ", ".join(n.conf["name"] for n in targets)
            raise SparkError("confirm_required", f"{action} {names}: whatever runs there stops.", hint="Repeat with confirm=true if the user asked for it.")
        results = []
        if action in ("shutdown", "restart", "sleep") and not keep_models:
            ids = {n.id for n in targets}
            for d in self.recipes.deployments(check=True):
                if d["state"] in ("running", "starting") and ids & set(d["nodes"]):
                    try:
                        self.recipes.stop(d["recipe"], wait=True)
                        results.append({"unloaded": d["recipe"]})
                    except SparkError as exc:
                        results.append({"unloaded": d["recipe"], "error": exc.message})
        for n in targets:
            results.append(self._one(n, action))
        return {"action": action, "results": results}

    def _one(self, n: Node, action: str) -> dict[str, Any]:
        name = n.conf["name"]
        if action == "wake":
            info = self.macs.get(n.id) or ({"mac": n.mac} if n.mac else None)
            if not info or not info.get("mac"):
                raise SparkError("unavailable", f"The MAC address of {name} is not known yet.", hint="It is learnt the first time the Spark answers.")
            ip = info.get("ip", "").split("/")[0]
            broadcasts = ("255.255.255.255",) + ((ip.rsplit(".", 1)[0] + ".255",) if ip.count(".") == 3 else ())
            self.wol(info["mac"], broadcasts)
            n.power_state = "waking"
            n.power_since = time.time()
            self._event("power.wake", n)
            return {"node": n.id, "ok": True, "message": f"Wake-on-LAN sent to {info['mac']}. If the firmware allows it, {name} starts in a minute or two."}
        if not n.online:
            raise SparkError("unreachable", f"{name} is not answering: it may already be off.")
        if action == "lock":
            res = n.transport.run("loginctl lock-sessions", timeout=15)
            if res.rc != 0:
                raise SparkError("remote_failed", f"{name}: could not lock its sessions ({res.err.strip()[:200]}).")
            return {"node": n.id, "ok": True, "message": f"{name}: screen sessions locked."}
        verb = SYSTEMCTL[action]
        # delayed a second so the SSH command returns before the machine goes away
        res = n.transport.run(f"sudo -n true 2>/dev/null && (sleep 1; sudo -n systemctl {verb}) >/dev/null 2>&1 & "
                              f"if sudo -n true 2>/dev/null; then echo SUDO; else systemctl --no-ask-password {verb} 2>&1 && echo POLKIT; fi", timeout=20)
        out = res.out.strip()
        if "SUDO" not in out and "POLKIT" not in out:
            raise SparkError("forbidden", f"{name} refused to {action}: {(res.err or out).strip()[:200]}", hint=SUDOERS_HINT)
        n.power_state = {"restart": "restarting", "shutdown": "shutting_down", "sleep": "sleeping"}[action]
        n.power_since = time.time()
        self._event(f"power.{action}", n)
        return {"node": n.id, "ok": True, "message": {"restart": f"{name} is restarting.", "shutdown": f"{name} is shutting down.",
                                                         "sleep": f"{name} is going to sleep."}[action]}

    def _event(self, kind: str, n: Node) -> None:
        if self.on_event:
            self.on_event(kind, {"node": n.id, "name": n.conf["name"]})
