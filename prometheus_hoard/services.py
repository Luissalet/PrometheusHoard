"""Everything the app does, wired once: settings, the cluster and its poller, files, models, jobs, recipes, power and live serving figures."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from .clients import Clients
from .cluster import Cluster, ssh_factory
from .config import Config, Settings, norm_ip
from .errors import SparkError
from .files import Files
from .hoard_link import family
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.tokens import read_or_create_token
from .jobs import Jobs
from .models import Models
from .power import Power
from .recipes import Recipes
from .serving import Serving
from .speedcard import SpeedCards
from .transport import run_script
from .xidwatch import XidWatch

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
        self.serving = Serving(self.recipes.endpoints, config.serving_path, **({"getter": self.world.http_text} if self.world is not None else {}))
        extra = {}
        if self.world is not None:
            extra = {"psutil_mod": self.world.psutil, "resolve": self.world.resolve, "route": lambda host: self.world.pc_address}
        self.clients = Clients(self.serving.infos, config.clients_path, runner=self._read_access_log, containers=self.recipes.log_containers,
                               names=lambda: self.settings["client_names"], spark_addrs=self.spark_addresses, node_name=self._node_name,
                               touched=lambda: self.serving.last_touch, **extra)
        self.speedcards = SpeedCards(self.recipes.endpoints, self.jobs, config.speedcards_path, get_json=self.recipes.http, get_text=self.serving.get,
                                     recipe=self.recipes.load, **({"transport": self.world.speed_transport()} if self.world is not None else {}))
        self.xid = XidWatch(self.cluster.enabled, config.xid_path, self._xid_options, notifier=None if self.world is not None else self._notify)
        self._bg: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.config.background:
            return
        self.cluster.start()
        self.serving.start()
        self.clients.start()
        self.xid.start()
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
        self.xid.stop()
        self.clients.stop()
        self.serving.stop()
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

    # ------------------------------------------------------------------ Xid watcher
    def _xid_options(self) -> dict[str, Any]:
        return {"enabled": self.settings["xid_watch"], "interval_s": self.settings["xid_interval_s"], "notify": self.settings["xid_notify"]}

    @staticmethod
    def _notify(title: str, body: str, **kwargs: Any) -> dict[str, Any]:
        """Through the family hub's notification facet (the vendored ``hoard_link``); it answers ``hub unreachable`` when no hub runs."""
        from .hoard_link import fam_notify

        return fam_notify.notify(title, body, **kwargs)

    # ------------------------------------------------------------------ clients of the model servers
    def _read_access_log(self, node_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """The new access lines of a server's container, read on its head Spark (the collector's thread; one short remote helper)."""
        node = self.cluster.node(node_id)
        out = run_script(node.transport, "accesslog", args, timeout=30.0)
        if node.conf.get("proxy_jump") and isinstance(out, dict):
            out["ssh_client"] = ""        # the Spark sees the jump host, not this PC
        return out

    def _node_name(self, node_id: str) -> str:
        node = self.cluster.nodes.get(node_id)
        return node.conf["name"] if node else node_id

    def spark_addresses(self) -> dict[str, str]:
        """address -> Spark id for every address the Sparks are known by (LAN, CX7 fabric, the host the settings give)."""
        out: dict[str, str] = {}
        for node in self.cluster.nodes.values():
            for iface in ((node.metrics or {}).get("net") or {}).values():
                for cidr in iface.get("v4") or []:
                    ip = norm_ip(str(cidr).split("/", 1)[0])
                    if ip:
                        out[ip] = node.id
            for host in (node.conf.get("api_host"), node.conf.get("host")):
                ip = norm_ip(host)
                if ip:
                    out[ip] = node.id
        return out

    def serving_snapshot(self, recipe: Optional[str] = None, *, series: bool = True, clients: bool = True) -> dict[str, Any]:
        """Live figures of the servers (``Serving``) and, per server, who is using it (``Clients``)."""
        out = self.serving.snapshot(recipe, series=series)
        if clients:
            views = self.clients.snapshot(recipe)
            for e in out["endpoints"]:
                e["clients"] = views.get(e["recipe"])
        return out

    def set_client_name(self, ip: str, name: str) -> dict[str, Any]:
        address = norm_ip(ip)
        if not address:
            raise SparkError("invalid", f"{ip!r} is not an IP address.", hint="Use the address the server's log shows, e.g. 192.0.2.7.")
        names = self.settings["client_names"]
        clean = " ".join(str(name or "").split())[:60]
        if clean:
            names[address] = clean
        else:
            names.pop(address, None)
        self.settings.update({"client_names": names})
        return self.clients.identity(address)

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
            for n in x["nodes"]:
                by_node.setdefault(n, []).append({"recipe": x["recipe"], "title": x["title"], "state": "running" if x["up"] else "starting",
                                                  "role": "head" if n == x["node"] else "worker", "served": x["models"], "base_url": x["base_url"],
                                                  "detected": True})
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
            "serving": self.serving.summary(),
            "xid": self.xid.summary(),
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
