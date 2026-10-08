"""Everything the app does, wired once: settings, the cluster and its poller, files, models, jobs, recipes and power."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from .cluster import Cluster, ssh_factory
from .config import Config, Settings
from .files import Files
from .hoard_link import family
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.tokens import read_or_create_token
from .jobs import Jobs
from .models import Models
from .power import Power
from .recipes import Recipes

log = logging.getLogger("prometheus")


class Services:
    def __init__(self, config: Config, *, world: Any = None, hf_size: Any = None):
        self.config = config
        config.paths.ensure()
        self.token = read_or_create_token(config.token_path)
        self.settings = Settings(config.settings_path)
        self.world = world
        factory = ssh_factory
        http = None
        if config.fake or world is not None:
            from .fake import FakeWorld, demo_recipes, fake_factory

            self.world = world or FakeWorld(config.data_dir / "demo-cluster")
            factory = fake_factory(self.world)
            demo_dir = config.data_dir / "demo-recipes"
            demo_recipes(demo_dir)
            if self.settings["recipes_dir"] != str(demo_dir):
                self.settings.update({"recipes_dir": str(demo_dir)})
            http = self.world.http
            hf_size = hf_size or (lambda repo, rev: 6144)
        self.events: list[dict[str, Any]] = []
        self._events_lock = threading.Lock()
        self.cluster = Cluster(self.settings, factory, on_change=lambda kind, data: self.emit(f"node.{kind}", data))
        self.jobs = Jobs(self.cluster, config.data_dir / "jobs.json", on_event=self.emit)
        self.files = Files(self.cluster)
        self.models = Models(self.cluster, self.jobs, hf_size=hf_size, secrets=self.secrets)
        kwargs = {"http": http} if http else {}
        self.recipes = Recipes(self.cluster, self.jobs, config.state_path, on_event=self.emit, **kwargs)
        if self.world is not None:
            self.world.recipe_source = self.recipes.list
            self.recipes.sleep = lambda s: time.sleep(min(s, 0.2))
        self.power = Power(self.cluster, self.recipes, config.data_dir / "macs.json", on_event=self.emit,
                           **({"wol": lambda mac, b: self.world.wol.append(mac)} if self.world is not None else {}))
        self._bg: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.config.background:
            return
        self.cluster.start()
        self._stop.clear()
        self._bg = threading.Thread(target=self._housekeeping, name="prometheus-housekeeping", daemon=True)
        self._bg.start()

    def _housekeeping(self) -> None:
        while not self._stop.wait(5.0):
            try:
                if self.jobs.active():
                    self.jobs.refresh()
                self.power.remember()
            except Exception:  # noqa: BLE001
                log.exception("housekeeping")

    def stop(self) -> None:
        self._stop.set()
        self.cluster.stop()
        self.recipes.close()

    # ------------------------------------------------------------------ events and secrets
    def emit(self, kind: str, data: dict[str, Any]) -> None:
        item = {"t": time.time(), "type": kind, **data}
        with self._events_lock:
            self.events.append(item)
            del self.events[:-200]
        if self.world is None:
            try:
                family.emit(f"prometheus.{kind}", data)
            except Exception:  # noqa: BLE001 - the hub may be closed
                pass

    def recent_events(self, after: float = 0.0) -> list[dict[str, Any]]:
        with self._events_lock:
            return [e for e in self.events if e["t"] > after]

    @property
    def secrets_path(self):
        return self.config.data_dir / "secrets.json"

    def secrets(self) -> dict[str, str]:
        return read_json(self.secrets_path, default={}) or {}

    def set_secret(self, key: str, value: str) -> None:
        data = self.secrets()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        write_json_atomic(self.secrets_path, data)

    # ------------------------------------------------------------------ views
    def overview(self, *, detail: bool = False) -> dict[str, Any]:
        self.cluster.touch()
        nodes = [self.cluster.node_view(n, detail=detail) for n in self.cluster.nodes.values()]
        deps = self.recipes.deployments(check=True)
        by_node: dict[str, list[dict[str, Any]]] = {}
        for d in deps:
            if d["state"] in ("running", "starting", "stopping", "failed"):
                for n in d["nodes"]:
                    by_node.setdefault(n, []).append({"recipe": d["recipe"], "title": d["title"], "state": d["state"], "role": "head" if n == d["head"] else "worker",
                                                      "served": d["served"] or [d["served_model_name"]], "base_url": d["base_url"]})
        detected = self.recipes.detected(deps)
        for x in detected:
            by_node.setdefault(x["node"], []).append({"recipe": x["recipe"], "title": x["title"], "state": "running" if x["up"] else "starting",
                                                      "role": "head", "served": x["models"], "base_url": x["base_url"], "detected": True})
        for n in nodes:
            n["deployments"] = by_node.get(n["id"], [])
        online = [n for n in nodes if n["online"]]
        mem_total = sum(n["memory"]["total"] for n in online)
        mem_used = sum(n["memory"]["used"] for n in online)
        return {
            "nodes": nodes,
            "cluster": {"online": len(online), "total": len(nodes), "memory_total": mem_total, "memory_used": mem_used,
                        "gpu_util": round(sum((n["gpu"]["util"] or 0) for n in online) / len(online), 1) if online else None,
                        "power_w": round(sum((n["gpu"]["power_w"] or 0) for n in online), 1) if online else None},
            "deployments": deps,
            "detected": detected,
            "jobs": self.jobs.list(state="active", limit=20),
            "events": self.recent_events(time.time() - 600)[-20:],
            "demo": self.world is not None,
        }

    def history(self, node_id: str, minutes: int = 10) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        since = time.time() - 60 * minutes
        return {"node": node.id, "samples": [s for s in node.history if s["t"] >= since]}

    def health(self) -> dict[str, Any]:
        return {"nodes": {n.id: n.online for n in self.cluster.nodes.values()}, "demo": self.world is not None,
                "running": [d["recipe"] for d in self.recipes.deployments(check=False) if d["state"] == "running"]}
