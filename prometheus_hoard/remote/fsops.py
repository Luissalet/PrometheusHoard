# prometheus:fsops
"""File operations that are cheaper on the Spark than over SFTP: folder sizes, search, copy, emptying the trash."""
import fnmatch, json, os, shutil, sys, time

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
op = ARGS.get("op")


def size(path):
    total = 0
    for root, dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


if op == "du":
    out = {}
    for p in ARGS.get("paths", []):
        p2 = os.path.expanduser(p)
        out[p] = size(p2) if os.path.isdir(p2) else (os.lstat(p2).st_size if os.path.exists(p2) else None)
    print(json.dumps({"sizes": out}))
elif op == "search":
    base = os.path.expanduser(ARGS["path"])
    pattern = ARGS.get("pattern", "*").lower()
    if "*" not in pattern and "?" not in pattern:
        pattern = f"*{pattern}*"
    limit = int(ARGS.get("limit", 200))
    deadline = time.time() + float(ARGS.get("max_s", 8))
    hits, truncated = [], False
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".") or ARGS.get("hidden")]
        for n in dirs + names:
            if fnmatch.fnmatch(n.lower(), pattern):
                p = os.path.join(root, n)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                hits.append({"path": p, "name": n, "dir": os.path.isdir(p), "size": st.st_size, "mtime": st.st_mtime})
                if len(hits) >= limit:
                    truncated = True
                    break
        if truncated or time.time() > deadline:
            truncated = True
            break
    print(json.dumps({"hits": hits, "truncated": truncated}))
elif op == "copy":
    dest = os.path.expanduser(ARGS["dest"])
    done = []
    for p in ARGS["paths"]:
        src = os.path.expanduser(p)
        target = os.path.join(dest, os.path.basename(src.rstrip("/")))
        if os.path.exists(target):
            stem, ext = os.path.splitext(os.path.basename(target))
            i = 2
            while os.path.exists(os.path.join(dest, f"{stem} ({i}){ext}")):
                i += 1
            target = os.path.join(dest, f"{stem} ({i}){ext}")
        if os.path.isdir(src):
            shutil.copytree(src, target, symlinks=True)
        else:
            shutil.copy2(src, target)
        done.append(target)
    print(json.dumps({"copied": done}))
elif op == "rmtree":
    removed = []
    for p in ARGS["paths"]:
        p2 = os.path.expanduser(p)
        if os.path.isdir(p2) and not os.path.islink(p2):
            shutil.rmtree(p2, ignore_errors=True)
        elif os.path.lexists(p2):
            os.unlink(p2)
        removed.append(p)
    print(json.dumps({"removed": removed}))
else:
    print(json.dumps({"error": f"unknown op {op}", "code": "invalid"}))
