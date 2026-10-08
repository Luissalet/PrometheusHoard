"""Loading and unloading models: recipes and deployments.

A recipe is a folder (in the recipes folder of the settings) with ``recipe.json`` and the scripts that start and stop one inference
server on one or more Sparks. Nothing about a model is fixed in this app: the recipe says which Sparks, which port and which model, and
its ``start.sh`` / ``stop.sh`` do the rest (usually ``docker run -d`` / ``docker rm -f``). Loading copies the folder to every Spark it
uses (``<remote_dir>/recipes/<name>``), runs ``start.sh`` on each in order (workers first, the head last) as detached jobs, and waits until
the head answers on its port. A recipe that is already answering (started by hand, or before this app was opened) is shown as running.

recipe.json::

    {"title": "...", "description": "...", "nodes": ["spark1", "spark2", "spark3"], "head": "spark1", "port": 8000,
     "served_model_name": "glm-5.3-flash", "model": "~/models/glm-5.3-flash-nvfp4", "max_model_len": 1048576,
     "engine": "vllm", "memory_gb": 110, "ready_timeout_s": 2400, "health_path": "/v1/models", "container": "glm53",
     "env": {"KEY": "value"}, "start_order": ["spark3", "spark2", "spark1"], "tags": ["1M"]}

Scripts receive ``PROM_RECIPE PROM_NODE PROM_ROLE (head|worker) PROM_RANK PROM_NODES PROM_HEAD PROM_HEAD_HOST PROM_HEAD_IP PROM_PORT
PROM_MODEL PROM_SERVED_NAME PROM_MAX_MODEL_LEN PROM_FABRIC_<NODE>`` (the address of each other node on a CX7 subnet shared with this one)
and the recipe ``env``."""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from .cluster import Cluster, Node
from .errors import SparkError
from .hoard_link.atomic import read_json, write_json_atomic
from .jobs import Jobs, q

log = logging.getLogger("prometheus.recipes")
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
STATES = ("stopped", "starting", "running", "stopping", "failed")


def http_get(url: str, timeout: float = 2.0) -> tuple[int, Any]:
    import httpx

    try:
        r = httpx.get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:500]


