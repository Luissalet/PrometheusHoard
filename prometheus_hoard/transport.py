"""SSH to the Sparks: one persistent paramiko connection per node, commands and SFTP over it.

The host, user, port and key of a node come from the OpenSSH configuration of this computer (``~/.ssh/config`` and every file it
``Include``s — NVIDIA Sync writes its ``Spark1``.. aliases in an included file), so the app reaches a Spark exactly as ``ssh Spark1``
does. Connections are opened lazily, kept alive and reopened once when a call finds them dead.

Every remote helper is a Python script from ``remote/`` sent on stdin to ``python3 -`` with its arguments as one JSON argument; the
first line of each script is ``# prometheus:<op>``, which is how the demo cluster (``fake.py``) recognises them."""

from __future__ import annotations

import glob
import json
import logging
import os
import shlex
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from .errors import SparkError

log = logging.getLogger("prometheus.ssh")
REMOTE_DIR = Path(__file__).resolve().parent / "remote"


@dataclass
class RunResult:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Transport(Protocol):
    node_id: str

    def run(self, command: str, *, timeout: float = 30.0, stdin: Optional[str] = None) -> RunResult: ...

    def sftp(self) -> Any: ...

    def close(self) -> None: ...

    def target(self) -> dict[str, Any]: ...


def remote_script(op: str) -> str:
    path = REMOTE_DIR / f"{op}.py"
    return path.read_text(encoding="utf-8")


def run_script(transport: Transport, op: str, args: Optional[dict[str, Any]] = None, *, timeout: float = 30.0) -> Any:
    """Run ``remote/<op>.py`` on the node and parse its JSON answer (the last line of stdout)."""
    payload = json.dumps(args or {}, ensure_ascii=True)
    res = transport.run(f"python3 - {shlex.quote(payload)}", timeout=timeout, stdin=remote_script(op))
    lines = [ln for ln in res.out.splitlines() if ln.strip()]
    if not lines:
        raise SparkError("remote_failed", f"{transport.node_id}: {op} returned nothing (exit {res.rc}).", hint=(res.err or "")[-400:])
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise SparkError("remote_failed", f"{transport.node_id}: {op} returned something that is not JSON.", hint=(res.err or res.out)[-400:]) from exc
    if isinstance(data, dict) and data.get("error") and data.get("code"):
        raise SparkError(str(data["code"]), str(data["error"]), hint=str(data.get("hint") or ""))
    return data


# ----------------------------------------------------------------------------------------------- OpenSSH config with Include
def _ssh_dir() -> Path:
    return Path(os.environ.get("PROMETHEUS_SSH_DIR") or (Path.home() / ".ssh"))


def read_ssh_config_text(path: Optional[Path] = None, _depth: int = 0) -> str:
    """The text of ``~/.ssh/config`` with every ``Include`` expanded in place (paramiko's parser does not follow them)."""
    path = path or (_ssh_dir() / "config")
    if _depth > 8 or not path.is_file():
        return ""
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if stripped.lower().startswith("include ") or stripped.lower().startswith("include\t"):
            for target in shlex.split(stripped[8:].strip(), posix=False):
                target = target.strip('"')
                target = os.path.expanduser(target)
                if not os.path.isabs(target):
                    target = str(_ssh_dir() / target)
                for match in sorted(glob.glob(target)):
                    out.append(read_ssh_config_text(Path(match), _depth + 1))
            continue
        out.append(raw)
    return "\n".join(out)


def resolve_host(alias: str) -> dict[str, Any]:
    """``{hostname, user, port, key_files}`` for an alias, as OpenSSH would resolve it."""
    import paramiko

    cfg = paramiko.SSHConfig.from_text(read_ssh_config_text() or "")
    entry = cfg.lookup(alias)
    keys = [os.path.expanduser(k.strip('"')) for k in entry.get("identityfile", [])]
    host = entry.get("hostname", alias)
    user = entry.get("user") or os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return {"alias": alias, "hostname": host, "user": user, "port": int(entry.get("port", 22)), "key_files": [k for k in keys if os.path.isfile(k)]}


