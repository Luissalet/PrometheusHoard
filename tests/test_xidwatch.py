import shlex
import json
import time

from prometheus_hoard.errors import SparkError
from prometheus_hoard.transport import RunResult
from prometheus_hoard.xidwatch import KEEP, XidWatch, classify, journal_command, parse_stamp

XID79 = "2026-10-09T10:15:22+0200 spark-c6fe kernel: NVRM: Xid (PCI:000f:01:00): 79, pid=1234, name=VLLM::Worker, GPU has fallen off the bus."
RESET = "2026-10-09T10:15:23+0200 spark-c6fe kernel: NVRM: GPU at PCI:000f:01:00: NV_ERR_GPU_IN_FULLCHIP_RESET"
GSP = "2026-10-09T10:15:24+0200 spark-c6fe kernel: NVRM: _kgspRpcRecvPoll: GSP RPC timeout, function 76"
NOISE = [
    "2026-10-09T10:15:20+0200 spark-c6fe kernel: eth0: link up",
    "2026-10-09T10:15:21+0200 spark-c6fe kernel: nvidia-modeset: Loaded NVIDIA UNIX Open Kernel Module",
    "2026-10-09T10:15:21+0200 spark-c6fe kernel: NVRM: GSP firmware loaded",             # GSP without an error word
    "2026-10-09T10:15:21+0200 spark-c6fe kernel: usb 1-1: error -71 reading descriptor",    # an error without GSP or Xid
    "-- Boot 0123456789abcdef --",
]


# ------------------------------------------------------------------ the parser
def test_classify_the_three_kinds_and_ignore_the_rest():
    assert classify(XID79) == {"kind": "xid", "xid": 79, "pci": "PCI:000f:01:00", "meaning": "GPU has fallen off the bus"}
    assert classify("kernel: NVRM: Xid (PCI:0000:0f:00): 999, whatever")["xid"] == 999
    assert classify(RESET)["kind"] == "fullchip_reset" and classify("x FULLCHIP_RESET y")["kind"] == "fullchip_reset"
    assert classify(GSP)["kind"] == "gsp"
    assert classify("kernel: NVRM: GSP: failed to boot")["kind"] == "gsp" and classify("kernel: GSP command timed out")["kind"] == "gsp"
    assert all(classify(line) is None for line in NOISE)


def test_parse_stamp_with_and_without_offset():
    epoch, local = parse_stamp(XID79)
    assert local == "2026-10-09 10:15:22"
    a, _ = parse_stamp("2026-10-09T10:15:22+0200 x")
    b, _ = parse_stamp("2026-10-09T10:15:22+02:00 x")
    c, _ = parse_stamp("2026-10-09T08:15:22Z x")
    d, _ = parse_stamp("2026-10-09T08:15:22.123456+0000 x")
    assert a == b == c == epoch and abs(d - c) < 1
    assert parse_stamp("not a stamp") == (None, "")
    assert parse_stamp("-- No entries --") == (None, "")


def test_journal_command_first_scan_and_with_cursor():
    assert "--since '2 minutes ago'" in journal_command("") and "journalctl -k" in journal_command("") and "-o short-iso" in journal_command("")
    assert "--since '2026-10-09 10:15:17'" in journal_command("2026-10-09 10:15:17")
    hostile = shlex.split(journal_command("x'; rm -rf /"))
    assert hostile[hostile.index("--since") + 1] == "x'; rm -rf /"           # one argument, whatever it holds


# ------------------------------------------------------------------ the watcher on the invented cluster
def make(services, **kw):
    sent = []
    kw.setdefault("notifier", lambda title, body, **k: sent.append((title, body, k)) or {"ok": True})
    services.xid.notifier = kw["notifier"]
    return services.xid, sent


def test_scan_finds_events_and_keeps_evidence(services, world, call):
    xid, sent = make(services)
    world.kernel_log["spark2"] = NOISE[:2] + [XID79, RESET, GSP] + NOISE[2:]
    found = xid.scan_once()
    assert [(e["node"], e["kind"], e["xid"]) for e in found] == [("spark2", "xid", 79), ("spark2", "fullchip_reset", None), ("spark2", "gsp", None)]
    assert found[0]["line"] == XID79 and found[0]["stamp"] == "2026-10-09 10:15:22" and found[0]["t"] > 1.7e9
    saved = json.loads(services.config.xid_path.read_text())
    assert len(saved["events"]) == 3 and "spark2" in saved["cursors"]
    # the same lines again (the overlap of the cursor) are not new
    assert xid.scan_once() == []
    assert len(xid.snapshot(hours=1e6)["events"]) == 3
    # only a Spark with a problem notified, once, with the evidence
    assert len(sent) == 1 and sent[0][0].startswith("Spark2: GPU error") and "Xid 79" in sent[0][0]
    assert XID79 in sent[0][1] and sent[0][2]["priority"] == "high" and sent[0][2]["group"] == "gpu-xid"
    assert all(e["notified"] is True for e in found)


def test_cursor_moves_to_the_sparks_clock_and_is_used(services, world):
    xid, _ = make(services)
    xid.scan_once()
    cursor = xid.cursors["spark1"]
    assert len(cursor) == 19 and cursor <= time.strftime("%Y-%m-%d %H:%M:%S")
    xid.scan_once()
    sent_cmds = [c for n, c in world.commands if n == "spark1" and c.startswith("journalctl")]
    assert "--since '2 minutes ago'" in sent_cmds[0] and f"--since '{cursor}'" in sent_cmds[1]
    assert xid.status["spark1"]["ok"] is True and xid.status["spark1"]["cursor"]


