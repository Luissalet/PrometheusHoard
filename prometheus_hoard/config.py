"""Process configuration (environment) and the editable settings (``<data>/settings.json``).

The cluster itself — which Sparks there are, how to reach them, where the models live and where the recipes are — is a setting, edited
from the UI or with ``cluster_set``; nothing about the machines is fixed in code. A first run with no settings proposes the three
``Spark1``..``Spark3`` SSH aliases that NVIDIA Sync writes, because that is what a fresh cluster has."""

from __future__ import annotations

import copy
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import APP_ID, DEFAULT_PORT
from .hoard_link.appconfig import AppPaths, env_flag, env_int, env_str
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent

NODE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def default_recipes_dir() -> str:
    """``PROMETHEUS_RECIPES_DIR``, else ``Sparks cluster/recipes`` next to this repository (the shared folder the family keeps), else
    ``<repo>/recipes``."""
    env = env_str("PROMETHEUS_RECIPES_DIR")
    if env:
        return env
    sibling = REPO_ROOT.parent / "Sparks cluster" / "recipes"
    if sibling.parent.is_dir():
        return str(sibling)
    return str(REPO_ROOT / "recipes")


def default_nodes() -> list[dict[str, Any]]:
    return [
        {"id": f"spark{i}", "name": f"Spark{i}", "ssh": f"Spark{i}", "enabled": True, "api_host": "", "color": "", "host": "", "proxy_jump": ""}
        for i in (1, 2, 3)
    ]


DEFAULT_SETTINGS: dict[str, Any] = {
    "nodes": [],                     # filled with default_nodes() on first read
    "recipes_dir": "",               # filled with default_recipes_dir()
    "models_dirs": ["~/models"],     # where models live on every Spark (the first one receives downloads)
    "hf_cache": True,                # also list the Hugging Face cache (~/.cache/huggingface/hub)
    "poll_s": 2.0,                   # live metrics refresh while someone looks
    "idle_poll_s": 15.0,             # refresh when nobody looked for a minute
    "history_min": 30,               # minutes of metrics kept in memory for the charts
    "show_system": False,            # the explorer starts at the home folder; true shows "/" too (read-only outside home)
    "trash_dir": "~/.prometheus-trash",
    "remote_dir": "~/sparks",        # where recipes and job logs are copied on each Spark
    "ssh_timeout_s": 8.0,
    "default_endpoint": "",          # recipe whose endpoint is advertised first to Faustus and the family
}


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    allowed_hosts: tuple[str, ...] = ()
    data_dir_configured: bool = False
    fake: bool = False              # demo/tests: an invented cluster in memory, no SSH
    background: bool = True         # the poller thread; tests switch it off

    @property
    def paths(self) -> AppPaths:
        return AppPaths(APP_ID, REPO_ROOT, self.data_dir, self.data_dir_configured)

    @property
    def token_path(self) -> Path:
        return self.paths.token_path

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def serving_path(self) -> Path:
        return self.data_dir / "serving.json"

    @classmethod
    def from_env(cls) -> "Config":
        data_env = env_str("PROMETHEUS_DATA_DIR")
        return cls(
            data_dir=Path(data_env).expanduser() if data_env else REPO_ROOT / "data",
            port=env_int("PROMETHEUS_PORT", default=DEFAULT_PORT) or DEFAULT_PORT,
            allowed_hosts=tuple(parse_allowed_hosts(os.environ.get("PROMETHEUS_ALLOWED_HOSTS", ""))),
            data_dir_configured=bool(data_env),
            fake=env_flag("PROMETHEUS_FAKE"),
        )


class Settings:
    """Thread-safe JSON settings with defaults; ``update`` validates and writes atomically."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        raw = read_json(self.path, default={}) or {}
        data = copy.deepcopy(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
        if not data.get("nodes"):
            data["nodes"] = default_nodes()
        if not data.get("recipes_dir"):
            data["recipes_dir"] = default_recipes_dir()
        data["nodes"] = [normalize_node(n) for n in data["nodes"]]
        return data

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data[key])

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = copy.deepcopy(self._data)
            for key, value in changes.items():
                if key not in DEFAULT_SETTINGS:
                    raise ValueError(f"Unknown setting: {key}")
                if value is None:
                    continue
                data[key] = value
            data["nodes"] = [normalize_node(n) for n in data["nodes"]]
            ids = [n["id"] for n in data["nodes"]]
            if len(set(ids)) != len(ids):
                raise ValueError("Two Sparks share the same id")
            if not data["models_dirs"] or not all(isinstance(d, str) and d.strip() for d in data["models_dirs"]):
                raise ValueError("models_dirs needs at least one folder")
            data["poll_s"] = min(max(float(data["poll_s"]), 1.0), 30.0)
            data["idle_poll_s"] = min(max(float(data["idle_poll_s"]), 5.0), 300.0)
            data["history_min"] = int(min(max(int(data["history_min"]), 5), 240))
            data["ssh_timeout_s"] = min(max(float(data["ssh_timeout_s"]), 2.0), 60.0)
            self._data = data
            write_json_atomic(self.path, data)
            return copy.deepcopy(data)


def normalize_node(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise ValueError("A node must be an object")
    node_id = str(node.get("id") or "").strip().lower()
    if not NODE_ID.match(node_id):
        raise ValueError(f"Invalid node id: {node_id!r} (lowercase letters, digits, - and _)")
    ssh = str(node.get("ssh") or "").strip()
    if not ssh:
        raise ValueError(f"Node {node_id} needs an SSH alias or host")
    return {
        "id": node_id,
        "name": str(node.get("name") or node_id).strip()[:40],
        "ssh": ssh[:200],
        "enabled": bool(node.get("enabled", True)),
        "api_host": str(node.get("api_host") or "").strip()[:200],   # host other programs use for its HTTP servers; empty = its hostname
        "host": str(node.get("host") or "").strip()[:200],           # SSH address override (the alias keeps user and key); empty = the alias
        "proxy_jump": str(node.get("proxy_jump") or "").strip()[:200],  # reach it through another host (SSH alias), e.g. over a CX7 cable
        "color": str(node.get("color") or "").strip()[:20],
    }