class SshTransport:
    """A node reached over SSH. Thread-safe: commands open their own channel on the shared connection; SFTP has one session per node
    guarded by a lock (paramiko's SFTP client is not safe across threads)."""

    def __init__(self, node_id: str, alias: str, *, timeout: float = 8.0, host: str = "", proxy_jump: str = ""):
        self.node_id = node_id
        self.alias = alias
        self.host_override = host
        self.proxy_jump = proxy_jump
        self._jump: Any = None
        self.timeout = timeout
        self._client: Any = None
        self._sftp: Any = None
        self._lock = threading.RLock()
        self.sftp_lock = threading.RLock()
        self._target: dict[str, Any] = {}
        self.last_error = ""
        self._down_until = 0.0

    def target(self) -> dict[str, Any]:
        if not self._target:
            try:
                self._target = resolve_host(self.alias)
                if self.host_override:
                    self._target["hostname"] = self.host_override
                if self.proxy_jump:
                    self._target["proxy_jump"] = self.proxy_jump
            except Exception as exc:  # noqa: BLE001 - a broken config is reported, not raised at import
                self._target = {"alias": self.alias, "hostname": self.alias, "user": "", "port": 22, "key_files": [], "error": str(exc)}
        return dict(self._target)

    def _connect(self) -> Any:
        import paramiko

        if time.monotonic() < self._down_until:
            raise SparkError("unreachable", f"{self.node_id} is not answering over SSH ({self.last_error}).",
                             hint="Check that the Spark is on and on the network (ssh " + self.alias + " from a terminal).")
        t = self.target()
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        known = _ssh_dir() / "known_hosts"
        if known.is_file():
            try:
                client.load_host_keys(str(known))
            except Exception:  # noqa: BLE001 - a malformed known_hosts must not stop the app
                pass
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            sock = None
            if self.proxy_jump:
                jt = resolve_host(self.proxy_jump)
                jump = paramiko.SSHClient()
                jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                jump.connect(jt["hostname"], port=jt["port"], username=jt["user"] or None, key_filename=jt["key_files"] or None,
                             timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout, look_for_keys=not jt["key_files"])
                jtrans = jump.get_transport()
                jtrans.set_keepalive(15)
                sock = jtrans.open_channel("direct-tcpip", (t["hostname"], t["port"]), ("127.0.0.1", 0), timeout=self.timeout)
                self._jump = jump
            client.connect(t["hostname"], port=t["port"], username=t["user"] or None, key_filename=t["key_files"] or None,
                           timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout, look_for_keys=not t["key_files"],
                           allow_agent=True, sock=sock)
        except (OSError, socket.error, paramiko.SSHException) as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            self._down_until = time.monotonic() + 5.0  # do not hammer a machine that is off
            raise SparkError("unreachable", f"{self.node_id} ({t['hostname']}) is not reachable over SSH: {self.last_error}",
                             hint="Check that the Spark is on and on the network (ssh " + self.alias + " from a terminal).") from exc
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)
        self.last_error = ""
        return client

    def _client_alive(self) -> Any:
        with self._lock:
            transport = self._client.get_transport() if self._client else None
            if transport is None or not transport.is_active():
                self._drop()
                self._client = self._connect()
            return self._client

    def _drop(self) -> None:
        for obj in (self._sftp, self._client, self._jump):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        self._sftp = None
        self._client = None
        self._jump = None

    def run(self, command: str, *, timeout: float = 30.0, stdin: Optional[str] = None) -> RunResult:
        for attempt in (1, 2):
            client = self._client_alive()
            try:
                chan_in, chan_out, chan_err = client.exec_command(command, timeout=timeout)
                if stdin is not None:
                    chan_in.write(stdin)
                    chan_in.flush()
                chan_in.channel.shutdown_write()
                out = chan_out.read().decode("utf-8", errors="replace")
                err = chan_err.read().decode("utf-8", errors="replace")
                rc = chan_out.channel.recv_exit_status()
                return RunResult(rc, out, err)
            except socket.timeout as exc:
                raise SparkError("timeout", f"{self.node_id}: the command took longer than {timeout:.0f} s.") from exc
            except Exception as exc:  # noqa: BLE001 - a dead connection is reopened once
                if attempt == 2 or isinstance(exc, SparkError):
                    raise SparkError("unreachable", f"{self.node_id}: SSH failed: {exc}") from exc
                with self._lock:
                    self._drop()
        raise AssertionError("unreachable")

    def sftp(self) -> Any:
        with self._lock:
            client = self._client_alive()
            if self._sftp is None or self._sftp.sock.closed:
                self._sftp = client.open_sftp()
                self._sftp.get_channel().settimeout(max(self.timeout, 30.0))
            return self._sftp

    def reset(self) -> None:
        with self._lock:
            self._drop()
            self._down_until = 0.0

    def close(self) -> None:
        with self._lock:
            self._drop()
