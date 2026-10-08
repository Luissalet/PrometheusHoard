# prometheus:jobs
"""Background jobs on a Spark (downloads, copies between Sparks, recipe steps): start one detached with its own log, or report the
state of several. A job is a folder <remote_dir>/jobs/<id>/ with cmd, pid, log and rc (written when it ends)."""
import json, os, signal, subprocess, sys, time

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
BASE = os.path.join(os.path.expanduser(ARGS.get("remote_dir", "~/sparks")), "jobs")
os.makedirs(BASE, exist_ok=True)


def tail(path, n):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    # progress bars rewrite the same line with \r: keep the last state of each line
    return "\n".join(line.split("\r")[-1] for line in data.split("\n"))


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return False


def status(job_id, n):
    d = os.path.join(BASE, job_id)
    if not os.path.isdir(d):
        return {"id": job_id, "state": "missing"}
    pid = int(open(os.path.join(d, "pid")).read().strip() or 0) if os.path.exists(os.path.join(d, "pid")) else 0
    rc_path = os.path.join(d, "rc")
    rc = None
    if os.path.exists(rc_path):
        raw = open(rc_path).read().strip()
        rc = int(raw) if raw.lstrip("-").isdigit() else None
    running = bool(pid) and alive(pid) and rc is None
    watch = ""
    if os.path.exists(os.path.join(d, "watch")):
        watch = open(os.path.join(d, "watch")).read().strip()
    watched = None
    if watch:
        total = 0
        for root, _dirs, names in os.walk(os.path.expanduser(watch)):
            for name in names:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
        # a Hugging Face download keeps partial files under .cache/huggingface/download as *.incomplete: count them as well
        watched = total
    state = "running" if running else ("done" if rc == 0 else ("failed" if rc is not None else "lost"))
    return {"id": job_id, "pid": pid, "rc": rc, "state": state, "log": tail(os.path.join(d, "log"), n), "watched_bytes": watched,
            "started": os.path.getmtime(os.path.join(d, "cmd")) if os.path.exists(os.path.join(d, "cmd")) else None}


op = ARGS.get("op", "status")
if op == "start":
    job_id = ARGS["id"]
    d = os.path.join(BASE, job_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "cmd"), "w") as fh:
        fh.write(ARGS["cmd"])
    if ARGS.get("watch"):
        with open(os.path.join(d, "watch"), "w") as fh:
            fh.write(ARGS["watch"])
    wrapper = f'cd ~ && ( {ARGS["cmd"]} ) > "{d}/log" 2>&1; echo $? > "{d}/rc"'
    proc = subprocess.Popen(["bash", "-lc", wrapper], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, env={**os.environ, **{k: str(v) for k, v in (ARGS.get("env") or {}).items()}})
    with open(os.path.join(d, "pid"), "w") as fh:
        fh.write(str(proc.pid))
    time.sleep(0.3)
    print(json.dumps(status(job_id, 2000)))
elif op == "kill":
    out = []
    for job_id in ARGS.get("ids", []):
        st = status(job_id, 0)
        if st.get("pid") and st["state"] == "running":
            try:
                os.killpg(os.getpgid(st["pid"]), signal.SIGTERM)
            except OSError:
                pass
            time.sleep(1.0)
            try:
                os.killpg(os.getpgid(st["pid"]), signal.SIGKILL)
            except OSError:
                pass
            with open(os.path.join(BASE, job_id, "rc"), "w") as fh:
                fh.write("-15")
        out.append(status(job_id, 2000))
    print(json.dumps({"jobs": out}))
else:
    ids = ARGS.get("ids") or sorted(os.listdir(BASE))
    print(json.dumps({"jobs": [status(i, int(ARGS.get("tail", 2000))) for i in ids]}))
