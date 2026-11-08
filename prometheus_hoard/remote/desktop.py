# prometheus:desktop
"""Remote desktop of a Spark: one TigerVNC session running XFCE, started on demand and stopped when nobody looks at it.

Ops (``ARGS["op"]``): ``status``, ``start``, ``stop``. The VNC server listens on 127.0.0.1 only (``-localhost yes``); the app reaches it
through an SSH ``direct-tcpip`` channel. Everything lives in the Spark user's home (``~/.config/prometheus-desktop``) and the X display
that ARGS gives. While the session runs, a small watchdog (detached, own process) counts the established connections to the VNC port and
stops the session after ``idle_min`` minutes without any viewer, so it ends even if the app that started it is gone."""
import json, os, re, subprocess, sys, time

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
HOME = os.path.expanduser("~")
CONF = os.path.join(HOME, ".config", "prometheus-desktop")
ENV = dict(os.environ, LC_ALL="C", LANG="C")
DISPLAY = str(ARGS.get("display") or ":5")
NUM = int(DISPLAY.lstrip(":") or 5)
PORT = 5900 + NUM
LOG = os.path.join(CONF, "vncserver.log")

WATCHDOG = r'''
import subprocess, sys, time
port, display, idle_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
last = time.time()
def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}).stdout
while True:
    time.sleep(20)
    if not run(["ss", "-Hltn", "sport", "=", ":" + port]).strip():
        break
    if run(["ss", "-Htn", "state", "established", "sport", "=", ":" + port]).strip():
        last = time.time()
    elif time.time() - last > idle_s:
        subprocess.run(["vncserver", "-kill", display], capture_output=True)
        break
'''

XSTARTUP = """#!/bin/sh
# written by Prometheus's Hoard
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=XFCE
%s
exec dbus-launch --exit-with-session startxfce4
"""


def sh(cmd, timeout=20):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV)
        return res.returncode, res.stdout, res.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def missing_packages():
    pkgs = list(ARGS.get("packages") or [])
    if not pkgs:
        return []
    _, out, _ = sh(["dpkg-query", "-W", "-f", "${db:Status-Abbrev} ${Package}\\n"] + pkgs)
    have = {line.split()[-1] for line in out.splitlines() if line.startswith("ii")}
    return [p for p in pkgs if p not in have]


def vnc_pids():
    _, out, _ = sh(["pgrep", "-u", str(os.getuid()), "-f", r"Xtigervnc %s( |$)" % re.escape(DISPLAY)])
    return [int(x) for x in out.split() if x.isdigit()]


def listening():
    return bool(sh(["ss", "-Hltn", "sport", "=", ":%d" % PORT])[1].strip())


def viewers():
    return len([x for x in sh(["ss", "-Htn", "state", "established", "sport", "=", ":%d" % PORT])[1].splitlines() if x.strip()])


def watchdog_pid():
    try:
        pid = int(open(os.path.join(CONF, "watchdog.pid")).read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def stop_watchdog():
    pid = watchdog_pid()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def status():
    missing = missing_packages()
    pids = vnc_pids()
    up = listening()
    out = {"display": DISPLAY, "port": PORT, "installed": not missing, "missing": missing, "running": bool(pids) and up,
           "pid": pids[0] if pids else 0, "viewers": viewers() if up else 0, "has_password": os.path.isfile(os.path.join(CONF, "passwd")) and os.path.getsize(os.path.join(CONF, "passwd")) > 0,
           "watchdog": bool(watchdog_pid()), "user": os.environ.get("USER", "")}
    if out["pid"]:
        out["uptime_s"] = int((sh(["ps", "-o", "etimes=", "-p", str(out["pid"])])[1].strip() or "0"))
    if not out["installed"]:
        out["sudo_ok"] = sh(["sudo", "-n", "true"], timeout=8)[0] == 0
    return out


def fail(message, hint=""):
    print(json.dumps({"error": message, "code": "remote_failed", "hint": hint}))
    sys.exit(0)


def log_tail(n=1500):
    try:
        return open(LOG, errors="replace").read()[-n:]
    except OSError:
        return ""


def clean_stale():
    if vnc_pids():
        return
    for path in ("/tmp/.X%d-lock" % NUM, "/tmp/.X11-unix/X%d" % NUM):
        try:
            os.remove(path)
        except OSError:
            pass


def start():
    st = status()
    if not st["installed"]:
        fail("The remote desktop packages are not installed.", "Missing: " + ", ".join(st["missing"]))
    if st["running"]:
        print(json.dumps({**st, "already": True}))
        return
    if not st["has_password"]:
        fail("The session password is not set.")
    os.makedirs(CONF, mode=0o700, exist_ok=True)
    keymap = re.sub(r"[^a-zA-Z0-9_,()-]", "", str(ARGS.get("keymap") or ""))[:20]
    xstartup = os.path.join(CONF, "xstartup")
    with open(xstartup, "w") as fh:
        fh.write(XSTARTUP % (("setxkbmap %s 2>/dev/null || true" % keymap) if keymap else ""))
    os.chmod(xstartup, 0o755)
    # the panel's "default terminal" resolves through x-terminal-emulator, which may point at one that is not installed: pin the one we installed
    helpers = os.path.join(HOME, ".config", "xfce4", "helpers.rc")
    if not os.path.exists(helpers):
        os.makedirs(os.path.dirname(helpers), exist_ok=True)
        with open(helpers, "w") as fh:
            fh.write("TerminalEmulator=xfce4-terminal\nFileManager=Thunar\n")
    clean_stale()
    geometry = str(ARGS.get("geometry") or "1600x900")
    with open(LOG, "w") as logf:
        rc = subprocess.run(["vncserver", DISPLAY, "-localhost", "yes", "-SecurityTypes", "VncAuth", "-PasswordFile", os.path.join(CONF, "passwd"),
                             "-geometry", geometry, "-depth", "24", "-xstartup", xstartup], stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                            env=ENV, timeout=40).returncode
    deadline = time.time() + 15
    while time.time() < deadline and not listening():
        time.sleep(0.4)
    if not listening():
        fail("The VNC server did not start (exit %s)." % rc, log_tail())
    idle_min = float(ARGS.get("idle_min") or 0)
    stop_watchdog()
    if idle_min > 0:
        wd = os.path.join(CONF, "watchdog.py")
        with open(wd, "w") as fh:
            fh.write(WATCHDOG)
        proc = subprocess.Popen([sys.executable, wd, str(PORT), DISPLAY, str(idle_min * 60)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True, env=ENV)
        with open(os.path.join(CONF, "watchdog.pid"), "w") as fh:
            fh.write(str(proc.pid))
    print(json.dumps({**status(), "started": True, "log": log_tail(400)}))


def stop():
    stop_watchdog()
    with open(LOG, "a") as logf:
        sh(["vncserver", "-kill", DISPLAY], timeout=20)
    deadline = time.time() + 8
    while time.time() < deadline and (vnc_pids() or listening()):
        time.sleep(0.4)
    for sig in (15, 9):
        for pid in vnc_pids():
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        time.sleep(1.0)
    clean_stale()
    st = status()
    if st["running"] or vnc_pids():
        fail("The session did not stop.", log_tail())
    print(json.dumps({**st, "stopped": True}))


op = ARGS.get("op", "status")
if op == "status":
    print(json.dumps(status()))
elif op == "start":
    start()
elif op == "stop":
    stop()
else:
    fail("Unknown desktop operation: %s" % op)
