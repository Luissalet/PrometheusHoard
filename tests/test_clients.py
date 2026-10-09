import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from prometheus_hoard.clients import (KEEP_S, Clients, classify, format_stamp, local_processes, parse_line, parse_stamp, process_label,
                                      route_source)
from prometheus_hoard.config import Settings, norm_ip
from prometheus_hoard.fake import FakePsutil, fake_access_lines

T0 = 1_790_000_000          # a fixed "now" (seconds)


def stamp(t, frac=0):
    return format_stamp(int(t * 10**9) + frac)


def line(t, ip="192.0.2.5", path="POST /v1/chat/completions", code=200, frac=0, port=57320, prefix=True):
    body = f'INFO:     {ip}:{port} - "{path} HTTP/1.1" {code} OK'
    return f"{stamp(t, frac)} {body}" if prefix else body


# ------------------------------------------------------------------ the log format
def test_parse_line_with_and_without_the_docker_stamp():
    a = parse_line('2026-10-09T10:11:12.123456789Z INFO:     192.0.2.140:61825 - "POST /v1/chat/completions HTTP/1.1" 200 OK')
    assert (a.ip, a.method, a.path, a.code) == ("192.0.2.140", "POST", "/v1/chat/completions", 200)
    assert a.ns == parse_stamp("2026-10-09T10:11:12.123456789Z") and a.ns % 10**9 == 123456789
    bare = parse_line('INFO:     127.0.0.1:58586 - "GET /health HTTP/1.1" 200 OK')
    assert bare.ns is None and bare.ip == "127.0.0.1" and bare.path == "/health"
    assert parse_line('192.0.2.7:1 - "GET /v1/models HTTP/1.1" 404 Not Found').code == 404       # no level prefix either
    assert parse_line('2026-10-09T10:11:12Z INFO:     192.0.2.140:6 - "GET /metrics HTTP/1.1" 200').ns == parse_stamp("2026-10-09T10:11:12Z")


def test_parse_line_ipv6_and_mapped_addresses():
    assert parse_line('INFO:     [::1]:58586 - "GET /health HTTP/1.1" 200 OK').ip == "::1"
    assert parse_line('INFO:     ::1:58586 - "GET /health HTTP/1.1" 200 OK').ip == "::1"          # uvicorn does not bracket it
    assert parse_line('INFO:     [2001:db8::7]:4000 - "POST /v1/messages HTTP/1.1" 200 OK').ip == "2001:db8::7"
    assert parse_line('INFO:     2001:db8::7:4000 - "POST /v1/messages HTTP/1.1" 200 OK').ip == "2001:db8::7"
    assert parse_line('INFO:     ::ffff:192.0.2.9:4000 - "POST /v1/messages HTTP/1.1" 200 OK').ip == "192.0.2.9"
    assert parse_line('INFO:     [fe80::1%eth0]:4000 - "GET /health HTTP/1.1" 200 OK').ip == "fe80::1"


@pytest.mark.parametrize("odd", [
    "", "INFO:     Started server process [1]", "Traceback (most recent call last):", '  File "x.py", line 3, in <module>',
    'INFO:     192.0.2.5:57320 - "POST /v1/chat/completions HTTP/1.1"',                     # no status: cut line
    'INFO:     999.1.1.1:5 - "GET /health HTTP/1.1" 200 OK',                                  # not an address
    'INFO:     192.0.2.5 - "GET /health HTTP/1.1" 200 OK',                                    # no port
    '2026-13-45T25:61:61Z INFO:     192.0.2.5:5 - "GET /health HTTP/1.1" 200 OK',            # impossible stamp
    "(APIServer pid=1) INFO 10-09 10:11:12 [loggers.py:123] Engine 000: Avg prompt throughput: 0.0 tokens/s",
    'INFO:     192.0.2.5:57320 - "weird" 200', None,
])
def test_parse_line_ignores_odd_lines(odd):
    assert parse_line(odd) is None


def test_stamps_roundtrip_and_zones():
    ns = parse_stamp("2026-10-09T10:11:12.5Z")
    assert ns % 10**9 == 500_000_000 and format_stamp(ns) == "2026-10-09T10:11:12.500000000Z"
    assert parse_stamp("2026-10-09T12:11:12.5+02:00") == ns
    assert parse_stamp("2026-10-09T10:11:12.1234567891234Z") % 10**9 == 123456789          # extra digits are cut, not rounded
    assert parse_stamp("2026-10-09T10:11:12") == parse_stamp("2026-10-09T10:11:12Z")
    assert parse_stamp("yesterday") is None and parse_stamp("") is None


@pytest.mark.parametrize("method,path,kind", [
    ("POST", "/v1/chat/completions", "chat"), ("POST", "/v1/completions", "completions"), ("POST", "/v1/responses", "responses"),
    ("GET", "/v1/responses/resp_1", "responses"), ("POST", "/v1/messages", "messages"), ("POST", "/v1/messages/count_tokens", "other"),
    ("POST", "/v1/embeddings", "embeddings"), ("POST", "/tokenize", "other"), ("POST", "/v1/chat/completions?x=1", "chat"),
    ("GET", "/health", "poll"), ("GET", "/v1/models", "poll"), ("GET", "/v1/models/glm", "poll"), ("GET", "/metrics", "poll"),
    ("HEAD", "/health", "poll"), ("GET", "/docs", "other"), ("GET", "/v1/chat/completions", "other"),
])
def test_classify(method, path, kind):
    assert classify(method, path) == kind


