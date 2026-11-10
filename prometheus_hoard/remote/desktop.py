# prometheus:desktop
"""Remote desktop of a Spark: one TigerVNC session running XFCE, started on demand and stopped when nobody looks at it.

Ops (``ARGS["op"]``): ``status``, ``start``, ``stop``, ``clip_get`` and ``clip_set``. The VNC server listens on 127.0.0.1 only
(``-localhost yes``); the app reaches it through an SSH ``direct-tcpip`` channel. Everything lives in the Spark user's home, in one folder
per X display (``~/.config/prometheus-desktop/d<number>``), so two sessions on two displays never share a password, a log or a watchdog.

The screen size follows the browser window: the page asks for it with the RFB ``SetDesktopSize`` message, which Xtigervnc honours by
itself (RandR), so nothing here is involved. Text crosses the clipboard inside the RFB stream too. Images cannot (the VNC clipboard only
carries text), so ``clip_get`` reads the session's CLIPBOARD selection with GTK (image first, else text) and ``clip_set`` hands a PNG or a
text file that the app uploaded to a small detached GTK process that owns the selection until another program copies something. While the session runs, a small watchdog (detached, own process) counts the established connections to the VNC port and
stops the session after ``idle_min`` minutes without any viewer, so it ends even if the app that started it is gone."""
import json, os, re, subprocess, sys, time

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
HOME = os.path.expanduser("~")
ENV = dict(os.environ, LC_ALL="C", LANG="C")
DISPLAY = str(ARGS.get("display") or ":5")
NUM = int(DISPLAY.lstrip(":") or 5)
PORT = 5900 + NUM
CONF = os.path.join(HOME, ".config", "prometheus-desktop", "d%d" % NUM)
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

CLIP_GET = r'''
import base64, json, sys
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
ok, targets = cb.wait_for_targets()
names = [t.name() for t in (targets or [])] if ok else []
out = {"kind": "", "targets": names[:40]}
if any(n.startswith("image/") for n in names):
    pb = cb.wait_for_image()
    if pb is not None:
        done, buf = pb.save_to_bufferv("png", [], [])
        if done and len(buf) <= int(sys.argv[1]):
            out.update(kind="image", width=pb.get_width(), height=pb.get_height(), bytes=len(buf), png=base64.b64encode(buf).decode("ascii"))
        elif done:
            out.update(kind="image", width=pb.get_width(), height=pb.get_height(), bytes=len(buf), too_big=True)
if not out["kind"]:
    text = cb.wait_for_text()
    if text:
        out.update(kind="text", text=text[:1000000])
print(json.dumps(out))
'''

CLIP_OWN = r'''
import os, sys
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
kind, path, pidfile = sys.argv[1], sys.argv[2], sys.argv[3]
cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
if kind == "image":
    cb.set_image(GdkPixbuf.Pixbuf.new_from_file(path))
else:
    text = open(path, encoding="utf-8", errors="replace").read()
    cb.set_text(text, -1)
try:
    os.remove(path)
except OSError:
    pass
with open(pidfile, "w") as fh:
    fh.write(str(os.getpid()))
def lost(*_):
    Gtk.main_quit()
def watch():
    cb.connect("owner-change", lost)      # from now on, another program copying something ends this one
    return False
GLib.timeout_add(1500, watch)
GLib.timeout_add_seconds(12 * 3600, lost)
Gtk.main()
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


def desktop_dir():
    rc, out, _ = sh(["xdg-user-dir", "DESKTOP"], timeout=5)
    path = out.strip() if rc == 0 else ""
    if path and path != HOME and os.path.isdir(path):
        return path
    for name in ("Desktop", "Escritorio"):
        if os.path.isdir(os.path.join(HOME, name)):
            return os.path.join(HOME, name)
    return ""


def x_env():
    return dict(ENV, DISPLAY=DISPLAY, LC_ALL="C.UTF-8", LANG="C.UTF-8")


def clip_get():
    if not (vnc_pids() and listening()):
        fail("The desktop is not running.")
    limit = int(ARGS.get("max_bytes") or 20000000)
    try:
        res = subprocess.run([sys.executable, "-c", CLIP_GET, str(limit)], capture_output=True, text=True, timeout=15, env=x_env())
    except subprocess.TimeoutExpired:
        fail("Reading the desktop clipboard took too long.")
    lines = [x for x in res.stdout.splitlines() if x.strip()]
    if res.returncode != 0 or not lines:
        hint = (res.stderr or "")[-300:]
        if "No module named 'gi'" in hint or "Namespace Gtk not available" in hint:
            fail("This Spark has no GTK for Python, so the clipboard cannot be read from here.", "Install python3-gi and gir1.2-gtk-3.0.")
        fail("Could not read the desktop clipboard.", hint)
    print(lines[-1])


def clip_set():
    if not (vnc_pids() and listening()):
        fail("The desktop is not running.")
    kind = "image" if ARGS.get("kind") == "image" else "text"
    path = os.path.join(CONF, "clip-in.png" if kind == "image" else "clip-in.txt")
    if not os.path.isfile(path):
        fail("Nothing was uploaded for the clipboard.")
    pidfile = os.path.join(CONF, "clip-owner.pid")
    try:
        old = int(open(pidfile).read().strip())
        os.kill(old, 15)
    except (OSError, ValueError):
        pass
    script = os.path.join(CONF, "clipowner.py")
    with open(script, "w") as fh:
        fh.write(CLIP_OWN)
    try:
        os.remove(pidfile)
    except OSError:
        pass
    errlog = os.path.join(CONF, "clipowner.log")
    with open(errlog, "w") as err:
        proc = subprocess.Popen([sys.executable, script, kind, path, pidfile], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                start_new_session=True, env=x_env())
    deadline = time.time() + 8
    while time.time() < deadline and proc.poll() is None and not os.path.isfile(pidfile):
        time.sleep(0.1)
    if not os.path.isfile(pidfile):
        tail = open(errlog, errors="replace").read()[-300:]
        if "No module named 'gi'" in tail or "Namespace Gtk not available" in tail:
            fail("This Spark has no GTK for Python, so images cannot be put on its clipboard.", "Install python3-gi and gir1.2-gtk-3.0.")
        fail("Could not set the desktop clipboard.", tail)
    print(json.dumps({"ok": True, "kind": kind, "pid": proc.pid}))


def status():
    missing = missing_packages()
    pids = vnc_pids()
    up = listening()
    out = {"display": DISPLAY, "port": PORT, "installed": not missing, "missing": missing, "running": bool(pids) and up,
           "pid": pids[0] if pids else 0, "viewers": viewers() if up else 0, "has_password": os.path.isfile(os.path.join(CONF, "passwd")) and os.path.getsize(os.path.join(CONF, "passwd")) > 0,
           "watchdog": bool(watchdog_pid()), "user": os.environ.get("USER", ""), "desktop_dir": desktop_dir() if not missing else ""}
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
elif op == "clip_get":
    clip_get()
elif op == "clip_set":
    clip_set()
else:
    fail("Unknown desktop operation: %s" % op)