def test_events_survive_a_restart_without_repeating(services, world, tmp_path):
    xid, _ = make(services)
    world.kernel_log["spark1"] = [XID79]
    assert len(xid.scan_once()) == 1
    again = XidWatch(services.cluster.enabled, services.config.xid_path, lambda: {"enabled": True, "interval_s": 60.0, "notify": False})
    assert len(again.events) == 1 and again.cursors == xid.cursors
    assert again.scan_once() == []


def test_unreadable_journal_is_reported_and_keeps_the_cursor(services, world):
    xid, _ = make(services)
    xid.scan_once()
    cursor = xid.cursors["spark3"]
    real = services.cluster.node("spark3").transport.run

    def denied(cmd, **kw):
        return RunResult(0, "@@RC=1\n@@NOW=2030-01-01 00:00:00\n", "Failed to open journal: permission denied")

    services.cluster.node("spark3").transport.run = denied
    xid.scan_once()
    assert xid.status["spark3"]["ok"] is False and "permission" in xid.status["spark3"]["error"] and xid.cursors["spark3"] == cursor
    assert services.xid.summary()["unreadable"] == ["spark3"]

    def down(cmd, **kw):
        raise SparkError("unreachable", "spark3 is not answering over SSH")

    services.cluster.node("spark3").transport.run = down
    xid.scan_once()
    assert xid.status["spark3"]["error"].startswith("unreachable")
    services.cluster.node("spark3").transport.run = real
    xid.scan_once()
    assert xid.status["spark3"]["ok"] is True and xid.summary()["unreadable"] == []

    def silent_denial(cmd, **kw):
        return RunResult(0, "No journal files were opened due to insufficient permissions.\n@@RC=0\n@@NOW=2030-01-01 00:00:00\n", "")

    services.cluster.node("spark3").transport.run = silent_denial
    xid.scan_once()
    assert xid.status["spark3"]["ok"] is False


def test_notification_failure_and_switch_off(services, world):
    xid, _ = make(services, notifier=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hub down")))
    world.kernel_log["spark1"] = [XID79]
    found = xid.scan_once()
    assert found[0]["notified"] is False                       # kept as evidence all the same
    services.settings.update({"xid_notify": False})
    world.kernel_log["spark1"] = [XID79, RESET]
    assert xid.scan_once()[0]["notified"] is None              # not asked: the setting says no
    assert xid.snapshot()["notify"] is False


def test_events_are_bounded(services, world):
    xid, _ = make(services)
    world.kernel_log["spark1"] = [f"2026-10-09T10:{i // 60:02d}:{i % 60:02d}+0200 h kernel: NVRM: Xid (PCI:0:0): {i}, x" for i in range(KEEP + 40)]
    xid.scan_once()
    assert len(xid.events) == KEEP and xid.events[-1]["xid"] == KEEP + 39 and len(json.loads(services.config.xid_path.read_text())["events"]) == KEEP


def test_xid_events_tool_filters(call, services, world):
    services.xid.notifier = None
    world.kernel_log["spark1"] = [XID79]
    world.kernel_log["spark3"] = [GSP]
    services.xid.scan_once()
    everything = call("xid_events")
    assert everything["total"] == 2 and everything["counts"] == {"spark1": 1, "spark3": 1} and everything["enabled"] is True
    assert [e["node"] for e in call("xid_events", {"node": "spark3"})["events"]] == ["spark3"]
    assert set(everything["nodes"]) == {"spark1", "spark2", "spark3"}


def test_overview_carries_the_summary(call, services, world):
    services.xid.notifier = None
    assert call("sparks_overview")["xid"] == {"enabled": True, "count_24h": 0, "by_node": {}, "last": None, "unreadable": []}
    world.kernel_log["spark2"] = [XID79]
    services.xid.clock = lambda: parse_stamp(XID79)[0] + 60       # a minute after the line was written
    services.xid.scan_once()
    xid = call("sparks_overview")["xid"]
    assert xid["count_24h"] == 1 and xid["by_node"] == {"spark2": 1} and xid["last"]["xid"] == 79


def test_settings_switch_the_watcher_off_and_bound_the_interval(call, services):
    call("settings_set", {"xid_watch": False, "xid_interval_s": 1, "xid_notify": False})
    s = call("settings_get")
    assert s["xid_watch"] is False and s["xid_interval_s"] == 15.0 and s["xid_notify"] is False
    snap = call("xid_events")
    assert snap["enabled"] is False and snap["interval_s"] == 15.0
    call("settings_set", {"xid_interval_s": 99999})
    assert call("settings_get")["xid_interval_s"] == 3600.0


def test_the_thread_scans_only_while_enabled(services, world):
    xid, _ = make(services)
    state = {"on": False}
    xid.options = lambda: {"enabled": state["on"], "interval_s": 15.0, "notify": False}
    world.kernel_log["spark1"] = [XID79]
    xid.start()
    try:
        time.sleep(1.6)
        assert xid.events == []                                     # disabled: nothing read
        state["on"] = True
        deadline = time.time() + 5
        while not xid.events and time.time() < deadline:
            time.sleep(0.1)
        assert len(xid.events) == 1
    finally:
        xid.stop()