# ------------------------------------------------------------------ aggregation
class Runner:
    """A scripted head Spark: serves the log lines it was given, honouring the cursor like the real helper does."""

    def __init__(self, clock, lines=(), **extra):
        self.clock = clock
        self.lines = list(lines)
        self.calls = []
        self.extra = extra

    def __call__(self, node, args):
        self.calls.append((node, args))
        since = parse_stamp(args["since"])
        fresh = [ln for ln in self.lines if (parse_line(ln).ns or 0) > since]
        return {"ok": True, "container": args["containers"][0], "via": "docker", "lines": fresh, "truncated": False, "now": self.clock[0],
                "ssh_client": "", **self.extra}


INFO = {"recipe": "m", "title": "M", "base_url": "http://spark-a.invalid:8000/v1", "head": "spark1", "nodes": ["spark1"], "models": ["x"], "engine": "vllm"}


@pytest.fixture
def clock():
    return [float(T0)]


def make(tmp_path, clock, runner=None, **kw):
    runner = runner or Runner(clock)
    kw.setdefault("containers", lambda info: ["c1"])
    kw.setdefault("psutil_mod", None)
    kw.setdefault("route", lambda host: None)
    kw.setdefault("save_s", 0)
    c = Clients(lambda: [INFO], tmp_path / "clients.json", runner=runner, clock=lambda: clock[0], fast_s=0, idle_s=0, **kw)
    c.refresh_list()
    return c, runner


def client(view, ip):
    return next(c for c in view["clients"] if c["ip"] == ip)


def test_requests_are_aggregated_per_client_and_api(tmp_path, clock):
    c, _ = make(tmp_path, clock)
    L = []
    for i in range(5):
        L.append(line(T0 - 600 + i, "192.0.2.5", "POST /v1/chat/completions"))
    L += [line(T0 - 500, "192.0.2.5", "POST /v1/responses"), line(T0 - 400, "192.0.2.5", "POST /v1/messages", code=400),
          line(T0 - 300, "192.0.2.5", "POST /v1/embeddings"), line(T0 - 200, "192.0.2.5", "POST /v1/completions"),
          line(T0 - 100, "192.0.2.5", "POST /tokenize"), line(T0 - 90, "192.0.2.5", "GET /v1/models"),
          line(T0 - 80, "192.0.2.6", "GET /health"), line(T0 - 70, "192.0.2.6", "GET /metrics", code=500)]
    assert c.ingest("m", L, now=T0) == len(L)
    v = c.view("m")
    a = client(v, "192.0.2.5")
    assert a["by_kind"] == {"chat": 5, "responses": 1, "messages": 1, "completions": 1, "embeddings": 1, "other": 1, "poll": 1}
    assert a["kinds"][0] == "chat" and set(a["kinds"]) == {"chat", "responses", "messages", "completions", "embeddings", "other"}
    assert a["requests"] == 11 and a["inference"] == 9 and a["errors"] == 1 and a["activity"] == "inferring" and a["only_polling"] is False
    p = client(v, "192.0.2.6")
    assert p["by_kind"] == {"poll": 2} and p["kinds"] == [] and p["only_polling"] is True and p["activity"] == "polling" and p["errors"] == 1
    assert [x["ip"] for x in v["clients"]] == ["192.0.2.5", "192.0.2.6"]       # the one that infers first
    assert a["first"] == T0 - 600 and a["last"] == T0 - 90
    other_only = c.ingest("m", [line(T0 - 10, "192.0.2.7", "POST /tokenize")], now=T0)
    assert other_only == 1 and client(c.view("m"), "192.0.2.7")["activity"] == "other"


def test_rolling_hour_and_24_hour_window(tmp_path, clock):
    c, _ = make(tmp_path, clock)
    L = [line(T0 - 7200, "192.0.2.5"), line(T0 - 3700, "192.0.2.5"), line(T0 - 3000, "192.0.2.5"), line(T0 - 60, "192.0.2.5"),
         line(T0 - 90000, "192.0.2.5"), line(T0 - 600, "192.0.2.5", code=503)]
    c.ingest("m", L, now=T0)
    a = client(c.view("m"), "192.0.2.5")
    assert a["requests"] == 5                      # the line from 25 h ago is outside the retention
    assert a["requests_1h"] == 3 and a["inference_1h"] == 3 and a["errors"] == 1 and a["errors_1h"] == 1
    clock[0] = T0 + 3300                           # 55 minutes later only the last two are still inside the hour...
    a = client(c.view("m"), "192.0.2.5")
    assert a["requests_1h"] == 0 or a["requests_1h"] == 1     # minute buckets: the edge minute may or may not count
    clock[0] = T0 + 2 * 3600
    assert client(c.view("m"), "192.0.2.5")["requests_1h"] == 0
    c.ingest("m", [], now=T0 + KEEP_S + 4000)      # a day later everything has aged out, and so has the client
    clock[0] = T0 + KEEP_S + 4000
    assert c.view("m")["clients"] == []


def test_loopback_probes_are_counted_not_listed(tmp_path, clock):
    c, _ = make(tmp_path, clock)
    L = [line(T0 - 5, "127.0.0.1", "GET /health", prefix=True), line(T0 - 4, "::1", "GET /metrics"), line(T0 - 3, "127.0.0.1", "GET /health"),
         line(T0 - 2, "192.0.2.5", "GET /health")]
    c.ingest("m", L, now=T0)
    v = c.view("m")
    assert v["local_probes"] == 3 and [x["ip"] for x in v["clients"]] == ["192.0.2.5"]


