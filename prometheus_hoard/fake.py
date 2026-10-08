"""An invented cluster for the demo (``PROMETHEUS_FAKE=1``) and the tests: three Sparks that live in a temporary folder.

Files are real files under ``<root>/<node>/`` shown to the app as ``/home/demo/...``; the helper scripts that only read files
(``models``, ``fsops``) really run, with paths translated; probes, jobs, docker and power commands are simulated. Nothing here touches
the network or another computer."""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .transport import RunResult, remote_script

HOME = "/home/demo"


class LocalSFTP:
    """The subset of paramiko's SFTPClient the app uses, over a local folder that plays ``/``."""

    def __init__(self, root: Path):
        self.root = root

    def _p(self, path: str) -> Path:
        path = path if path.startswith("/") else HOME + "/" + path
        parts = [p for p in path.split("/") if p not in ("", ".")]
        out: list[str] = []
        for p in parts:
            if p == "..":
                if out:
                    out.pop()
            else:
                out.append(p)
        return self.root.joinpath(*out) if out else self.root

    def normalize(self, path: str) -> str:
        return HOME if path in (".", "") else path

    def _attr(self, p: Path, name: str, follow: bool = True):
        import paramiko

        st = p.stat() if follow else p.lstat()
        a = paramiko.SFTPAttributes.from_stat(st, name)
        return a

    def listdir_attr(self, path: str):
        p = self._p(path)
        if not p.exists():
            raise FileNotFoundError(path)
        if not p.is_dir():
            raise OSError(f"{path} is not a folder")
        return [self._attr(c, c.name, follow=False) for c in sorted(p.iterdir())]

    def listdir(self, path: str):
        p = self._p(path)
        if not p.exists():
            raise FileNotFoundError(path)
        return sorted(c.name for c in p.iterdir())

    def stat(self, path: str):
        p = self._p(path)
        if not p.exists():
            raise FileNotFoundError(path)
        return self._attr(p, p.name)

    def lstat(self, path: str):
        p = self._p(path)
        if not p.exists() and not p.is_symlink():
            raise FileNotFoundError(path)
        return self._attr(p, p.name, follow=False)

    def open(self, path: str, mode: str = "rb"):
        p = self._p(path)
        if "r" in mode and not p.exists():
            raise FileNotFoundError(path)
        return open(p, mode)

    def mkdir(self, path: str, mode: int = 0o755):
        self._p(path).mkdir()

    def rename(self, src: str, dst: str):
        self._p(src).rename(self._p(dst))

    posix_rename = rename

    def chmod(self, path: str, mode: int):
        os.chmod(self._p(path), mode)

    def remove(self, path: str):
        self._p(path).unlink()


class FakeWorld:
    """State shared by the fake nodes: running jobs, servers that answer, a clock."""

    def __init__(self, root: Optional[Path] = None, *, job_s: float = 2.0):
        self.root = root or Path(tempfile.mkdtemp(prefix="prometheus-demo-"))
        self.job_s = job_s
        self.jobs: dict[str, dict[str, Any]] = {}
        self.serving: dict[str, set[str]] = {}       # recipe -> nodes where start.sh ran
        self.lock = threading.RLock()
        self.commands: list[tuple[str, str]] = []    # (node, command) for the tests
        self.wol: list[str] = []
        self.power: dict[str, str] = {}              # node -> "off" | "asleep"
        self.started = time.time()

    recipe_source = None   # set by the app: a callable returning the recipes (name, port, nodes, served_model_name, max_model_len)

    def recipes_meta(self) -> dict[str, dict[str, Any]]:
        out = {}
        for r in (self.recipe_source() if self.recipe_source else []):
            if "invalid" not in r:
                out[r["name"]] = {"port": r["port"], "nodes": r["nodes"], "served": r["served_model_name"], "max_len": r["max_model_len"]}
        return out

    def http(self, url: str, timeout: float = 2.0) -> tuple[int, Any]:
        """http://<host>:<port>/... — the recipe on that port answers when its start.sh ran on all its nodes."""
        meta = self.recipes_meta()
        with self.lock:
            for recipe, nodes in self.serving.items():
                m = meta.get(recipe)
                if m and f":{m['port']}/" in url and set(m["nodes"]) <= nodes:
                    return 200, {"object": "list", "data": [{"id": m["served"], "max_model_len": m["max_len"]}]}
        return 0, "connection refused"


