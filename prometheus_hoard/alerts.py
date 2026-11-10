"""Warnings when a Spark stops answering or a model server stops serving, kept as incidents (when it started, when it came back).

``Alerts.check`` runs from the housekeeping thread every few seconds and looks at what the poller and the recipes already know, so it
adds no SSH or HTTP traffic of its own (it only asks the recipes for a fresh health check every 30 s, which runs in the background):

* a Spark is **down** when its probe has failed for ``alert_after_s`` seconds. When this app powered it off, put it to sleep or is
  restarting it, the incident is kept but marked ``expected`` and nobody is notified.
* a model server is **down** when a recipe this app holds as running has failed its health check (``/v1/models`` and its identity) for
  ``alert_after_s`` seconds, or when a server started by hand that answered before stops answering. Unloading a recipe from the app is not
  an incident (its state is no longer ``running``).

Each incident that opens or closes is shown in the app (banner and Historial page), published on the family bus
(``prometheus.alert.spark_down`` / ``spark_up`` / ``server_down`` / ``server_up``) and, unless ``alert_notify`` is off or it was expected,
sent to the hub's notifications. The last 500 incidents are kept in ``<data>/alerts.json``."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .hoard_link.atomic import read_json, write_json_atomic

log = logging.getLogger("prometheus.alerts")

KEEP = 500
EXPECTED_POWER = ("shutting_down", "off", "restarting", "sleeping", "waking")


class Alerts:
    def __init__(self, cluster: Any, recipes: Any, path: Path, options: Callable[[], dict[str, Any]], *,
                 emit: Optional[Callable[[str, dict[str, Any]], None]] = None, notifier: Optional[Callable[..., Any]] = None,
                 clock: Callable[[], float] = time.time):
        self.cluster = cluster
        self.recipes = recipes
        self.path = Path(path)
        self.options = options
        self.emit = emit
        self.notifier = notifier
        self.clock = clock
        self._lock = threading.RLock()
        raw = read_json(self.path, default={}) or {}
        self.incidents: list[dict[str, Any]] = [i for i in (raw.get("incidents") or []) if isinstance(i, dict)] if isinstance(raw, dict) else []
        self._bad_since: dict[str, float] = {}
        self._seen_up: dict[str, float] = {}
        self._checked_health = 0.0
        self._last_servers: dict[str, dict[str, Any]] = {}
        self.last_check = 0.0

    # ------------------------------------------------------------------ the check
    def check(self) -> None:
        opts = self.options()
        if not opts.get("enabled", True):
            return
        after = float(opts.get("after_s", 90))
        now = self.clock()
        self.last_check = now
        for node in self.cluster.enabled():
            key = f"spark:{node.id}"
            if not node.online and node.error_since:
                if now - node.error_since >= after and not self._active(key):
                    expected = node.power_state in EXPECTED_POWER
                    self._open(key, "spark", node.id, node.conf["name"], started=node.error_since, error=node.error, expected=expected,
                               node=node.id)
            elif node.online and self._active(key):
                self._close(key)
        for key, srv in self._servers(now).items():
            if srv["up"]:
                self._seen_up[key] = now
                self._bad_since.pop(key, None)
                if self._active(key):
                    self._close(key)
            elif srv["up"] is False and srv["watch"]:
                since = self._bad_since.setdefault(key, now)
                if now - since >= after and not self._active(key):
                    self._open(key, "server", srv["recipe"], srv["title"], started=since, error=srv["error"], expected=False,
                               node=srv.get("head", ""), base_url=srv.get("base_url", ""))
        # a server that is no longer listed (unloaded, or its process ended) cannot be down any more
        listed = set(self._last_servers)
        for inc in self.active():
            if inc["kind"] == "server" and inc["key"] not in listed:
                self._close(inc["key"], note="gone")


    def _servers(self, now: float) -> dict[str, dict[str, Any]]:
        """Every server this app expects to answer: recipes it holds as running, and servers started by hand that it has seen answer."""
        out: dict[str, dict[str, Any]] = {}
        check = now - self._checked_health >= 30
        if check:
            self._checked_health = now
        try:
            deps = self.recipes.deployments(check=check)
            detected = self.recipes.detected(deps)
        except Exception:  # noqa: BLE001 - a broken recipe folder must not stop the watch
            log.exception("alerts: servers")
            return {key: srv for key, srv in self._last_servers.items()}
        for d in deps:
            key = f"server:{d['recipe']}"
            st = self.recipes.state.get(d["recipe"]) or {}
            stored = st.get("state")
            health = d.get("health") or {}
            if not health or health.get("verifying"):
                continue
            # held as running by this app, or found serving (started elsewhere) and not unloaded from here since it was last seen up
            unloaded_here = stored in ("stopping", "stopped") and st.get("operation") == "stop" and float(st.get("updated") or 0) >= self._seen_up.get(key, 0) - 30
            watch = stored == "running" or (key in self._seen_up and not unloaded_here and stored not in ("starting",))
            if not watch and not health.get("up"):
                continue
            out[key] = {"recipe": d["recipe"], "title": d["title"], "up": bool(health.get("up")), "watch": watch or bool(health.get("up")),
                                            "error": str(health.get("error") or "")[:300], "head": d.get("head", ""), "base_url": d.get("base_url", "")}
        for x in detected:
            key = f"server:{x['recipe']}"
            out[key] = {"recipe": x["recipe"], "title": x["title"], "up": bool(x["up"]), "watch": key in self._seen_up or x["up"],
                        "error": "" if x["up"] else "no answer on /v1/models", "head": x.get("node", ""), "base_url": x.get("base_url", "")}
        self._last_servers = out
        return out

    # ------------------------------------------------------------------ incidents
    def _active(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return next((i for i in reversed(self.incidents) if i["key"] == key and not i.get("ended")), None)

    def active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(i) for i in self.incidents if not i.get("ended")]

    def _open(self, key: str, kind: str, target: str, name: str, *, started: float, error: str, expected: bool, **extra: Any) -> None:
        inc = {"id": uuid.uuid4().hex[:12], "key": key, "kind": kind, "target": target, "name": name, "started": round(started, 1),
               "detected": round(self.clock(), 1), "ended": None, "error": (error or "")[:300], "expected": bool(expected), "notified": None,
               **{k: v for k, v in extra.items() if v}}
        with self._lock:
            self.incidents.append(inc)
            del self.incidents[:-KEEP]
            self._save()
        self._announce(inc, opened=True)

    def _close(self, key: str, *, note: str = "") -> None:
        with self._lock:
            inc = self._active(key)
            if not inc:
                return
            for i in self.incidents:
                if i["id"] == inc["id"]:
                    i["ended"] = round(self.clock(), 1)
                    if note:
                        i["note"] = note
                    inc = dict(i)
                    break
            self._save()
        self._bad_since.pop(key, None)
        if note != "gone":
            self._announce(inc, opened=False)

    def _save(self) -> None:
        try:
            write_json_atomic(self.path, {"incidents": self.incidents})
        except OSError as exc:
            log.warning("alerts: could not save: %s", exc)

    def _announce(self, inc: dict[str, Any], *, opened: bool) -> None:
        what = ("spark" if inc["kind"] == "spark" else "server") + ("_down" if opened else "_up")
        payload = {k: inc.get(k) for k in ("id", "kind", "target", "name", "started", "ended", "error", "expected", "node", "base_url")}
        if not opened:
            payload["down_s"] = round((inc["ended"] or self.clock()) - inc["started"])
        if self.emit:
            try:
                self.emit(f"alert.{what}", payload)
            except Exception:  # noqa: BLE001 - the bus is best effort
                pass
        if inc.get("expected") or not self.notifier or not self.options().get("notify", True):
            return
        if inc["kind"] == "spark":
            title = f"{inc['name']} no responde" if opened else f"{inc['name']} vuelve a responder"
        else:
            title = f"El modelo {inc['name']} no contesta" if opened else f"El modelo {inc['name']} vuelve a contestar"
        body = inc.get("error") or ""
        if not opened:
            mins = max(1, round(payload["down_s"] / 60))
            body = f"Caído {mins} min."
        try:
            res = self.notifier(title, body, priority="high" if opened else "normal", group="sparks-alert",
                                dedupe_key=f"alert:{inc['id']}:{'down' if opened else 'up'}")
            with self._lock:
                for i in self.incidents:
                    if i["id"] == inc["id"]:
                        i["notified"] = bool(isinstance(res, dict) and res.get("ok"))
                self._save()
        except Exception as exc:  # noqa: BLE001 - a notification must never stop the watch
            log.warning("alerts: notification failed: %s", exc)

    # ------------------------------------------------------------------ views
    def snapshot(self, *, hours: float = 168.0, node: str = "", limit: int = 100) -> dict[str, Any]:
        since = self.clock() - hours * 3600
        with self._lock:
            items = [dict(i) for i in self.incidents if (not i.get("ended") or i["ended"] >= since or i["started"] >= since)]
        if node:
            items = [i for i in items if i.get("node") == node or i.get("target") == node]
        items.sort(key=lambda i: (bool(i.get("ended")), -i["started"]))
        now = self.clock()
        for i in items:
            i["down_s"] = round((i.get("ended") or now) - i["started"])
        opts = self.options()
        return {"active": [i for i in items if not i.get("ended")], "incidents": items[:limit], "count": len(items),
                "enabled": bool(opts.get("enabled", True)), "after_s": opts.get("after_s"), "notify": bool(opts.get("notify", True)),
                "last_check": self.last_check or None}

    def summary(self) -> dict[str, Any]:
        act = self.active()
        return {"active": [{k: i.get(k) for k in ("id", "kind", "target", "name", "started", "error", "expected", "node")} for i in act]}
