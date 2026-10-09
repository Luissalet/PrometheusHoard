# prometheus:accesslog
"""The HTTP access lines a model server wrote to its container log since a moment: who asked it, and what.

vLLM's own metrics never say who asks, but uvicorn logs every request (``INFO:     192.0.2.5:57320 - "POST /v1/chat/completions HTTP/1.1" 200 OK``).
Reads ``docker logs --timestamps --since <cursor>`` of the first container among ``containers`` (ids or names) that docker knows, with
``sudo -n`` when the user is not in the docker group (never a password prompt), and answers one JSON line: ``{ok, container, via,
lines, truncated, ssh_client, now}``. Only access lines newer than the cursor come back, at most ``max_lines`` of them."""
import calendar, collections, json, os, re, subprocess, sys, time

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
MAX_LINES = int(ARGS.get("max_lines") or 20000)
TIMEOUT = float(ARGS.get("timeout_s") or 20)
STAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?")
ACCESS = re.compile(r'\S+ - "[A-Z]+ \S+ HTTP/[\d.]+" \d{3}')


def stamp_ns(text):
    """Nanoseconds since the epoch of the RFC 3339 stamp at the start of a line (None when it has none)."""
    m = STAMP.match(text)
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    sec = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s)))
    if zone and zone != "Z":
        off = (int(zone[1:3]) * 3600 + int(zone[4:6]) * 60) * (1 if zone[0] == "+" else -1)
        sec -= off
    return sec * 10**9 + int(((frac or "") + "000000000")[:9])


def read_logs(prefix, container):
    """(returncode, access lines, truncated, stderr-ish text) of one ``docker logs`` run; stderr of docker is merged because uvicorn logs there."""
    since = ARGS.get("since")
    cmd = prefix + ["logs", "--timestamps"] + (["--since", str(since)] if since else []) + [container]
    cursor = stamp_ns(str(since)) if since else None
    kept = collections.deque(maxlen=MAX_LINES)
    seen = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    deadline = time.time() + TIMEOUT
    head = []
    try:
        for raw in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if len(head) < 3:
                head.append(line)
            if not ACCESS.search(line):
                continue
            ns = stamp_ns(line)
            if cursor is not None and ns is not None and ns <= cursor:
                continue
            seen += 1
            kept.append((ns or 0, len(kept), line[:400]))
    finally:
        proc.stdout.close()
        rc = proc.wait()
    ordered = [t[2] for t in sorted(kept, key=lambda t: (t[0], t[1]))]
    return rc, ordered, seen > len(kept), " ".join(head)[:300]


def main():
    out = {"ok": False, "container": "", "via": "", "lines": [], "truncated": False, "now": time.time(),
           "ssh_client": (os.environ.get("SSH_CONNECTION") or "").split(" ")[0]}
    candidates = [c for c in ARGS.get("containers") or [] if c]
    if not candidates:
        out["error"] = "no_container"
        return out
    prefixes = [["docker"], ["sudo", "-n", "docker"]]
    if ARGS.get("prefer_sudo"):
        prefixes.reverse()
    problem = ""
    for container in candidates:
        for prefix in prefixes:
            try:
                rc, lines, truncated, why = read_logs(prefix, container)
            except OSError as exc:      # docker (or sudo) is not installed
                problem = problem or ("docker_missing: " + str(exc))
                continue
            if rc == 0:
                out.update(ok=True, container=container, via="sudo" if prefix[0] == "sudo" else "docker", lines=lines, truncated=truncated)
                return out
            low = why.lower()
            if "no such container" in low:
                problem = problem or "no_container"
                break           # another prefix will not find it either: try the next candidate
            problem = "docker_denied" if ("permission denied" in low or "password is required" in low or "a password is required" in low
                                          or "sudo:" in low or "daemon" in low) else (why or "docker_failed")
    out["error"] = problem or "docker_failed"
    return out


print(json.dumps(main()))
