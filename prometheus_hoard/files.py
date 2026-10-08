"""The explorer: every Spark is a drive, browsed over SFTP.

Writes are allowed inside the home folder of the Spark user (where the models, recipes and jobs live); outside it everything is read-only,
and only when the setting ``show_system`` is on. Deleting moves to a trash folder on the same Spark (``trash_dir``), from which items can
be restored; emptying the trash or deleting permanently needs ``confirm``."""

from __future__ import annotations

import json
import posixpath
import stat as statmod
import time
from typing import Any, BinaryIO, Iterator, Optional

from .cluster import Cluster, Node
from .errors import SparkError
from .transport import run_script

TEXT_EXT = {".txt", ".md", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".py", ".sh", ".log", ".csv", ".tsv",
            ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".env", ".xml", ".rs", ".go", ".c", ".h", ".cpp", ".java", ".sql", ".jinja",
            ".service", ".dockerfile", ""}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
TRASH_META = ".prometheus-trash.json"


def kind_of(name: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    ext = posixpath.splitext(name.lower())[1]
    if ext in (".safetensors", ".gguf", ".bin", ".pt", ".pth"):
        return "weights"
    if ext in IMAGE_EXT:
        return "image"
    if ext in (".mp4", ".mkv", ".webm", ".mov"):
        return "video"
    if ext in (".wav", ".mp3", ".flac", ".ogg"):
        return "audio"
    if ext in (".zip", ".tar", ".gz", ".tgz", ".xz", ".zst", ".7z"):
        return "archive"
    if ext in (".py", ".sh", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".c", ".cpp", ".h", ".java"):
        return "code"
    if ext in TEXT_EXT or name.lower() in ("dockerfile", "makefile", "readme", "license"):
        return "text"
    return "file"


class Files:
    def __init__(self, cluster: Cluster):
        self.cluster = cluster

    # ------------------------------------------------------------------ paths
    def home(self, node: Node) -> str:
        if node.metrics and node.metrics.get("home"):
            return node.metrics["home"]
        with node.transport.sftp_lock:
            return node.transport.sftp().normalize(".")

    def resolve(self, node: Node, path: str) -> str:
        path = (path or "~").strip()
        home = self.home(node)
        if path == "~" or path == "":
            return home
        if path.startswith("~/"):
            path = posixpath.join(home, path[2:])
        if not path.startswith("/"):
            path = posixpath.join(home, path)
        norm = posixpath.normpath(path)
        if not norm.startswith(home) and not self.cluster.settings["show_system"]:
            raise SparkError("forbidden", f"{norm} is outside the home folder of {node.conf['name']}.",
                             hint="Turn on «Mostrar el sistema» in Ajustes to browse the whole disk (read-only outside home).")
        return norm

    def writable(self, node: Node, path: str) -> str:
        norm = self.resolve(node, path)
        home = self.home(node)
        if not (norm == home or norm.startswith(home + "/")):
            raise SparkError("forbidden", f"{norm} is outside the home folder: the explorer only writes inside {home}.")
        if norm == home:
            raise SparkError("forbidden", "The home folder itself cannot be moved, renamed or deleted.")
        return norm

    def _sftp(self, node: Node):
        return node.transport.sftp()

    # ------------------------------------------------------------------ reading
    def entry(self, parent: str, name: str, attr: Any) -> dict[str, Any]:
        mode = attr.st_mode or 0
        is_link = statmod.S_ISLNK(mode)
        is_dir = statmod.S_ISDIR(mode)
        return {"name": name, "path": posixpath.join(parent, name), "dir": is_dir, "link": is_link, "size": attr.st_size or 0,
                "mtime": attr.st_mtime, "mode": statmod.filemode(mode) if mode else "", "kind": kind_of(name, is_dir),
                "hidden": name.startswith(".")}

    def list(self, node_id: str, path: str = "~", *, hidden: bool = False, sort: str = "name", limit: int = 5000) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        target = self.resolve(node, path)
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            try:
                attrs = sftp.listdir_attr(target)
            except FileNotFoundError as exc:
                raise SparkError("not_found", f"{target} does not exist on {node.conf['name']}.") from exc
            except PermissionError as exc:
                raise SparkError("forbidden", f"No permission to read {target} on {node.conf['name']}.") from exc
            except OSError as exc:
                raise SparkError("invalid", f"{target}: {exc}") from exc
            entries = []
            for a in attrs:
                e = self.entry(target, a.filename, a)
                if e["link"]:  # follow symlinks for type and size, keep the link flag
                    try:
                        real = sftp.stat(e["path"])
                        e["dir"] = statmod.S_ISDIR(real.st_mode or 0)
                        e["size"] = real.st_size or 0
                        e["kind"] = kind_of(e["name"], e["dir"])
                    except OSError:
                        e["broken"] = True
                entries.append(e)
        if not hidden:
            entries = [e for e in entries if not e["hidden"]]
        key = {"name": lambda e: (not e["dir"], e["name"].lower()), "size": lambda e: (not e["dir"], -e["size"]),
               "mtime": lambda e: (not e["dir"], -(e["mtime"] or 0)), "kind": lambda e: (not e["dir"], e["kind"], e["name"].lower())}.get(sort)
        entries.sort(key=key or (lambda e: (not e["dir"], e["name"].lower())))
        home = self.home(node)
        crumbs = []
        if target.startswith(home):
            crumbs.append({"name": "~", "path": home})
            rest = target[len(home):].strip("/")
            acc = home
            for part in rest.split("/") if rest else []:
                acc = posixpath.join(acc, part)
                crumbs.append({"name": part, "path": acc})
        else:
            acc = "/"
            crumbs.append({"name": "/", "path": "/"})
            for part in target.strip("/").split("/") if target != "/" else []:
                acc = posixpath.join(acc, part)
                crumbs.append({"name": part, "path": acc})
        return {"node": node.id, "path": target, "parent": posixpath.dirname(target) if target != "/" else None, "home": home,
                "writable": target == home or target.startswith(home + "/"), "crumbs": crumbs, "entries": entries[:limit],
                "count": len(entries), "truncated": len(entries) > limit}

    def info(self, node_id: str, path: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        target = self.resolve(node, path)
        with node.transport.sftp_lock:
            try:
                a = self._sftp(node).lstat(target)
            except FileNotFoundError as exc:
                raise SparkError("not_found", f"{target} does not exist on {node.conf['name']}.") from exc
        e = self.entry(posixpath.dirname(target), posixpath.basename(target), a)
        if e["dir"]:
            e["bytes"] = run_script(node.transport, "fsops", {"op": "du", "paths": [target]}, timeout=120)["sizes"].get(target)
        return {"node": node.id, **e}

    def read_text(self, node_id: str, path: str, *, max_bytes: int = 512_000) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        target = self.resolve(node, path)
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            try:
                a = sftp.stat(target)
                if statmod.S_ISDIR(a.st_mode or 0):
                    raise SparkError("invalid", f"{target} is a folder.")
                with sftp.open(target, "rb") as fh:
                    data = fh.read(max_bytes + 1)
            except FileNotFoundError as exc:
                raise SparkError("not_found", f"{target} does not exist on {node.conf['name']}.") from exc
        truncated = len(data) > max_bytes
        data = data[:max_bytes]
        binary = b"\x00" in data[:8192]
        text = "" if binary else data.decode("utf-8", errors="replace")
        return {"node": node.id, "path": target, "size": a.st_size, "truncated": truncated, "binary": binary, "text": text,
                "kind": kind_of(posixpath.basename(target), False)}

    def open_stream(self, node_id: str, path: str, *, chunk: int = 1 << 20) -> tuple[str, int, Iterator[bytes]]:
        """``(name, size, iterator)`` for a download. The SFTP lock is held only per chunk so a long download does not freeze the explorer."""
        node = self.cluster.node(node_id)
        target = self.resolve(node, path)
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            try:
                a = sftp.stat(target)
            except FileNotFoundError as exc:
                raise SparkError("not_found", f"{target} does not exist on {node.conf['name']}.") from exc
            if statmod.S_ISDIR(a.st_mode or 0):
                raise SparkError("invalid", f"{target} is a folder: download its files, or compress it first.")
            fh = sftp.open(target, "rb")
            try:
                fh.prefetch(a.st_size)
            except Exception:  # noqa: BLE001 - fake SFTP and older servers
                pass

        def gen() -> Iterator[bytes]:
            try:
                while True:
                    with node.transport.sftp_lock:
                        block = fh.read(chunk)
                    if not block:
                        break
                    yield block
            finally:
                fh.close()

        return posixpath.basename(target), int(a.st_size or 0), gen()

    # ------------------------------------------------------------------ writing
    def write_text(self, node_id: str, path: str, content: str, *, overwrite: bool = False) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        target = self.writable(node, path)
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            if not overwrite:
                try:
                    sftp.stat(target)
                    raise SparkError("conflict", f"{target} already exists.", hint="Pass overwrite=true to replace it.")
                except FileNotFoundError:
                    pass
            with sftp.open(target, "wb") as fh:
                fh.write(content.encode("utf-8"))
        return {"node": node.id, "path": target, "bytes": len(content.encode("utf-8"))}

    def upload(self, node_id: str, folder: str, name: str, stream: BinaryIO, *, overwrite: bool = False) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        name = posixpath.basename(name.replace("\\", "/")).strip()
        if not name or name in (".", ".."):
            raise SparkError("invalid", "The file needs a name.")
        target = self.writable(node, posixpath.join(self.resolve(node, folder), name))
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            if not overwrite:
                try:
                    sftp.stat(target)
                    stem, ext = posixpath.splitext(name)
                    i = 2
                    while True:
                        candidate = posixpath.join(posixpath.dirname(target), f"{stem} ({i}){ext}")
                        try:
                            sftp.stat(candidate)
                            i += 1
                        except FileNotFoundError:
                            target = candidate
                            break
                except FileNotFoundError:
                    pass
            written = 0
            with sftp.open(target, "wb") as fh:
                fh.set_pipelined(True) if hasattr(fh, "set_pipelined") else None
                while True:
                    block = stream.read(1 << 20)
                    if not block:
                        break
                    fh.write(block)
                    written += len(block)
        return {"node": node.id, "path": target, "bytes": written}

    def mkdir(self, node_id: str, path: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        target = self.writable(node, path)
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            parts = target.split("/")
            acc = ""
            for part in parts[1:]:
                acc += "/" + part
                try:
                    sftp.stat(acc)
                except FileNotFoundError:
                    sftp.mkdir(acc)
        return {"node": node.id, "path": target}

    def rename(self, node_id: str, path: str, new_name: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        src = self.writable(node, path)
        new_name = new_name.strip()
        if not new_name or "/" in new_name or new_name in (".", ".."):
            raise SparkError("invalid", "A name cannot be empty or contain «/».")
        dst = posixpath.join(posixpath.dirname(src), new_name)
        self._move_one(node, src, dst)
        return {"node": node.id, "from": src, "path": dst}

    def _move_one(self, node: Node, src: str, dst: str) -> None:
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            try:
                sftp.stat(dst)
                raise SparkError("conflict", f"{dst} already exists.")
            except FileNotFoundError:
                pass
            try:
                sftp.posix_rename(src, dst) if hasattr(sftp, "posix_rename") else sftp.rename(src, dst)
            except FileNotFoundError as exc:
                raise SparkError("not_found", f"{src} does not exist.") from exc

    def move(self, node_id: str, paths: list[str], dest: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        folder = self.writable(node, dest) if dest not in ("~", "") else self.home(node)
        moved = []
        for p in paths:
            src = self.writable(node, p)
            if folder == src or folder.startswith(src + "/"):
                raise SparkError("invalid", f"{src} cannot be moved inside itself.")
            dst = posixpath.join(folder, posixpath.basename(src))
            self._move_one(node, src, dst)
            moved.append(dst)
        return {"node": node.id, "moved": moved}

    def copy(self, node_id: str, paths: list[str], dest: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        folder = self.resolve(node, dest)
        self.writable(node, posixpath.join(folder, "x"))
        srcs = [self.resolve(node, p) for p in paths]
        out = run_script(node.transport, "fsops", {"op": "copy", "paths": srcs, "dest": folder}, timeout=1800)
        return {"node": node.id, "copied": out.get("copied", [])}

    def delete(self, node_id: str, paths: list[str], *, permanent: bool = False, confirm: bool = False) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        targets = [self.writable(node, p) for p in paths]
        if permanent:
            if not confirm:
                raise SparkError("confirm_required", f"Deleting {len(targets)} item(s) permanently cannot be undone.",
                                 hint="Repeat with confirm=true if the user asked for it, or delete without permanent to use the trash.")
            run_script(node.transport, "fsops", {"op": "rmtree", "paths": targets}, timeout=600)
            return {"node": node.id, "deleted": targets, "permanent": True}
        trash = self.resolve(node, self.cluster.settings["trash_dir"])
        self.mkdir(node.id, trash)
        trashed = []
        for src in targets:
            if src == trash or src.startswith(trash + "/"):
                raise SparkError("invalid", "That is already in the trash: empty the trash or restore it.")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            item_id = f"{stamp}-{abs(hash(src)) % 100000:05d}"
            folder = posixpath.join(trash, item_id)
            with node.transport.sftp_lock:
                sftp = self._sftp(node)
                sftp.mkdir(folder)
                with sftp.open(posixpath.join(folder, TRASH_META), "wb") as fh:
                    fh.write(json.dumps({"origin": src, "deleted": time.time()}).encode())
            self._move_one(node, src, posixpath.join(folder, posixpath.basename(src)))
            trashed.append({"id": item_id, "origin": src})
        return {"node": node.id, "trashed": trashed, "permanent": False}

    def trash_list(self, node_id: str) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        trash = self.resolve(node, self.cluster.settings["trash_dir"])
        items = []
        with node.transport.sftp_lock:
            sftp = self._sftp(node)
            try:
                folders = sftp.listdir(trash)
            except FileNotFoundError:
                folders = []
            for item_id in sorted(folders, reverse=True):
                meta = {}
                try:
                    with sftp.open(posixpath.join(trash, item_id, TRASH_META), "rb") as fh:
                        meta = json.loads(fh.read().decode() or "{}")
                except (OSError, ValueError):
                    pass
                names = [n for n in sftp.listdir(posixpath.join(trash, item_id)) if n != TRASH_META]
                items.append({"id": item_id, "origin": meta.get("origin", ""), "deleted": meta.get("deleted"), "names": names})
        sizes = run_script(node.transport, "fsops", {"op": "du", "paths": [posixpath.join(trash, i["id"]) for i in items]}, timeout=120)["sizes"] if items else {}
        for i in items:
            i["bytes"] = sizes.get(posixpath.join(trash, i["id"]))
        return {"node": node.id, "trash": trash, "items": items}

    def trash_restore(self, node_id: str, ids: list[str]) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        trash = self.resolve(node, self.cluster.settings["trash_dir"])
        listing = {i["id"]: i for i in self.trash_list(node.id)["items"]}
        restored = []
        for item_id in ids:
            item = listing.get(item_id)
            if not item or not item["names"]:
                raise SparkError("not_found", f"{item_id} is not in the trash.")
            origin = item["origin"] or posixpath.join(self.home(node), item["names"][0])
            self.mkdir(node.id, posixpath.dirname(origin)) if posixpath.dirname(origin) != self.home(node) else None
            self._move_one(node, posixpath.join(trash, item_id, item["names"][0]), origin)
            run_script(node.transport, "fsops", {"op": "rmtree", "paths": [posixpath.join(trash, item_id)]}, timeout=60)
            restored.append(origin)
        return {"node": node.id, "restored": restored}

    def trash_empty(self, node_id: str, *, confirm: bool = False, ids: Optional[list[str]] = None) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        if not confirm:
            raise SparkError("confirm_required", "Emptying the trash deletes its contents for good.", hint="Repeat with confirm=true.")
        trash = self.resolve(node, self.cluster.settings["trash_dir"])
        items = ids or [i["id"] for i in self.trash_list(node.id)["items"]]
        run_script(node.transport, "fsops", {"op": "rmtree", "paths": [posixpath.join(trash, i) for i in items]}, timeout=600)
        return {"node": node.id, "emptied": len(items)}

    def search(self, node_id: str, path: str, pattern: str, *, hidden: bool = False, limit: int = 200) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        base = self.resolve(node, path)
        out = run_script(node.transport, "fsops", {"op": "search", "path": base, "pattern": pattern, "hidden": hidden, "limit": limit}, timeout=30)
        for h in out["hits"]:
            h["kind"] = kind_of(h["name"], h["dir"])
        return {"node": node.id, "path": base, **out}

    def du(self, node_id: str, paths: list[str]) -> dict[str, Any]:
        node = self.cluster.node(node_id)
        targets = [self.resolve(node, p) for p in paths]
        return {"node": node.id, **run_script(node.transport, "fsops", {"op": "du", "paths": targets}, timeout=300)}