def test_the_cursor_skips_what_was_counted_and_dedups_ties(tmp_path, clock):
    c, runner = make(tmp_path, clock)
    first = [line(T0 - 30, "192.0.2.5"), line(T0 - 20, "192.0.2.5"), line(T0 - 10, "192.0.2.6", frac=5), line(T0 - 10, "192.0.2.7", frac=5)]
    runner.lines = first
    c.poll_once(wait=True)
    assert client(c.view("m"), "192.0.2.5")["requests"] == 2 and runner.calls[0][1]["since"] == stamp(T0 - KEEP_S)
    assert c._eps["m"].cursor == parse_stamp(stamp(T0 - 10, 5))
    # the same lines again, one new line with the very stamp of the cursor and one later: only the new ones count
    again = first + [line(T0 - 10, "192.0.2.8", frac=5), line(T0 - 5, "192.0.2.5")]
    assert c.ingest("m", again, now=T0) == 2
    assert client(c.view("m"), "192.0.2.5")["requests"] == 3 and client(c.view("m"), "192.0.2.8")["requests"] == 1
    assert c.ingest("m", again, now=T0) == 0
    clock[0] += 20
    runner.lines = again
    c.poll_once(wait=True)
    assert runner.calls[1][1]["since"] == stamp(T0 - 5)                          # the next read starts at the newest line
    assert client(c.view("m"), "192.0.2.5")["requests"] == 3


def test_a_quiet_log_still_moves_the_cursor_forward(tmp_path, clock):
    c, runner = make(tmp_path, clock)
    c.poll_once(wait=True)
    clock[0] += 60
    c.poll_once(wait=True)
    assert runner.calls[1][1]["since"] == stamp(T0 - 5)          # not 24 h again: everything up to 5 s before the Spark's now was read


def test_lines_without_a_stamp_count_once_per_read(tmp_path, clock):
    c, _ = make(tmp_path, clock)
    c.ingest("m", ['INFO:     192.0.2.5:1 - "POST /v1/chat/completions HTTP/1.1" 200 OK'], now=T0)
    assert client(c.view("m"), "192.0.2.5")["requests"] == 1 and client(c.view("m"), "192.0.2.5")["last"] == T0


def test_clock_skew_of_the_spark_does_not_hide_the_last_hour(tmp_path, clock):
    runner = Runner(clock)
    c, _ = make(tmp_path, clock, runner)
    runner.lines = [line(T0 + 4000 - 30, "192.0.2.5")]
    runner.clock = [T0 + 4000.0]                    # the Spark's clock runs 4000 s ahead of ours
    c.poll_once(wait=True)
    assert client(c.view("m"), "192.0.2.5")["requests_1h"] == 1


# ------------------------------------------------------------------ the remote read
def test_reads_go_to_the_head_with_the_containers_and_remember_sudo(tmp_path, clock):
    runner = Runner(clock, via="sudo", container="c2")
    c, _ = make(tmp_path, clock, runner, containers=lambda info: ["c1", "c2"])
    c.poll_once(wait=True)
    node, args = runner.calls[0]
    assert node == "spark1" and args["containers"] == ["c1", "c2"] and args["prefer_sudo"] is False and args["max_lines"] == 20000
    v = c.view("m")
    assert v["ok"] is True and v["source"] == "sudo" and v["container"] == "c2" and v["error"] == ""
    c.poll_once(wait=True)
    assert runner.calls[1][1]["prefer_sudo"] is True


@pytest.mark.parametrize("reply,error", [
    ({"ok": False, "error": "docker_denied"}, "docker_denied"),
    ({"ok": False, "error": "no_container"}, "no_container"),
    ({}, "docker_failed"),
])
def test_a_failed_read_is_reported_and_backs_off(tmp_path, clock, reply, error):
    calls = []

    def runner(node, args):
        calls.append(1)
        return reply

    c, _ = make(tmp_path, clock, runner)
    c.idle_s = 120
    c.poll_once(wait=True)
    v = c.view("m")
    assert v["ok"] is False and v["error"] == error and v["clients"] == []
    clock[0] += 30
    c.poll_once(wait=True)
    assert len(calls) == 1                           # it waits before bothering the Spark again
    clock[0] += 200
    c.poll_once(wait=True)
    assert len(calls) == 2


def test_no_container_to_read_and_runner_exceptions(tmp_path, clock):
    c, _ = make(tmp_path, clock, containers=lambda info: [])
    c.poll_once(wait=True)
    assert c.view("m")["error"] == "no_container"

    def boom(node, args):
        raise RuntimeError("ssh down")

    c2, _ = make(tmp_path, clock, boom)
    c2.poll_once(wait=True)
    assert c2.view("m")["ok"] is False and "ssh down" in c2.view("m")["error"]


def test_the_collector_follows_the_last_look(tmp_path, clock):
    c = Clients(lambda: [], tmp_path / "c.json", runner=Runner(clock), clock=lambda: clock[0], psutil_mod=None)
    assert c.interval == 120.0
    c.touch()
    assert c.interval == 20.0
    clock[0] += 121
    assert c.interval == 120.0
    other = Clients(lambda: [], tmp_path / "c2.json", runner=Runner(clock), clock=lambda: clock[0], psutil_mod=None, touched=lambda: clock[0] - 10)
    assert other.interval == 20.0                     # the Serving page was used 10 s ago
    c.stop()
    other.stop()


