# prometheus:probe
"""Live snapshot of a Spark as one JSON line: CPU counters, memory, GPU, disks, network counters, addresses, inference servers,
containers and the processes that hold the most memory. Raw counters: the app computes rates between two snapshots."""
import json, os, re, shutil, socket, subprocess, sys, time

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
HOME = os.path.expanduser("~")
SERVER_HINTS = ("vllm", "sglang", "llama-server", "llama_server", "ollama", "trtllm", "tensorrt_llm", "text-generation-launcher", "tabbyapi", "exllama")


def sh(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return default


def cpu():
    first = read("/proc/stat").splitlines()
    total = [int(x) for x in first[0].split()[1:]] if first else []
    cores = sum(1 for line in first if re.match(r"cpu\d+ ", line))
    per_core = [[int(x) for x in line.split()[1:]] for line in first if re.match(r"cpu\d+ ", line)]
    load = read("/proc/loadavg").split()
    temps = []
    for zone in sorted(os.listdir("/sys/class/thermal")) if os.path.isdir("/sys/class/thermal") else []:
        t = read(f"/sys/class/thermal/{zone}/temp").strip()
        if t.isdigit():
            temps.append(int(t) / 1000.0)
    return {"counters": total, "per_core": per_core, "cores": cores, "load": [float(x) for x in load[:3]] if load else [],
            "temp_c": max(temps) if temps else None}


def memory():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            info[key] = int(parts[0]) * 1024
    return {"total": info.get("MemTotal", 0), "available": info.get("MemAvailable", 0), "free": info.get("MemFree", 0),
            "cached": info.get("Cached", 0) + info.get("Buffers", 0), "swap_total": info.get("SwapTotal", 0), "swap_free": info.get("SwapFree", 0)}


def num(value):
    value = (value or "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def gpu():
    fields = "name,utilization.gpu,temperature.gpu,power.draw,power.limit,clocks.sm,clocks.max.sm,memory.used,memory.total,pstate,driver_version"
    out = sh(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 11:
            continue
        gpus.append({"name": p[0], "util": num(p[1]), "temp_c": num(p[2]), "power_w": num(p[3]), "power_limit_w": num(p[4]),
                     "clock_mhz": num(p[5]), "clock_max_mhz": num(p[6]), "mem_used_mb": num(p[7]), "mem_total_mb": num(p[8]),
                     "pstate": p[9], "driver": p[10]})
    apps = []
    out = sh(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 3 and p[0].isdigit():
            apps.append({"pid": int(p[0]), "name": p[1], "mem_mb": num(p[2])})
    return {"gpus": gpus, "apps": apps}


def disks():
    seen, out = set(), []
    paths = ["/", HOME] + [os.path.expanduser(d) for d in ARGS.get("models_dirs", [])]
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            st = os.statvfs(path)
        except OSError:
            continue
        key = (st.f_fsid if hasattr(st, "f_fsid") else path)
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": path, "total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize})
    return out


def network():
    counters = {}
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo" or name.startswith(("docker", "veth", "br-")):
            continue
        p = rest.split()
        if len(p) >= 9:
            counters[name] = {"rx": int(p[0]), "tx": int(p[8])}
    addrs = {}
    try:
        for item in json.loads(sh(["ip", "-j", "addr"]) or "[]"):
            name = item.get("ifname")
            if name in counters:
                v4 = [a["local"] + "/" + str(a.get("prefixlen", "")) for a in item.get("addr_info", []) if a.get("family") == "inet"]
                addrs[name] = {"v4": v4, "up": item.get("operstate") == "UP", "mtu": item.get("mtu"), "mac": item.get("address", "")}
    except Exception:
        pass
    for name in counters:
        speed = read(f"/sys/class/net/{name}/speed").strip()
        counters[name]["speed_mbps"] = int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None
        counters[name].update(addrs.get(name, {}))
        counters[name]["fabric"] = os.path.isdir(f"/sys/class/net/{name}/device/infiniband") or name.startswith(("enp1s0f", "enP2p1s0f"))
    return counters


def processes(gpu_pids):
    page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    rows = []
    servers = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        statm = read(f"/proc/{pid}/statm").split()
        if len(statm) < 2:
            continue
        rss = int(statm[1]) * page
        cmd = read(f"/proc/{pid}/cmdline").replace("\x00", " ").strip()
        if not cmd:
            continue
        rows.append((rss, int(pid), cmd))
        low = cmd.lower()
        if any(h in low for h in SERVER_HINTS) and ("serve" in low or "server" in low or "launch" in low or "ollama" in low):
            servers.append({"pid": int(pid), "cmd": cmd[:2000], "rss": rss})
    rows.sort(reverse=True)
    top = [{"pid": pid, "rss": rss, "cmd": cmd[:300], "gpu": pid in gpu_pids} for rss, pid, cmd in rows[:10]]
    return top, servers


def containers():
    fmt = ["ps", "--no-trunc", "--format", "{{json .}}"]
    try:
        res = subprocess.run(["docker", *fmt], capture_output=True, text=True, timeout=5)
        out = res.stdout if res.returncode == 0 else ""
        if res.returncode != 0:  # not in the docker group: passwordless sudo, never a prompt
            out = sh(["sudo", "-n", "docker", *fmt], timeout=5)
    except Exception:
        out = ""
    items = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        items.append({"id": d.get("ID", "")[:12], "name": d.get("Names", ""), "image": d.get("Image", ""), "status": d.get("Status", ""),
                      "labels": d.get("Labels", ""), "ports": d.get("Ports", ""), "command": (d.get("Command", "") or "")[:500]})
    return items


g = gpu()
top, servers = processes({a["pid"] for a in g["apps"]})
uname = os.uname()
print(json.dumps({
    "time": time.time(), "hostname": socket.gethostname(), "kernel": uname.release, "arch": uname.machine,
    "uptime_s": float((read("/proc/uptime").split() or ["0"])[0]), "home": HOME, "user": os.environ.get("USER", ""),
    "os": (re.search(r'PRETTY_NAME="([^"]+)"', read("/etc/os-release")) or [None, ""])[1],
    "cpu": cpu(), "memory": memory(), "gpu": g, "disks": disks(), "net": network(), "top": top, "servers": servers,
    "containers": containers(), "docker": bool(shutil.which("docker")),
}))
