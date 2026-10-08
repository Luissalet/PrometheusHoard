"""Background work on the Sparks — model downloads, copies between Sparks over the CX7 cables, recipe steps — tracked from here.

A job runs detached on a Spark (``remote/jobs.py``), so it survives this app being closed; its record (``<data>/jobs.json``) keeps what the
UI needs to show progress: the expected size of a download and the folder that grows while it runs."""

from __future__ import annotations

import shlex
import threading
import time
from typing import Any, Callable, Optional

from .cluster import Cluster
from .errors import SparkError
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.ids import new_id
from .transport import run_script

FINAL = ("done", "failed", "cancelled", "lost")


class Jobs:
    def __init__(self, cluster: Cluster, path, *, on_event: Optional[Callable[[str, dict[str, Any]], None]] = None):
        self.cluster = cluster
        self.path = path
        self.on_event = on_event
        self._lock = threading.RLock()
        data = read_json(path, default={}) or {}
        self.jobs: dict[str, dict[str, Any]] = {j["id"]: j for j in data.get("jobs", []) if isinstance(j, dict) and j.get("id")}

    def _save(self) -> None:
        recent = sorted(self.jobs.values(), key=lambda j: j.get("created", 0), reverse=True)[:300]
        write_json_atomic(self.path, {"jobs": recent})

    def start(self, node_id: str, cmd: str, *, kind: str, title: str, watch: str = "", expected_bytes: Optional[int] = None,
              meta: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        job_id = new_id("job")
        remote = run_script(node.transport, "jobs", {"op": "start", "id": job_id, "cmd": cmd, "watch": watch, "env": env or {},
                                                      "remote_dir": self.cluster.settings["remote_dir"]}, timeout=30)
        job = {"id": job_id, "node": node.id, "kind": kind, "title": title, "cmd": cmd, "watch": watch, "expected_bytes": expected_bytes,
               "meta": meta or {}, "created": time.time(), "state": remote.get("state", "running"), "rc": remote.get("rc"),
               "log": remote.get("log", ""), "watched_bytes": remote.get("watched_bytes"), "updated": time.time()}
        with self._lock:
            self.jobs[job_id] = job
            self._save()
        if self.on_event:
            self.on_event("job.started", {"id": job_id, "node": node.id, "kind": kind, "title": title})
        return self.view(job)

    def refresh(self, ids: Optional[list[str]] = None) -> None:
        with self._lock:
            live = [j for j in self.jobs.values() if j["state"] not in FINAL and (not ids or j["id"] in ids)]
        by_node: dict[str, list[dict[str, Any]]] = {}
        for j in live:
            by_node.setdefault(j["node"], []).append(j)
        for node_id, items in by_node.items():
            try:
                node = self.cluster.node(node_id)
                if not node.online and node.error:
                    continue
                out = run_script(node.transport, "jobs", {"op": "status", "ids": [j["id"] for j in items], "tail": 3000,
                                                          "remote_dir": self.cluster.settings["remote_dir"]}, timeout=60)
            except SparkError:
                continue
            for remote in out.get("jobs", []):
                with self._lock:
                    job = self.jobs.get(remote["id"])
                    if not job:
                        continue
                    before = job["state"]
                    if remote["state"] == "missing":
                        job["state"] = "lost"
                    else:
                        job.update({"state": remote["state"], "rc": remote.get("rc"), "log": remote.get("log", ""),
                                    "watched_bytes": remote.get("watched_bytes")})
                    job["updated"] = time.time()
                    if before != job["state"] and job["state"] in FINAL:
                        job["finished"] = time.time()
                        if self.on_event:
                            self.on_event(f"job.{job['state']}", {"id": job["id"], "node": job["node"], "kind": job["kind"], "title": job["title"]})
            with self._lock:
                self._save()

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get_raw(job_id)
        if job["state"] in FINAL:
            return self.view(job)
        node = self.cluster.node(job["node"])
        run_script(node.transport, "jobs", {"op": "kill", "ids": [job_id], "remote_dir": self.cluster.settings["remote_dir"]}, timeout=30)
        with self._lock:
            job["state"] = "cancelled"
            job["finished"] = time.time()
            self._save()
        return self.view(job)

    def get_raw(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.jobs.get(job_id)
        if not job:
            raise SparkError("not_found", f"No job {job_id}.")
        return job

    def get(self, job_id: str, *, refresh: bool = True) -> dict[str, Any]:
        if refresh:
            self.refresh([job_id])
        return self.view(self.get_raw(job_id))

    def list(self, *, state: str = "", node: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self.jobs.values(), key=lambda j: j.get("created", 0), reverse=True)
        if state == "active":
            items = [j for j in items if j["state"] not in FINAL]
        elif state:
            items = [j for j in items if j["state"] == state]
        if node:
            items = [j for j in items if j["node"] == node]
        return [self.view(j, log_lines=6) for j in items[:limit]]

    def forget(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job and job["state"] not in FINAL:
                raise SparkError("busy", "That job is still running: cancel it first.")
            self.jobs.pop(job_id, None)
            self._save()

    def active(self, kind: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in self.jobs.values() if j["state"] not in FINAL and (not kind or j["kind"] == kind)]

    @staticmethod
    def view(job: dict[str, Any], *, log_lines: int = 40) -> dict[str, Any]:
        expected = job.get("expected_bytes")
        got = job.get("watched_bytes")
        progress = None
        if expected and got is not None:
            progress = min(1.0, got / expected) if job["state"] not in ("done",) else 1.0
        elif job["state"] == "done":
            progress = 1.0
        lines = [ln for ln in (job.get("log") or "").splitlines() if ln.strip()]
        return {k: job.get(k) for k in ("id", "node", "kind", "title", "state", "rc", "created", "finished", "expected_bytes", "watched_bytes", "meta", "watch")} | {
            "progress": progress, "log": "\n".join(lines[-log_lines:]), "elapsed_s": round((job.get("finished") or time.time()) - job["created"], 1)}


def q(value: str) -> str:
    """Quote for the remote bash, keeping a leading ``~/`` expandable."""
    if value.startswith("~/"):
        return "~/" + shlex.quote(value[2:])
    return shlex.quote(value)