class Recipes:
    def __init__(self, cluster: Cluster, jobs: Jobs, state_path: Path, *, http: Callable[[str, float], tuple[int, Any]] = http_get,
                 on_event: Optional[Callable[[str, dict[str, Any]], None]] = None, sleep: Callable[[float], None] = time.sleep):
        self.cluster = cluster
        self.jobs = jobs
        self.state_path = state_path
        self.http = http
        self.on_event = on_event
        self.sleep = sleep
        self._lock = threading.RLock()
        data = read_json(state_path, default={}) or {}
        self.state: dict[str, dict[str, Any]] = data.get("deployments", {}) if isinstance(data.get("deployments"), dict) else {}
        self._threads: dict[str, threading.Thread] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="health")
        for name, st in self.state.items():  # a start or stop interrupted by a restart of this app is re-checked, not trusted
            if st.get("state") in ("starting", "stopping"):
                st["state"] = "unknown"

    # ------------------------------------------------------------------ recipes on disk
    @property
    def folder(self) -> Path:
        return Path(os.path.expanduser(self.cluster.settings["recipes_dir"]))

    def load(self, name: str) -> dict[str, Any]:
        if not NAME.match(name or ""):
            raise SparkError("invalid", f"{name!r} is not a recipe name.")
        folder = self.folder / name
        raw = read_json(folder / "recipe.json", default=None)
        if not isinstance(raw, dict):
            raise SparkError("not_found", f"There is no recipe {name!r} in {self.folder}.", hint="recipes_list shows them.")
        return self.normalize(name, raw, folder)

    def _node_id(self, ref: str) -> str:
        try:
            return self.cluster.node(str(ref)).id
        except SparkError:
            return str(ref).lower()

    def normalize(self, name: str, raw: dict[str, Any], folder: Path) -> dict[str, Any]:
        """Accepts this app's keys and the per-node lifecycle keys some recipes use (``head_node``, ``start_timeout``, ``endpoint``,
        ``scripts``, node names such as ``Spark1``): node references are matched against the Sparks of the settings by id, name or alias."""
        nodes = [self._node_id(n) for n in raw.get("nodes") or []]
        if not nodes:
            raise SparkError("invalid", f"Recipe {name}: «nodes» is empty.")
        head = self._node_id(raw.get("head") or raw.get("head_node") or nodes[0])
        if head not in nodes:
            raise SparkError("invalid", f"Recipe {name}: the head {head} is not one of its nodes.")
        order_raw = raw.get("start_order")
        if isinstance(order_raw, list) and order_raw:
            order = [self._node_id(n) for n in order_raw]
        elif order_raw == "workers_first":
            order = [n for n in nodes if n != head] + [head]
        else:  # head first: the head opens the rendezvous the workers join
            order = [head] + [n for n in nodes if n != head]
        scripts = raw.get("scripts") if isinstance(raw.get("scripts"), dict) else {}
        start_s, stop_s, health_s = scripts.get("start", "start.sh"), scripts.get("stop", "stop.sh"), scripts.get("health", "health.sh")
        files = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
        if start_s not in files or stop_s not in files:
            raise SparkError("invalid", f"Recipe {name} needs {start_s} and {stop_s} next to recipe.json.")
        endpoint = str(raw.get("endpoint") or "")
        port = int(raw.get("port") or 0) or 8000
        return {
            "name": name, "title": str(raw.get("title") or name), "description": str(raw.get("description") or ""),
            "nodes": nodes, "head": head, "start_order": order, "port": port, "endpoint": endpoint.rstrip("/"),
            "api_path": str(raw.get("api_path") or "/v1"), "health_path": str(raw.get("health_path") or "/v1/models"),
            "served_model_name": str(raw.get("served_model_name") or ""), "model": str(raw.get("model") or raw.get("model_dir") or ""),
            "max_model_len": raw.get("max_model_len"), "engine": str(raw.get("engine") or ("vllm" if raw.get("engine_repo") else "")),
            "memory_gb": raw.get("memory_gb"), "ready_timeout_s": int(raw.get("ready_timeout_s") or raw.get("start_timeout") or 1800),
            "container": str(raw.get("container") or ""), "env": {str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            "tags": list(raw.get("tags") or []), "files": files, "folder": str(folder), "notes": str(raw.get("notes") or ""),
            "measured": raw.get("measured") or {}, "scripts": {"start": start_s, "stop": stop_s, "health": health_s if health_s in files else ""},
            "context_verified": raw.get("context_verified"), "validation_status": raw.get("validation_status", ""),
        }

    def list(self) -> list[dict[str, Any]]:
        out = []
        if not self.folder.is_dir():
            return out
        for d in sorted(self.folder.iterdir()):
            if not (d / "recipe.json").is_file():
                continue
            try:
                r = self.load(d.name)
            except SparkError as exc:
                out.append({"name": d.name, "title": d.name, "invalid": exc.message})
                continue
            out.append(r)
        return out

    # ------------------------------------------------------------------ state
    def _save(self) -> None:
        write_json_atomic(self.state_path, {"deployments": self.state})

    def _set(self, name: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            st = self.state.setdefault(name, {"name": name, "state": "stopped"})
            before = st.get("state")
            st.update(changes)
            st["updated"] = time.time()
            self._save()
            if self.on_event and "state" in changes and changes["state"] != before:
                self.on_event(f"deploy.{changes['state']}", {"recipe": name, "nodes": st.get("nodes", []), "message": st.get("message", "")})
            return dict(st)

    def base_url(self, recipe: dict[str, Any]) -> str:
        if recipe.get("endpoint"):
            ep = recipe["endpoint"]
            return ep[: -len(recipe["api_path"])] if ep.endswith(recipe["api_path"]) else ep
        head = self.cluster.node(recipe["head"])
        return f"http://{head.api_host()}:{recipe['port']}"

    def probe_health(self, recipe: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
        try:
            base = self.base_url(recipe)
        except SparkError:
            return {"up": False, "error": "unknown head"}
        code, body = self.http(base + recipe["health_path"], timeout)
        models = []
        if code == 200 and isinstance(body, dict) and isinstance(body.get("data"), list):
            models = [{"id": m.get("id"), "max_model_len": m.get("max_model_len")} for m in body["data"] if isinstance(m, dict)]
        res = {"up": code == 200, "code": code, "models": models, "checked": time.time()}
        if code != 200:
            res["error"] = body if isinstance(body, str) else f"HTTP {code}"
        self._health[recipe["name"]] = res
        return res

    def deployments(self, *, check: bool = True) -> list[dict[str, Any]]:
        recipes = [r for r in self.list() if "invalid" not in r]
        if check:
            list(self._pool.map(lambda r: self.probe_health(r, 1.5), recipes))
        out = []
        for r in recipes:
            st = dict(self.state.get(r["name"], {"state": "stopped"}))
            health = self._health.get(r["name"], {})
            state = st.get("state", "stopped")
            if health.get("up") and state in ("stopped", "unknown", "failed"):
                state = "running"
                st["external"] = True
            elif state == "running" and health and not health.get("up"):
                state = "unknown"
            elif state == "unknown" and health and not health.get("up"):
                state = "stopped"
            st["state"] = state
            out.append(self._view(r, st, health))
        return out

    def _view(self, r: dict[str, Any], st: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
        try:
            base = self.base_url(r)
        except SparkError:
            base = ""
        return {"recipe": r["name"], "title": r["title"], "nodes": r["nodes"], "head": r["head"], "state": st.get("state", "stopped"),
                "external": bool(st.get("external")), "message": st.get("message", ""), "step": st.get("step", ""),
                "started": st.get("started"), "ready_at": st.get("ready_at"), "jobs": st.get("jobs", []),
                "base_url": base + r["api_path"] if base else "", "port": r["port"], "served_model_name": r["served_model_name"],
                "model": r["model"], "max_model_len": r["max_model_len"], "engine": r["engine"], "memory_gb": r["memory_gb"],
                "context_verified": r.get("context_verified"), "validation_status": r.get("validation_status", ""), "health": health, "served": [m["id"] for m in health.get("models", [])] if health else [], "tags": r["tags"]}

    def get(self, name: str) -> dict[str, Any]:
        r = self.load(name)
        health = self.probe_health(r)
        return {**self._view(r, dict(self.state.get(name, {"state": "stopped"})), health), "recipe_def": r}

    def busy_nodes(self, exclude: str = "") -> dict[str, list[str]]:
        """node id -> deployments that use it and are running or starting."""
        out: dict[str, list[str]] = {}
        for d in self.deployments(check=True):
            if d["recipe"] == exclude or d["state"] not in ("running", "starting"):
                continue
            for n in d["nodes"]:
                out.setdefault(n, []).append(d["recipe"])
        return out

    # ------------------------------------------------------------------ start / stop
    def start(self, name: str, *, stop_conflicts: bool = False, wait: bool = False) -> dict[str, Any]:
        r = self.load(name)
        with self._lock:
            if name in self._threads and self._threads[name].is_alive():
                raise SparkError("busy", f"{r['title']} is already being started or stopped.")
        nodes = [self.cluster.node(n) for n in r["nodes"]]
        offline = [n.conf["name"] for n in nodes if not n.online]
        if offline:
            raise SparkError("unreachable", f"{', '.join(offline)} not online: {r['title']} cannot start.",
                             hint="Turn them on (power_wake) or wait for them to come back.")
        health = self.probe_health(r)
        if health.get("up"):
            self._set(name, state="running", nodes=r["nodes"], external=True, message="Ya estaba en marcha.")
            return self.get(name)
        busy = self.busy_nodes(exclude=name)
        conflicts = sorted({d for n in r["nodes"] for d in busy.get(n, [])})
        if conflicts and not stop_conflicts:
            raise SparkError("conflict", f"{r['title']} needs {', '.join(r['nodes'])}, where {', '.join(conflicts)} is running.",
                             hint="Unload it first, or repeat with stop_conflicts=true to unload it and continue.", conflicts=conflicts)
        if r["memory_gb"]:
            short = []
            for n in nodes:
                avail = (n.metrics or {}).get("memory", {}).get("available") or 0
                if conflicts:
                    continue  # memory will be freed by the conflicts that are stopped first
                if avail and avail < float(r["memory_gb"]) * 1e9:
                    short.append(f"{n.conf['name']} ({avail / 1e9:.0f} GB libres)")
            if short and not stop_conflicts:
                raise SparkError("conflict", f"{r['title']} needs about {r['memory_gb']} GB per Spark; not enough free memory on {', '.join(short)}.",
                                 hint="Free memory (unload another model) or repeat with stop_conflicts=true to try anyway.")
        self._set(name, state="starting", nodes=r["nodes"], started=time.time(), ready_at=None, step="copy", message="Copiando la receta",
                  external=False, jobs=[])
        thread = threading.Thread(target=self._start_worker, args=(r, conflicts), name=f"start-{name}", daemon=True)
        with self._lock:
            self._threads[name] = thread
        thread.start()
        if wait:
            thread.join(timeout=r["ready_timeout_s"] + 600)
        return self.get(name)

    def _env(self, r: dict[str, Any], node: Node, rank: int) -> dict[str, str]:
        head = self.cluster.node(r["head"])
        env = {
            "PROM_RECIPE": r["name"], "PROM_NODE": node.id, "SPARK_NODE": node.conf["name"], "PROM_ROLE": "head" if node.id == r["head"] else "worker",
            "PROM_RANK": str(rank), "PROM_NODES": ",".join(r["nodes"]), "PROM_NNODES": str(len(r["nodes"])), "PROM_HEAD": r["head"],
            "PROM_HEAD_HOST": head.api_host(), "PROM_HEAD_IP": (self.cluster.fabric_peer_ip(node, head) or "") if node.id != head.id else "",
            "PROM_PORT": str(r["port"]), "PROM_MODEL": r["model"], "PROM_SERVED_NAME": r["served_model_name"],
            "PROM_MAX_MODEL_LEN": str(r["max_model_len"] or ""),
        }
        for other_id in r["nodes"]:
            if other_id == node.id:
                continue
            ip = self.cluster.fabric_peer_ip(node, self.cluster.node(other_id)) or ""
            env[f"PROM_FABRIC_{other_id.upper().replace('-', '_')}"] = ip
        if node.id == head.id:  # the head's own fabric addresses, one per peer
            for other_id in r["nodes"]:
                if other_id != node.id:
                    ip = self.cluster.fabric_peer_ip(self.cluster.node(other_id), node) or ""
                    env[f"PROM_SELF_IP_TO_{other_id.upper().replace('-', '_')}"] = ip
        env.update(r["env"])
        return env

    def _upload(self, r: dict[str, Any], node: Node) -> str:
        files = Path(r["folder"])
        with node.transport.sftp_lock:
            sftp = node.transport.sftp()
            home = (node.metrics or {}).get("home") or sftp.normalize(".")
            remote = posixpath.join(home, self.cluster.settings["remote_dir"].removeprefix("~/").removeprefix("~"), "recipes", r["name"])
            acc = ""
            for part in remote.split("/")[1:]:
                acc += "/" + part
                try:
                    sftp.stat(acc)
                except FileNotFoundError:
                    sftp.mkdir(acc)
            for p in files.iterdir():
                if p.is_file():
                    data = p.read_bytes()
                    if p.suffix == ".sh":
                        data = data.replace(b"\r\n", b"\n")
                    with sftp.open(posixpath.join(remote, p.name), "wb") as fh:
                        fh.write(data)
                    if p.suffix == ".sh":
                        sftp.chmod(posixpath.join(remote, p.name), 0o755)
        return remote

    def _run_step(self, r: dict[str, Any], node: Node, script: str, remote: str, rank: int, *, timeout: float) -> dict[str, Any]:
        env = self._env(r, node, rank)
        job = self.jobs.start(node.id, f"cd {q(remote)} && bash {script}", kind="recipe", title=f"{r['title']}: {script} en {node.conf['name']}",
                              meta={"recipe": r["name"], "script": script}, env=env)
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.jobs.get(job["id"])
            if j["state"] in ("done", "failed", "cancelled", "lost"):
                return j
            self.sleep(2.0)
        self.jobs.cancel(job["id"])
        return self.jobs.get(job["id"])

    def _start_worker(self, r: dict[str, Any], conflicts: list[str]) -> None:
        name = r["name"]
        try:
            for other in conflicts:
                self._set(name, step="unload", message=f"Descargando {other}")
                self.stop(other, wait=True)
            remotes = {}
            for node_id in r["nodes"]:
                self._set(name, step="copy", message=f"Copiando la receta a {node_id}")
                remotes[node_id] = self._upload(r, self.cluster.node(node_id))
            jobs = []
            for node_id in r["start_order"]:
                node = self.cluster.node(node_id)
                self._set(name, step="start", message=f"Arrancando en {node.conf['name']}")
                j = self._run_step(r, node, r["scripts"]["start"], remotes[node_id], r["nodes"].index(node_id), timeout=1800)
                jobs.append(j["id"])
                self._set(name, jobs=jobs)
                if j["state"] != "done":
                    raise SparkError("remote_failed", f"{r['scripts']['start']} failed on {node.conf['name']} (exit {j.get('rc')}).", hint=(j.get("log") or "")[-600:])
            self._set(name, step="wait", message="Esperando a que el servidor responda (carga de pesos y compilación)")
            deadline = time.time() + r["ready_timeout_s"]
            while time.time() < deadline:
                if self.state.get(name, {}).get("state") != "starting":
                    return  # stopped meanwhile
                health = self.probe_health(r, 3.0)
                if health.get("up"):
                    self._set(name, state="running", step="", ready_at=time.time(), message="Listo")
                    return
                self.sleep(5.0)
            raise SparkError("timeout", f"{r['title']} did not answer on port {r['port']} within {r['ready_timeout_s']} s.",
                             hint="Look at the logs (deploy_logs) on the head.")
        except SparkError as exc:
            self._set(name, state="failed", step="", message=exc.message + (f" — {exc.hint}" if exc.hint else ""))
        except Exception as exc:  # noqa: BLE001 - the worker reports, never dies silently
            log.exception("start %s", name)
            self._set(name, state="failed", step="", message=str(exc))

    def stop(self, name: str, *, wait: bool = True) -> dict[str, Any]:
        r = self.load(name)
        self._set(name, state="stopping", step="stop", message="Parando")

        def work() -> None:
            errors = []
            for node_id in reversed(r["start_order"]):
                try:
                    node = self.cluster.node(node_id)
                    remote = self._upload(r, node)
                    j = self._run_step(r, node, r["scripts"]["stop"], remote, r["nodes"].index(node_id), timeout=300)
                    if j["state"] != "done":
                        errors.append(f"{node.conf['name']}: exit {j.get('rc')}")
                except SparkError as exc:
                    errors.append(f"{node_id}: {exc.message}")
            health = self.probe_health(r, 2.0)
            if health.get("up"):
                self._set(name, state="failed", step="", message="Sigue respondiendo tras stop.sh. " + "; ".join(errors))
            else:
                self._set(name, state="stopped", step="", message="; ".join(errors) if errors else "Descargado", external=False, ready_at=None)

        if wait:
            work()
        else:
            t = threading.Thread(target=work, name=f"stop-{name}", daemon=True)
            with self._lock:
                self._threads[name] = t
            t.start()
        return self.get(name)

    def logs(self, name: str, node_id: str = "", lines: int = 200) -> dict[str, Any]:
        r = self.load(name)
        node = self.cluster.node(node_id or r["head"])
        out: dict[str, Any] = {"recipe": name, "node": node.id}
        if r["scripts"].get("health"):
            res = node.transport.run(f"cd {q(self.cluster.settings['remote_dir'])}/recipes/{name} && SPARK_NODE={q(node.conf['name'])} bash {r['scripts']['health']} 2>&1 | tail -c 20000",
                                     timeout=60)
            out["health"] = res.out
        if r["container"]:
            res = node.transport.run(f"docker ps -a --filter name={q(r['container'])} --format '{{{{.Names}}}}' | head -1 | xargs -r docker logs --tail {int(lines)} 2>&1",
                                     timeout=30)
            out["container"] = res.out[-60000:]
        st = self.state.get(name, {})
        job_logs = []
        for job_id in st.get("jobs", []):
            try:
                j = self.jobs.get(job_id)
                if j["node"] == node.id:
                    job_logs.append({"id": j["id"], "title": j["title"], "state": j["state"], "log": j["log"]})
            except SparkError:
                pass
        out["jobs"] = job_logs
        if (Path(r["folder"]) / "logs.sh").is_file():
            res = node.transport.run(f"cd {q(self.cluster.settings['remote_dir'])}/recipes/{name} && PROM_LINES={int(lines)} bash logs.sh 2>&1 | tail -n {int(lines)}", timeout=30)
            out["script"] = res.out[-60000:]
        return out

    def detected(self, deployments: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        """Inference servers running on a Spark that no recipe accounts for (started by hand or by another tool): each one that
        answers on its port is offered as an endpoint too, named after the Spark and the port."""
        deployments = deployments if deployments is not None else self.deployments(check=False)
        taken = set()
        for d in deployments:
            if d["state"] in ("running", "starting"):
                taken.add((d["head"], d["port"]))
                for n in d["nodes"]:
                    taken.add((n, d["port"]))
        found = []
        for node in self.cluster.enabled():
            for srv in (node.metrics or {}).get("servers", []):
                port = srv.get("port")
                if not port or (node.id, port) in taken or srv.get("engine") not in ("vllm", "sglang", "llama-server", "trtllm"):
                    continue
                base = f"http://{node.api_host()}:{port}"
                key = f"{node.id}-{port}"
                if key not in self._health or time.time() - self._health[key].get("checked", 0) > 10:
                    code, body = self.http(base + "/v1/models", 1.5)
                    models = [m.get("id") for m in body.get("data", []) if isinstance(m, dict)] if code == 200 and isinstance(body, dict) else []
                    self._health[key] = {"up": code == 200, "models": models, "checked": time.time()}
                h = self._health[key]
                found.append({"recipe": key, "title": f"{srv.get('served_name') or srv.get('model') or srv.get('engine')} ({node.conf['name']}:{port})",
                              "node": node.id, "engine": srv.get("engine"), "port": port, "base_url": base + "/v1", "up": h["up"],
                              "models": h["models"] or ([srv["served_name"]] if srv.get("served_name") else []),
                              "max_model_len": srv.get("max_len"), "tp": srv.get("tp"), "pid": srv.get("pid")})
        return found

    def endpoints(self) -> list[dict[str, Any]]:
        out = []
        deps = self.deployments(check=True)
        for x in self.detected(deps):
            if x["up"]:
                out.append({"recipe": x["recipe"], "title": x["title"], "base_url": x["base_url"], "models": x["models"],
                            "max_model_len": x["max_model_len"], "nodes": [x["node"]], "head": x["node"], "engine": x["engine"],
                            "default": False, "detected": True})
        for d in deps:
            if d["state"] != "running":
                continue
            out.append({"recipe": d["recipe"], "title": d["title"], "base_url": d["base_url"], "models": d["served"] or [d["served_model_name"]],
                        "max_model_len": d["max_model_len"], "nodes": d["nodes"], "head": d["head"], "engine": d["engine"],
                        "default": d["recipe"] == self.cluster.settings["default_endpoint"], "detected": False})
        out.sort(key=lambda e: (not e["default"], e["detected"], e["recipe"]))
        return out

    def write_recipe(self, name: str, recipe: dict[str, Any], scripts: dict[str, str], *, overwrite: bool = False) -> dict[str, Any]:
        """Create a recipe folder from the UI or an assistant (recipe.json + scripts)."""
        if not NAME.match(name or ""):
            raise SparkError("invalid", "Recipe names use lowercase letters, digits, «.», «-» and «_».")
        folder = self.folder / name
        if folder.exists() and not overwrite:
            raise SparkError("conflict", f"Recipe {name} already exists.", hint="Pass overwrite=true to replace it.")
        if "start.sh" not in scripts or "stop.sh" not in scripts:
            raise SparkError("invalid", "A recipe needs start.sh and stop.sh.")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "recipe.json").write_text(json.dumps(recipe, indent=2, ensure_ascii=False), encoding="utf-8")
        for fname, text in scripts.items():
            if "/" in fname or "\\" in fname or not re.match(r"^[A-Za-z0-9._-]+$", fname):
                raise SparkError("invalid", f"Bad script name {fname!r}.")
            (folder / fname).write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
        return self.load(name)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