def seed(world: FakeWorld, node_id: str) -> Path:
    base = world.root / node_id
    home = base / HOME.strip("/")
    if home.exists():
        return base
    (home / "models").mkdir(parents=True)
    (home / "sparks" / "recipes").mkdir(parents=True)
    (home / "Documentos").mkdir()
    (home / "Documentos" / "notas.md").write_text("# Notas\n\nCluster de prueba.\n", encoding="utf-8")
    (home / "scripts").mkdir()
    (home / "scripts" / "hola.sh").write_text("#!/bin/bash\necho hola\n", encoding="utf-8")
    (base / "etc").mkdir()
    (base / "etc" / "hostname").write_text(f"spark-{node_id}\n")
    models = {"spark1": [("glm-5.3-flash-nvfp4", "Glm5NextForConditionalGeneration", "glm5_next", 1048576, "modelopt")],
              "spark3": [("qwen3.8-27b-nvfp4", "Qwen3_8ForConditionalGeneration", "qwen3_8", 262144, "compressed-tensors")]}.get(node_id, [])
    for name, arch, mtype, ctx, quant in models:
        d = home / "models" / name
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"architectures": [arch], "model_type": mtype, "max_position_embeddings": ctx,
                                                   "quantization_config": {"quant_method": quant}}), encoding="utf-8")
        (d / "model-00001-of-00002.safetensors").write_bytes(b"\0" * 4096)
        (d / "model-00002-of-00002.safetensors").write_bytes(b"\0" * 2048)
        (d / ".prometheus.json").write_text(json.dumps({"repo": f"demo/{name}"}))
    return base


