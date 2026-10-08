"""Models on disk: the inventory of every Spark, downloads from Hugging Face and copies between Sparks.

Copies between Sparks go over a CX7 cable when the two have one (an address of the destination on a subnet the source shares), which is
200 Gb/s instead of the LAN; ``rsync`` resumes a copy that was interrupted."""

from __future__ import annotations

import posixpath
import re
import threading
import time
from typing import Any, Callable, Optional

from .cluster import Cluster, Node
from .errors import SparkError
from .jobs import Jobs, q
from .transport import run_script

REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
HF_BIN = "~/.venvs/hf/bin/hf"
HF_SETUP = ('test -x ~/.venvs/hf/bin/hf || { python3 -m venv ~/.venvs/hf && ~/.venvs/hf/bin/pip install -q -U "huggingface_hub[cli,hf_xet]"; }')


def slug(repo: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", repo.split("/")[-1].lower()).strip("-") or "model"


class Models:
    def __init__(self, cluster: Cluster, jobs: Jobs, *, hf_size: Optional[Callable[[str, str], Optional[int]]] = None,
                 secrets: Optional[Callable[[], dict[str, str]]] = None):
        self.cluster = cluster
        self.jobs = jobs
        self.hf_size = hf_size or hf_repo_size
        self.secrets = secrets or (lambda: {})
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def inventory(self, node_id: str = "", *, refresh: bool = False) -> dict[str, Any]:
        nodes = [self.cluster.node(node_id)] if node_id else self.cluster.enabled()
        out: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for node in nodes:
            try:
                out[node.id] = self.node_models(node, refresh=refresh)
            except SparkError as exc:
                errors[node.id] = exc.message
        return {"nodes": out, "errors": errors}

    def node_models(self, node: Node, *, refresh: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._cache.get(node.id)
        if cached and not refresh and time.time() - cached[0] < 60:
            return cached[1]
        data = run_script(node.transport, "models", {"dirs": self.cluster.settings["models_dirs"], "hf_cache": self.cluster.settings["hf_cache"]},
                          timeout=120)
        models = data.get("models", [])
        downloading = {j["watch"]: j for j in self.jobs.active("download") if j["node"] == node.id}
        for m in models:
            job = downloading.get(m["path"]) or next((j for w, j in downloading.items() if w.endswith("/" + m["name"])), None)
            m["downloading"] = job["id"] if job else ""
        with self._lock:
            self._cache[node.id] = (time.time(), models)
        return models

    def invalidate(self, node_id: str = "") -> None:
        with self._lock:
            if node_id:
                self._cache.pop(node_id, None)
            else:
                self._cache.clear()

    def find(self, node: Node, ref: str) -> dict[str, Any]:
        ref = ref.strip()
        for m in self.node_models(node):
            if ref in (m["path"], m["name"], m.get("repo")) or m["path"].endswith("/" + ref):
                return m
        raise SparkError("not_found", f"{node.conf['name']} has no model {ref!r}.", hint="models_list shows what each Spark has.")

    # ------------------------------------------------------------------ downloads
    def download(self, node_id: str, repo: str, *, name: str = "", revision: str = "", include: Optional[list[str]] = None,
                 folder: str = "") -> dict[str, Any]:
        node = self.cluster.node(node_id)
        repo = repo.strip().removeprefix("https://huggingface.co/").strip("/")
        if not REPO_ID.match(repo):
            raise SparkError("invalid", f"{repo!r} is not a Hugging Face repository id (owner/name).")
        base = folder or self.cluster.settings["models_dirs"][0]
        target = posixpath.join(base, name or slug(repo))
        for j in self.jobs.active("download"):
            if j["node"] == node.id and j["watch"] == target:
                raise SparkError("busy", f"{repo} is already downloading to {target} on {node.conf['name']}.", job=j["id"])
        expected = None
        try:
            expected = self.hf_size(repo, revision or "main")
        except Exception:  # noqa: BLE001 - the size only feeds the progress bar
            expected = None
        args = [HF_BIN, "download", q(repo), "--local-dir", q(target)]
        if revision:
            args += ["--revision", q(revision)]
        for pattern in include or []:
            args += ["--include", q(pattern)]
        marker = shq(f'{{"repo": "{repo}", "revision": "{revision or "main"}"}}')
        cmd = f"{HF_SETUP} && mkdir -p {q(target)} && {' '.join(args)} && printf '%s\\n' {marker} > {q(target)}/.prometheus.json"
        env = {}
        token = self.secrets().get("HF_TOKEN")
        if token:
            env["HF_TOKEN"] = token
        env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        job = self.jobs.start(node.id, cmd, kind="download", title=f"{repo} → {node.conf['name']}", watch=target, expected_bytes=expected,
                              meta={"repo": repo, "target": target, "revision": revision or "main"}, env=env)
        self.invalidate(node.id)
        return job

    # ------------------------------------------------------------------ copies between Sparks
    def copy(self, src_id: str, path: str, dst_ids: list[str], *, dest_folder: str = "") -> list[dict[str, Any]]:
        src = self.cluster.node(src_id)
        model = self.find(src, path) if not path.startswith(("/", "~")) else {"path": path, "name": posixpath.basename(path.rstrip("/")), "bytes": None}
        jobs = []
        for dst_id in dst_ids:
            dst = self.cluster.node(dst_id)
            if dst.id == src.id:
                continue
            ip = self.cluster.fabric_peer_ip(src, dst)
            host = ip or dst.api_host()
            user = dst.transport.target().get("user") or (dst.metrics or {}).get("user") or ""
            target_dir = dest_folder or posixpath.dirname(model["path"])
            if target_dir.startswith((src.metrics or {}).get("home", "/nonexistent")) and dst.metrics:
                target_dir = dst.metrics["home"] + target_dir[len(src.metrics["home"]):]
            remote = f"{user + '@' if user else ''}{host}"
            ssh_opts = "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
            cmd = (f"ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes {shq(remote)} mkdir -p {q(target_dir)} && "
                   f"rsync -a --partial --info=progress2 --exclude '.cache/huggingface/download' -e {shq(ssh_opts)} "
                   f"{q(model['path'].rstrip('/'))} {shq(remote + ':' + target_dir + '/')}")
            jobs.append(self.jobs.start(src.id, cmd, kind="copy", title=f"{model['name']}: {src.conf['name']} → {dst.conf['name']}"
                                        + (" (CX7)" if ip else " (LAN)"), expected_bytes=model.get("bytes"),
                                        meta={"src": src.id, "dst": dst.id, "path": model["path"], "via": "cx7" if ip else "lan", "host": host},
                                        watch=""))
        return jobs

    def delete(self, node_id: str, path: str, *, confirm: bool = False) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        model = self.find(node, path)
        if not confirm:
            raise SparkError("confirm_required", f"Deleting {model['name']} frees {round((model['bytes'] or 0) / 1e9, 1)} GB on {node.conf['name']} and cannot be undone.",
                             hint="Repeat with confirm=true if the user asked for it.")
        run_script(node.transport, "fsops", {"op": "rmtree", "paths": [model["path"]]}, timeout=600)
        self.invalidate(node.id)
        return {"node": node.id, "deleted": model["path"], "bytes": model["bytes"]}


def shq(value: str) -> str:
    import shlex

    return shlex.quote(value)


def hf_repo_size(repo: str, revision: str = "main") -> Optional[int]:
    """Total size of a repository's files from the Hugging Face API (None when it cannot be known)."""
    import httpx

    r = httpx.get(f"https://huggingface.co/api/models/{repo}/revision/{revision}", params={"blobs": "true"}, timeout=10.0, follow_redirects=True)
    if r.status_code != 200:
        return None
    total = 0
    for s in r.json().get("siblings", []):
        size = s.get("size") or (s.get("lfs") or {}).get("size")
        if isinstance(size, int):
            total += size
    return total or None