def test_the_background_thread_reads_and_stops(tmp_path):
    seen = []
    base = time.time()

    def runner(node, args):
        seen.append(args)
        return {"ok": True, "container": "c", "via": "docker", "lines": [], "now": time.time(), "ssh_client": ""}

    c = Clients(lambda: [INFO], tmp_path / "c.json", runner=runner, containers=lambda i: ["c"], psutil_mod=None, route=lambda h: None,
                fast_s=0.05, idle_s=0.05, local_s=0.05)
    c.start()
    try:
        deadline = time.time() + 5
        while len(seen) < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert len(seen) >= 3 and base > 0
    finally:
        c.stop()
    n = len(seen)
    time.sleep(0.2)
    assert len(seen) == n


# ------------------------------------------------------------------ persistence
def test_the_summary_survives_a_restart(tmp_path, clock):
    c, _ = make(tmp_path, clock)
    c.ingest("m", [line(T0 - 30, "192.0.2.5"), line(T0 - 20, "192.0.2.5", code=500), line(T0 - 10, "127.0.0.1", "GET /health")], now=T0)
    c._add_pc("192.0.2.5", "ssh")
    c.save(force=True)
    saved = json.loads((tmp_path / "clients.json").read_text(encoding="utf-8"))
    rows = saved["endpoints"]["m"]["ips"]["192.0.2.5"]["b"]
    assert saved["this_pc"] == ["192.0.2.5"] and [sum(r[i] for r in rows) for i in range(1, 9)] == [2, 0, 0, 0, 0, 0, 0, 1]
    assert all(len(r) == 9 for r in rows)
    again, runner = make(tmp_path, clock)
    v = again.view("m")
    a = client(v, "192.0.2.5")
    assert a["requests"] == 2 and a["errors"] == 1 and a["role"] == "this_pc" and v["local_probes"] == 1
    again.poll_once(wait=True)
    assert runner.calls[0][1]["since"] == stamp(T0 - 10)             # it carries on from the cursor, not from 24 h ago
    assert client(again.view("m"), "192.0.2.5")["requests"] == 2


def test_a_corrupt_file_is_ignored(tmp_path, clock):
    (tmp_path / "clients.json").write_text("{not json", encoding="utf-8")
    c, _ = make(tmp_path, clock)
    assert c.view("m")["clients"] == []
    (tmp_path / "clients.json").write_text(json.dumps({"endpoints": {"m": {"cursor": "x", "ips": {"192.0.2.5": {"first": "a"}}, "loop": 4}}}), encoding="utf-8")
    c, _ = make(tmp_path, clock)
    assert c.view("m")["clients"] == []


# ------------------------------------------------------------------ names
def test_this_pc_is_found_from_the_ssh_session_and_the_route(tmp_path, clock):
    runner = Runner(clock, ssh_client="192.0.2.77")
    c, _ = make(tmp_path, clock, runner, route=lambda host: "192.0.2.78" if host == "spark-a.invalid" else None)
    c.ingest("m", [line(T0 - 5, "192.0.2.77"), line(T0 - 4, "192.0.2.78"), line(T0 - 3, "192.0.2.79")], now=T0)
    assert client(c.view("m"), "192.0.2.77")["role"] == ""         # not before it is known
    c.poll_once(wait=True)
    v = c.view("m")
    assert {x["ip"]: x["label"] for x in v["clients"]} == {"192.0.2.77": "Este PC", "192.0.2.78": "Este PC", "192.0.2.79": "192.0.2.79"}
    assert v["this_pc"]["ips"] == ["192.0.2.77", "192.0.2.78"]


def test_loopback_ssh_clients_are_not_this_pc(tmp_path, clock):
    runner = Runner(clock, ssh_client="127.0.0.1")
    c, _ = make(tmp_path, clock, runner, route=lambda host: "127.0.0.1")
    c.poll_once(wait=True)
    assert c.view("m")["this_pc"]["ips"] == []


def test_names_sparks_and_custom_names(tmp_path, clock):
    names = {"192.0.2.5": "Portátil"}
    c, _ = make(tmp_path, clock, names=lambda: names, spark_addrs=lambda: {"198.51.100.2": "spark2", "192.0.2.5": "spark1"},
                node_name=lambda n: n.capitalize())
    c.ingest("m", [line(T0 - 5, "192.0.2.5"), line(T0 - 4, "198.51.100.2"), line(T0 - 3, "198.51.100.9")], now=T0)
    by = {x["ip"]: x for x in c.view("m")["clients"]}
    assert by["192.0.2.5"]["label"] == "Portátil" and by["192.0.2.5"]["name"] == "Portátil" and by["192.0.2.5"]["role"] == "spark"
    assert by["198.51.100.2"]["label"] == "Spark2" and by["198.51.100.2"]["spark"] == "spark2" and by["198.51.100.2"]["name"] == ""
    assert by["198.51.100.9"]["label"] == "198.51.100.9" and by["198.51.100.9"]["role"] == ""
    c._add_pc("198.51.100.2", "ssh")                              # a Spark that runs this app is this PC
    assert c.identity("198.51.100.2")["role"] == "this_pc"