class FakeTransport:
    def __init__(self, node_id: str, world: FakeWorld, index: int = 0):
        self.node_id = node_id
        self.world = world
        self.index = index
        self.base = seed(world, node_id)
        self.sftp_lock = threading.RLock()
        self._sftp = LocalSFTP(self.base)

    def target(self) -> dict[str, Any]:
        return {"alias": self.node_id, "hostname": f"spark-{self.node_id}.local", "user": "demo", "port": 22, "key_files": []}

    def sftp(self) -> LocalSFTP:
        self._check_on()
        return self._sftp

    def close(self) -> None:
        pass

    def reset(self) -> None:
        pass

    def _check_on(self) -> None:
        from .errors import SparkError

        state = self.world.power.get(self.node_id)
        if state:
            raise SparkError("unreachable", f"{self.node_id} is {state} (demo).")

    # ----------------------------------------------------------------- commands
    def run(self, command: str, *, timeout: float = 30.0, stdin: Optional[str] = None) -> RunResult:
        self._check_on()
        with self.world.lock:
            self.world.commands.append((self.node_id, command))
        if stdin and stdin.startswith("# prometheus:"):
            op = stdin.split("\n", 1)[0].split(":", 1)[1].strip()
            import shlex

            args = json.loads(shlex.split(command)[2]) if command.startswith("python3 - ") else {}
            return RunResult(0, json.dumps(getattr(self, f"_op_{op}")(args)), "")
        if "systemctl" in command:
            verb = next((v for v in ("poweroff", "reboot", "suspend") if f"systemctl {v}" in command), "")
            self.world.power[self.node_id] = {"poweroff": "off", "reboot": "restarting", "suspend": "asleep"}.get(verb, "off")
            if verb == "reboot":
                threading.Timer(3.0, lambda: self.world.power.pop(self.node_id, None)).start()
            return RunResult(0, "SUDO\n", "")
        if command.startswith("loginctl"):
            return RunResult(0, "", "")
        if command.startswith("docker"):
            return RunResult(0, "vLLM server ready (demo)\n", "")
        return RunResult(0, "", "")

    def _local(self, path: str) -> str:
        return str(self._sftp._p(path))

    def _remote(self, local: str) -> str:
        rel = os.path.relpath(local, self.base).replace(os.sep, "/")
        return "/" + rel if rel != "." else "/"

    def _run_real(self, op: str, args: dict[str, Any]) -> Any:
        res = subprocess.run([sys.executable, "-", json.dumps(args)], input=remote_script(op), capture_output=True, text=True,
                             env={**os.environ, "HOME": self._local(HOME), "USERPROFILE": self._local(HOME)}, timeout=60)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def _op_models(self, args: dict[str, Any]) -> Any:
        args = {**args, "dirs": [self._local(d.replace("~", HOME, 1)) for d in args.get("dirs", [])], "hf_cache": False}
        out = self._run_real("models", args)
        for m in out["models"]:
            m["path"] = self._remote(m["path"])
        return out

    def _op_fsops(self, args: dict[str, Any]) -> Any:
        mapped = dict(args)
        for key in ("path", "dest"):
            if key in mapped:
                mapped[key] = self._local(mapped[key])
        if "paths" in mapped:
            mapped["paths"] = [self._local(p) for p in mapped["paths"]]
        out = self._run_real("fsops", mapped)
        if "sizes" in out:
            out["sizes"] = {args["paths"][i]: v for i, v in enumerate(out["sizes"].values())}
        for h in out.get("hits", []):
            h["path"] = self._remote(h["path"])
        if "copied" in out:
            out["copied"] = [self._remote(p) for p in out["copied"]]
        return out

    def _op_probe(self, args: dict[str, Any]) -> Any:
        t = time.time()
        phase = t / 7.0 + self.index
        busy = any(self.node_id in nodes for nodes in self.world.serving.values())
        util = (55 + 40 * math.sin(phase)) if busy else (3 + 2 * random.random())
        used = (96 if busy else 6) * 1024**3 + int(2 * 1024**3 * random.random())
        total = 121 * 1024**3
        base = int((t - self.world.started) * 1e9)
        counters = [base // 10 + int(util * 1e6), 0, base // 50, base // 4, 1000, 0, 100, 0, 0, 0]
        net = {}
        fabric = {"spark1": ["10.100.32.2", "10.100.33.2", "10.100.36.1", "10.100.37.1"],
                  "spark2": ["10.100.36.2", "10.100.37.2", "10.100.34.2", "10.100.35.2"],
                  "spark3": ["10.100.34.1", "10.100.35.1", "10.100.32.1", "10.100.33.1"]}.get(self.node_id, [])
        for i, (name, ip) in enumerate(zip(["enp1s0f0np0", "enP2p1s0f0np0", "enp1s0f1np1", "enP2p1s0f1np1"], fabric)):
            flow = int((t - self.world.started) * (2e9 if busy else 1e5))
            net[name] = {"rx": flow + i, "tx": flow + 2 * i, "speed_mbps": 200000, "v4": [ip + "/24"], "up": True, "mtu": 9000, "fabric": True,
                         "mac": f"4c:bb:47:ea:c7:0{i}"}
        net["enP7s7"] = {"rx": int((t - self.world.started) * 2e5), "tx": int((t - self.world.started) * 1e5), "speed_mbps": 10000,
                         "v4": [f"192.168.0.{230 + self.index}/24"], "up": True, "mtu": 1500, "fabric": False, "mac": f"30:c5:99:00:00:0{self.index}"}
        servers = []
        for recipe, nodes in self.world.serving.items():
            meta = self.world.recipes_meta().get(recipe)
            if meta and self.node_id in nodes:
                servers.append({"pid": 4242, "cmd": f"python3 -m vllm.entrypoints.openai.api_server --model /models/x --served-model-name {meta['served']} "
                                f"--port {meta['port']} --max-model-len {meta['max_len'] or 0} --tensor-parallel-size {len(meta['nodes'])}", "rss": 2 * 1024**3})
        home = self._local(HOME)
        st = shutil.disk_usage(home)
        return {
            "time": t, "hostname": f"spark-{self.node_id}", "kernel": "6.17.0-demo", "arch": "aarch64", "uptime_s": t - self.world.started + 3600,
            "home": HOME, "user": "demo", "os": "Ubuntu 24.04 LTS (demo)",
            "cpu": {"counters": counters, "per_core": [counters] * 4, "cores": 20, "load": [0.4, 0.3, 0.2], "temp_c": 41.0 + util / 5},
            "memory": {"total": total, "available": total - used, "free": total - used, "cached": 2 * 1024**3, "swap_total": 0, "swap_free": 0},
            "gpu": {"gpus": [{"name": "NVIDIA GB10", "util": round(util, 1), "temp_c": 38 + util / 3, "power_w": 12 + util, "power_limit_w": None,
                              "clock_mhz": 2400 if busy else 600, "clock_max_mhz": 3000, "mem_used_mb": None, "mem_total_mb": None,
                              "pstate": "P0", "driver": "580.178.04"}],
                    "apps": [{"pid": 4242, "name": "python3", "mem_mb": 90_000}] if busy else []},
            "disks": [{"path": "/", "total": 4_000_000_000_000, "free": 3_700_000_000_000 - (st.used % 10**9)}],
            "net": net, "top": [{"pid": 4242, "rss": 2 * 1024**3, "cmd": "python3 -m vllm ...", "gpu": busy}] if busy else [],
            "servers": servers, "containers": [{"id": "abc123", "name": f"demo-{r}", "image": "vllm/vllm-openai:demo", "status": "Up 3 minutes",
                                                "labels": "", "ports": "", "command": ""} for r, n in self.world.serving.items() if self.node_id in n],
            "docker": True,
        }

    def _op_jobs(self, args: dict[str, Any]) -> Any:
        op = args.get("op", "status")
        w = self.world
        if op == "start":
            job = {"id": args["id"], "node": self.node_id, "cmd": args["cmd"], "watch": args.get("watch", ""), "start": time.time(),
                   "env": args.get("env") or {}, "state": "running", "rc": None}
            with w.lock:
                w.jobs[args["id"]] = job
            return self._status(job)
        if op == "kill":
            out = []
            for i in args.get("ids", []):
                job = w.jobs.get(i)
                if job and job["state"] == "running":
                    job.update(state="failed", rc=-15)
                out.append(self._status(job) if job else {"id": i, "state": "missing"})
            return {"jobs": out}
        out = []
        for i in args.get("ids") or [j for j, v in w.jobs.items() if v["node"] == self.node_id]:
            job = w.jobs.get(i)
            out.append(self._status(job) if job else {"id": i, "state": "missing"})
        return {"jobs": out}

    def _status(self, job: dict[str, Any]) -> dict[str, Any]:
        w = self.world
        elapsed = time.time() - job["start"]
        if job["state"] == "running" and elapsed >= w.job_s:
            self._finish(job)
        watched = None
        if job.get("watch"):
            local = Path(self._local(job["watch"].replace("~", HOME, 1)))
            watched = sum(p.stat().st_size for p in local.rglob("*") if p.is_file()) if local.exists() else 0
            if job["state"] == "running":
                watched = int(6144 * min(1.0, elapsed / w.job_s))
        log = f"$ {job['cmd'][:200]}\n" + ("done\n" if job["state"] != "running" else f"{int(100 * min(1, elapsed / w.job_s))}%\n")
        return {"id": job["id"], "pid": 1000, "rc": job["rc"], "state": "running" if job["state"] == "running" else ("done" if job["rc"] == 0 else "failed"),
                "log": log, "watched_bytes": watched, "started": job["start"]}

    def _finish(self, job: dict[str, Any]) -> None:
        job["state"] = "done"
        job["rc"] = 0
        cmd = job["cmd"]
        env = job.get("env", {})
        if "hf download" in cmd and job.get("watch"):
            d = Path(self._local(job["watch"].replace("~", HOME, 1)))
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(json.dumps({"architectures": ["DemoForCausalLM"], "model_type": "demo", "max_position_embeddings": 131072}))
            (d / "model.safetensors").write_bytes(b"\0" * 6144)
        if "fail.sh" in cmd or "exit 3" in cmd:
            job["state"], job["rc"] = "failed", 3
        recipe = env.get("PROM_RECIPE")
        if recipe and "start.sh" in cmd:
            with self.world.lock:
                self.world.serving.setdefault(recipe, set()).add(self.node_id)
        if recipe and "stop.sh" in cmd:
            with self.world.lock:
                nodes = self.world.serving.get(recipe, set())
                nodes.discard(self.node_id)
                if not nodes:
                    self.world.serving.pop(recipe, None)


def fake_factory(world: FakeWorld):
    def make(conf: dict[str, Any], settings: Any) -> FakeTransport:
        ids = [n["id"] for n in settings["nodes"]]
        return FakeTransport(conf["id"], world, ids.index(conf["id"]) if conf["id"] in ids else 0)

    return make


def demo_recipes(folder: Path) -> None:
    """Two example recipes for the demo, written once."""
    examples = {
        "glm53-tp3": {"title": "GLM-5.3-Flash NVFP4 · 3 Sparks (TP=3) · 1M", "nodes": ["spark1", "spark2", "spark3"], "head": "spark1", "port": 8000,
                      "served_model_name": "glm-5.3-flash", "model": "~/models/glm-5.3-flash-nvfp4", "max_model_len": 1048576, "engine": "vllm",
                      "memory_gb": 100, "ready_timeout_s": 60, "tags": ["1M", "TP=3"]},
        "qwen38-27b-1m": {"title": "Qwen3.8 27B NVFP4 · Spark3 · 1M", "nodes": ["spark3"], "port": 8001, "served_model_name": "qwen3.8-27b",
                          "model": "~/models/qwen3.8-27b-nvfp4", "max_model_len": 1048576, "engine": "vllm", "memory_gb": 60, "ready_timeout_s": 60,
                          "tags": ["1M"]},
    }
    for name, recipe in examples.items():
        d = folder / name
        if d.exists():
            continue
        d.mkdir(parents=True)
        (d / "recipe.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        (d / "start.sh").write_text("#!/bin/bash\necho start $PROM_NODE\n", encoding="utf-8")
        (d / "stop.sh").write_text("#!/bin/bash\necho stop $PROM_NODE\n", encoding="utf-8")