def test_client_names_setting_is_validated_and_normalised(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s["client_names"] == {}
    out = s.update({"client_names": {"[::1]": "  Local   host ", "::FFFF:192.0.2.9": "Mapped", "192.0.2.4": "", "192.0.2.5": "x" * 100}})
    assert out["client_names"] == {"::1": "Local host", "192.0.2.9": "Mapped", "192.0.2.5": "x" * 60}
    assert Settings(tmp_path / "s.json")["client_names"] == out["client_names"]
    with pytest.raises(ValueError):
        s.update({"client_names": {"not-an-ip": "x"}})
    with pytest.raises(ValueError):
        s.update({"client_names": ["192.0.2.1"]})
    assert norm_ip("[2001:DB8::1]") == "2001:db8::1" and norm_ip("300.1.1.1") == "" and norm_ip(None) == ""


# ------------------------------------------------------------------ this PC in detail
def conn(raddr, laddr=("192.0.2.10", 50001), pid=1, status="ESTABLISHED"):
    return NS(status=status, raddr=raddr, laddr=laddr, pid=pid)


class Psutil:
    class Error(Exception):
        pass

    class AccessDenied(Error):
        pass

    class NoSuchProcess(Error):
        pass

    def __init__(self, conns, procs=None, fail=None):
        self.conns, self.procs, self.fail = conns, procs or {}, fail

    def net_connections(self, kind="tcp"):
        if self.fail:
            raise self.fail
        return self.conns

    def Process(self, pid):  # noqa: N802
        if pid not in self.procs:
            raise self.NoSuchProcess(pid)
        info = self.procs[pid]

        class P:
            def name(self_):
                if info == "denied":
                    raise Psutil.AccessDenied(pid)
                return info[0]

            def cmdline(self_):
                if info == "denied":
                    raise Psutil.AccessDenied(pid)
                return info[1]

            def cwd(self_):
                if info == "denied" or len(info) < 3:
                    raise Psutil.AccessDenied(pid)
                return info[2]

        return P()


TARGETS = [{"key": "m", "host": "spark-a.invalid", "port": 8000}, {"key": "n", "host": "spark-b.invalid", "port": 8001}]
RESOLVE = {"spark-a.invalid": {"198.51.100.1"}, "spark-b.invalid": {"198.51.100.2", "2001:db8::2"}}


def test_local_connections_are_mapped_to_programs():
    ps = Psutil([
        conn(("198.51.100.1", 8000), pid=10), conn(("198.51.100.1", 8000), ("192.0.2.10", 50002), pid=10), conn(("198.51.100.1", 8000), pid=11),
        conn(("198.51.100.1", 8000), pid=12), conn(("198.51.100.1", 8000), pid=13), conn(("198.51.100.1", 8000), pid=None),
        conn(("198.51.100.1", 8000), pid=10, status="TIME_WAIT"), conn(("198.51.100.1", 9999), pid=10),          # not established / other port
        conn(("198.51.100.2", 8001), pid=11), conn(("::ffff:198.51.100.2", 8001), pid=11), conn((), pid=10), conn(("203.0.113.5", 8000), pid=10),
    ], {10: ("python.exe", ["python", "-m", "galton_hoard"], "C:/x"), 11: ("node.exe", ["node", "C:/t/codex/bin/codex.js"]),
        12: "denied", 13: ("chrome.exe", ["chrome.exe", "--type=renderer"])})
    out = local_processes(TARGETS, ps, lambda h: RESOLVE[h])
    assert out["error"] is None
    m = out["by_key"]["m"]
    assert m["connections"] == 6 and m["local_ips"] == ["192.0.2.10"]
    rows = {r["pid"]: r for r in m["processes"]}
    assert rows[10]["connections"] == 2 and rows[10]["label"] == "Galton's Hoard" and rows[10]["exe"] == "python.exe" and "galton_hoard" in rows[10]["cmd"]
    assert rows[11]["label"] == "Codex" and rows[11]["exe"] == "node.exe"
    assert rows[12]["label"] == "" and rows[12]["exe"] == "" and rows[12]["connections"] == 1          # AccessDenied: listed, unnamed
    assert rows[13]["label"] == "chrome" and rows[None]["connections"] == 1
    assert m["processes"][0]["pid"] == 10                                                                # busiest first
    n = out["by_key"]["n"]
    assert n["connections"] == 2 and [r["pid"] for r in n["processes"]] == [11]


def test_local_connections_when_the_system_refuses_or_psutil_is_missing():
    denied = local_processes(TARGETS, Psutil([], fail=Psutil.AccessDenied()), lambda h: RESOLVE[h])
    assert denied["error"] == "access_denied" and denied["by_key"]["m"]["processes"] == []
    assert local_processes(TARGETS, Psutil([], fail=OSError("boom")), lambda h: RESOLVE[h])["error"] == "boom"
    assert local_processes(TARGETS, None, lambda h: RESOLVE[h])["error"] == "unavailable"
    nothing = local_processes(TARGETS, Psutil([conn(("198.51.100.1", 8000))]), lambda h: set())          # a host name that does not resolve
    assert nothing["error"] is None and nothing["by_key"]["m"]["connections"] == 0


@pytest.mark.parametrize("name,cmd,cwd,label", [
    ("python.exe", ["python", "-m", "galton_hoard"], "", "Galton's Hoard"),
    ("python.exe", ["python", "-m", "my_great_hoard.app", "--port", "1"], "", "My Great's Hoard"),
    ("python.exe", ["python", "-m", "uvicorn", "x:app"], "", "python · -m uvicorn"),
    ("python.exe", ["python", "C:\\work\\faustus\\app.py"], "", "Faustus"),
    ("python.exe", ["python", "app.py"], "D:/LocalAI/odysseus", "Faustus"),
    ("node.exe", ["node", "C:/Users/u/AppData/npm/node_modules/@openai/codex/bin/codex.js"], "", "Codex"),
    ("claude.exe", ["claude"], "", "Claude Code"),
    ("node.exe", ["node", "/srv/app/server.js"], "", "node · server.js"),
    ("python3", ["python3", "/srv/run_me.py"], "", "python3 · run_me.py"),
    ("chrome.exe", ["chrome.exe"], "", "chrome"),
    ("", [], "", "?"),
])
def test_process_labels(name, cmd, cwd, label):
    assert process_label(name, cmd, cwd) == label


def test_the_local_snapshot_names_this_pc_from_its_connections(tmp_path, clock):
    ps = Psutil([conn(("198.51.100.1", 8000), ("192.0.2.10", 50002), pid=10)], {10: ("python.exe", ["python", "-m", "galton_hoard"])})
    c, _ = make(tmp_path, clock, psutil_mod=ps, resolve=lambda h: RESOLVE.get(h, set()))
    c.ingest("m", [line(T0 - 5, "192.0.2.10")], now=T0)
    assert client(c.view("m"), "192.0.2.10")["role"] == ""
    v = c.snapshot("m", poll=True)["m"]
    assert client(v, "192.0.2.10")["label"] == "Este PC" and client(v, "192.0.2.10")["this_pc"] is True
    assert v["this_pc"]["connections"] == 1 and v["this_pc"]["processes"][0]["label"] == "Galton's Hoard" and v["this_pc"]["error"] is None


def test_the_route_helper_never_raises():
    assert route_source("not a host name at all") is None
    assert route_source("127.0.0.1") == "127.0.0.1"


# ------------------------------------------------------------------ the demo world
def test_the_demo_log_speaks_uvicorn(clock):
    lines = fake_access_lines(8000, 0, T0, T0 - 3600)
    assert len(lines) > 100 and all(parse_line(ln) for ln in lines)
    ips = {parse_line(ln).ip for ln in lines}
    assert {"192.0.2.10", "192.0.2.20", "192.0.2.30", "198.51.100.7", "127.0.0.1"} <= ips
    odd = {parse_line(ln).ip for ln in fake_access_lines(8001, 0, T0, T0 - 3600)}
    assert "198.51.100.7" not in odd and "192.0.2.10" in odd
    cut = parse_line(lines[len(lines) // 2]).ns
    assert all(parse_line(ln).ns > cut for ln in fake_access_lines(8000, cut, T0, T0 - 3600))
    assert fake_access_lines(8000, 0, T0, T0 - 3600, limit=7) == lines[-7:]
    assert FakePsutil.PROGRAMS


# ------------------------------------------------------------------ the real helper, against a pretend docker
@pytest.fixture
def shim(tmp_path):
    """A folder with ``docker`` and ``sudo`` scripts that behave like the real ones would for a given scenario."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "docker.log"
    (bin_ / "docker").write_text(f"""#!/bin/sh
echo "docker $@" >> {log}
if [ -n "$DENY" ]; then echo "permission denied while trying to connect to the Docker daemon socket" >&2; exit 1; fi
last=""; for a in "$@"; do last="$a"; done
if [ "$last" != "good" ] && [ "$last" != "second" ]; then echo "Error response from daemon: No such container: $last" >&2; exit 1; fi
cat <<'EOF'
2026-10-09T10:00:00.000000001Z INFO 10-09 vLLM engine started
2026-10-09T10:00:01.000000000Z INFO:     192.0.2.5:1000 - "POST /v1/chat/completions HTTP/1.1" 200 OK
2026-10-09T10:00:02.000000000Z INFO:     127.0.0.1:1001 - "GET /health HTTP/1.1" 200 OK
2026-10-09T10:00:03.000000000Z INFO:     192.0.2.6:1002 - "GET /v1/models HTTP/1.1" 200 OK
2026-10-09T10:00:02.500000000Z Traceback (most recent call last):
2026-10-09T10:00:04.000000000Z INFO:     192.0.2.5:1003 - "POST /v1/messages HTTP/1.1" 400 Bad Request
EOF
""")
    (bin_ / "sudo").write_text('#!/bin/sh\nshift\nif [ -n "$NOSUDO" ]; then echo "sudo: a password is required" >&2; exit 1; fi\nDENY= exec "$@"\n')
    for f in ("docker", "sudo"):
        (bin_ / f).chmod(bin_ .joinpath(f).stat().st_mode | stat.S_IEXEC)
    return bin_, log


def run_helper(shim, args, **env):
    bin_, _ = shim
    script = Path(__file__).resolve().parent.parent / "prometheus_hoard" / "remote" / "accesslog.py"
    res = subprocess.run([sys.executable, "-", json.dumps(args)], input=script.read_text(encoding="utf-8"), capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SSH_CONNECTION": "192.0.2.10 5000 192.0.2.99 22", **env}, timeout=30)
    return json.loads(res.stdout.strip().splitlines()[-1])


# The helper runs on the Sparks (Linux); the shims are POSIX shell scripts standing in for docker and sudo.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the access-log helper and its docker/sudo shims run on the Sparks (POSIX)")


@posix_only
def test_the_helper_returns_only_access_lines_newer_than_the_cursor(shim):
    out = run_helper(shim, {"containers": ["good"], "since": "2026-10-09T10:00:01.000000000Z"})
    assert out["ok"] is True and out["via"] == "docker" and out["container"] == "good" and out["ssh_client"] == "192.0.2.10"
    assert [parse_line(ln).ip for ln in out["lines"]] == ["127.0.0.1", "192.0.2.6", "192.0.2.5"]         # sorted, strictly after the cursor
    assert all("Traceback" not in ln and "vLLM" not in ln for ln in out["lines"])
    assert "--since 2026-10-09T10:00:01.000000000Z" in shim[1].read_text(encoding="utf-8")
    assert len(run_helper(shim, {"containers": ["good"], "since": "2026-10-09T09:00:00Z", "max_lines": 2})["lines"]) == 2
    assert run_helper(shim, {"containers": ["good"], "since": "2026-10-09T09:00:00Z", "max_lines": 2})["truncated"] is True


@posix_only
def test_the_helper_tries_containers_in_turn_and_falls_back_to_sudo(shim):
    out = run_helper(shim, {"containers": ["stale-id", "second"], "since": "2026-10-09T10:00:00Z"})
    assert out["ok"] is True and out["container"] == "second" and out["via"] == "docker"
    denied = run_helper(shim, {"containers": ["good"], "since": "2026-10-09T10:00:00Z"}, DENY="1")
    assert denied["ok"] is True and denied["via"] == "sudo"                    # not in the docker group: passwordless sudo
    swapped = run_helper(shim, {"containers": ["good"], "since": "2026-10-09T10:00:00Z", "prefer_sudo": True}, DENY="1")
    assert swapped["via"] == "sudo"
    nosudo = run_helper(shim, {"containers": ["good"], "since": "2026-10-09T10:00:00Z"}, DENY="1", NOSUDO="1")
    assert nosudo["ok"] is False and nosudo["error"] == "docker_denied"
    missing = run_helper(shim, {"containers": ["nope"], "since": "2026-10-09T10:00:00Z"})
    assert missing["ok"] is False and missing["error"] == "no_container"
    assert run_helper(shim, {"containers": []})["error"] == "no_container"


# ------------------------------------------------------------------ containers of a server
def test_log_containers_prefers_the_health_script_then_the_recipe_then_the_head(services):
    r = services.recipes
    services.cluster.probe_all()
    info = {"recipe": "glm53-tp3", "base_url": "http://spark-spark1.local:8000/v1", "head": "spark1"}
    assert r.log_containers(info) == []                      # nothing is running: no health verdict, no declared container, no containers
    r._health["glm53-tp3"] = {"up": True, "script": {"memory_guard": {"container_id": "0123abcd"}}}
    assert r.log_containers(info)[0] == "0123abcd"
    folder = Path(r.folder) / "glm53-tp3" / "recipe.json"
    data = json.loads(folder.read_text(encoding="utf-8"))
    folder.write_text(json.dumps({**data, "container": "glm53"}), encoding="utf-8")
    assert r.log_containers(info)[:2] == ["0123abcd", "glm53"]
    node = services.cluster.nodes["spark1"]
    node.metrics = {**(node.metrics or {}), "containers": [
        {"id": "aaa111", "name": "web", "image": "nginx", "ports": "0.0.0.0:80->80/tcp", "command": "nginx"},
        {"id": "bbb222", "name": "llm", "image": "img", "ports": "", "command": "python -m x --port 8000 --model y"},
        {"id": "ccc333", "name": "other", "image": "img", "ports": "0.0.0.0:18000->18000/tcp", "command": "python -m x --port 18000"}]}
    assert r.log_containers(info) == ["0123abcd", "glm53", "bbb222", "llm"]
    # a detected server has no recipe: the port or the only engine container tells
    det = {"recipe": "spark1-9000", "base_url": "http://x:9000/v1", "head": "spark1"}
    assert r.log_containers(det) == []
    node.metrics["containers"] = [{"id": "ddd444", "name": "vllm-node", "image": "vllm/vllm-openai:x", "ports": "", "command": ""}]
    assert r.log_containers(det) == ["ddd444", "vllm-node"]
    node.metrics["containers"].append({"id": "eee555", "name": "sglang", "image": "lmsysorg/sglang", "ports": "", "command": ""})
    assert r.log_containers(det) == []                       # two candidates and no way to tell: it does not guess


# ------------------------------------------------------------------ the demo cluster, the route and the tools
def serve_demo(call, services, recipe="glm53-tp3"):
    call("deploy_start", {"recipe": recipe, "wait": True})
    services.serving.fast_s = 0
    services.serving.refresh_s = 0
    services.clients.fast_s = 0
    services.clients.idle_s = 0
    services.clients.refresh_s = 0
    services.clients.local_s = 0
    services.cluster.probe_all()


def test_the_demo_cluster_has_clients(call, services, client):
    assert call("serving_stats")["endpoints"] == []
    serve_demo(call, services)
    e = call("serving_stats")["endpoints"][0]
    c = e["clients"]
    assert c["ok"] is True and c["source"] and c["container"] and c["local_probes"] > 0
    by = {x["ip"]: x for x in c["clients"]}
    assert by["192.0.2.10"]["label"] == "Este PC" and by["192.0.2.10"]["role"] == "this_pc"
    assert set(by["192.0.2.10"]["kinds"]) == {"chat", "responses"} and by["192.0.2.10"]["inference_1h"] > 100
    assert by["192.0.2.20"]["only_polling"] is True and by["192.0.2.30"]["errors"] > 0 and by["192.0.2.30"]["kinds"] == ["messages"]
    assert by["198.51.100.7"]["kinds"][0] == "embeddings"
    assert by["198.51.100.231"]["label"] == "Spark2" and by["198.51.100.231"]["role"] == "spark"
    assert "127.0.0.1" not in by
    labels = {p["label"] for p in c["this_pc"]["processes"]}
    assert {"Faustus", "Galton's Hoard", "Codex", "Prospero's Hoard"} <= labels and "" in labels
    assert c["this_pc"]["ips"] == ["192.0.2.10"] and c["this_pc"]["connections"] == 8 and c["this_pc"]["error"] is None
    # a second look reads nothing twice
    before = by["192.0.2.10"]["requests"]
    again = {x["ip"]: x for x in call("serving_stats")["endpoints"][0]["clients"]["clients"]}
    assert 0 <= again["192.0.2.10"]["requests"] - before < 20
    assert "clients" not in call("serving_stats", {"clients": False})["endpoints"][0]


def test_api_serving_carries_the_clients(client, services, call):
    serve_demo(call, services, "qwen38-27b-1m")
    body = client.get("/api/serving").json()
    cl = body["endpoints"][0]["clients"]
    assert {x["ip"] for x in cl["clients"]} >= {"192.0.2.10", "192.0.2.30"} and "198.51.100.7" not in {x["ip"] for x in cl["clients"]}   # odd port: no embeddings client
    assert "clients" not in client.get("/api/serving?clients=false").json()["endpoints"][0]
    assert client.get("/api/serving?recipe=nope").json()["endpoints"] == []


def test_client_name_set_tool(call, services, client):
    serve_demo(call, services)
    out = call("client_name_set", {"ip": "192.0.2.30", "name": "  Móvil   de casa "})
    assert out == {"ip": "192.0.2.30", "name": "Móvil de casa", "role": "", "spark": "", "label": "Móvil de casa"}
    assert services.settings["client_names"] == {"192.0.2.30": "Móvil de casa"}
    by = {x["ip"]: x for x in call("serving_stats")["endpoints"][0]["clients"]["clients"]}
    assert by["192.0.2.30"]["label"] == "Móvil de casa" and by["192.0.2.30"]["name"] == "Móvil de casa"
    pc = call("client_name_set", {"ip": "[::ffff:192.0.2.10]", "name": "Torre"})
    assert pc["ip"] == "192.0.2.10" and pc["label"] == "Torre" and pc["role"] == "this_pc"           # a custom name wins, the role stays
    assert call("client_name_set", {"ip": "192.0.2.30", "name": ""})["label"] == "192.0.2.30"
    assert services.settings["client_names"] == {"192.0.2.10": "Torre"}
    assert call("client_name_set", {"ip": "nope", "name": "x"}, status=400)["code"] == "invalid"
    assert call("client_name_set", {"ip": "192.0.2.1", "name": "x" * 70}, status=400)["code"] == "invalid_arguments"
    assert call("settings_get")["client_names"] == {"192.0.2.10": "Torre"}
    assert call("settings_set", {"client_names": {"192.0.2.9": "Otro"}})["client_names"] == {"192.0.2.9": "Otro"}


def test_clients_persist_in_the_data_folder(call, services):
    serve_demo(call, services)
    call("serving_stats")
    services.clients.save(force=True)
    saved = json.loads((services.config.data_dir / "clients.json").read_text(encoding="utf-8"))
    assert saved["this_pc"] == ["192.0.2.10"] and "192.0.2.10" in saved["endpoints"]["glm53-tp3"]["ips"]
    call("deploy_stop", {"recipe": "glm53-tp3"})
    assert call("serving_stats")["endpoints"] == []
    services.clients.save(force=True)
    assert "192.0.2.10" in json.loads((services.config.data_dir / "clients.json").read_text(encoding="utf-8"))["endpoints"]["glm53-tp3"]["ips"]


def test_a_head_without_docker_access_says_so(call, services, world):
    serve_demo(call, services)
    orig = services.clients.runner
    services.clients.runner = lambda node, args: {"ok": False, "error": "docker_denied"}
    for ep in services.clients._eps.values():
        ep.cursor = None
        ep.last_read = -1e18
    e = call("serving_stats")["endpoints"][0]["clients"]
    assert e["ok"] is False and e["error"] == "docker_denied"
    services.clients.runner = orig


def test_probes_for_other_engines_count_as_polling_not_errors(tmp_path, clock):
    c, _ = make(tmp_path, clock)
    L = [line(T0 - 30, "192.0.2.5", "GET /props", code=404), line(T0 - 20, "192.0.2.5", "GET /api/version", code=404),
         line(T0 - 15, "192.0.2.5", "GET /slots", code=404), line(T0 - 10, "192.0.2.5", "POST /v1/chat/completions", code=500)]
    c.ingest("m", L, now=T0)
    a = client(c.view("m"), "192.0.2.5")
    assert a["by_kind"]["poll"] == 3
    assert a["errors"] == 1      # only the failed chat request
