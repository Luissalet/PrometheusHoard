"""Live inference figures of the servers the Sparks run, read from their Prometheus ``/metrics`` page.

``Serving`` follows every endpoint ``Recipes.endpoints()`` lists (recipes and detected servers alike): a sampler thread scrapes
``<server>/metrics`` every 2 s while the UI or an assistant asked in the last minute (``touch``) and every 10 s otherwise, keeps about
fifteen minutes of samples in memory and derives the figures the page shows: decode and prefill tokens per second, requests running
and waiting, KV cache use, tokens per step and acceptance of speculative decoding, time to first token and inter-token latency
percentiles. Cumulative totals survive restarts of the app and of the servers in ``<data>/serving.json``.

Only ``vllm:`` series are read. Counters live as long as the server's container, so a drop in a counter means a restart: the value
seen before is added to an offset and the totals keep growing. The totals only count what this app saw while it was running."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from .hoard_link.atomic import read_json, write_json_atomic

log = logging.getLogger("prometheus.serving")

INF = float("inf")
KEEP_S = 900.0          # samples kept per endpoint
WINDOW_S = 10.0         # span of the "now" rates
LATENCY_S = 300.0       # span of the latency percentiles
SERIES_S = 600.0        # span of the sparklines
SERIES_POINTS = 120
PREEMPT_S = 300.0

COUNTERS = {            # metric (without vllm: and _total) -> sample field
    "generation_tokens": "gen", "prompt_tokens": "prompt", "prompt_tokens_cached": "cached", "num_preemptions": "preempt",
    "request_success": "req", "spec_decode_num_drafts": "drafts", "spec_decode_num_draft_tokens": "dtok",
    "spec_decode_num_accepted_tokens": "acc",
}
HISTOGRAMS = {          # metric -> key
    "time_to_first_token_seconds": "ttft", "inter_token_latency_seconds": "itl", "time_per_output_token_seconds": "itl",
    "e2e_request_latency_seconds": "e2e", "request_queue_time_seconds": "queue", "request_prefill_time_seconds": "prefill",
}
TOTAL_FIELDS = ("prompt_tokens", "generation_tokens", "cached_tokens", "requests")
_TOTAL_SOURCE = {"prompt_tokens": "prompt", "generation_tokens": "gen", "cached_tokens": "cached", "requests": "req"}


# ====================================================================================== the text format
def _parse_labels(line: str, i: int) -> tuple[int, dict[str, str]]:
    """Labels from the character after ``{``; returns the index after ``}`` (-1 when the line is malformed)."""
    labels: dict[str, str] = {}
    n = len(line)
    while i < n:
        while i < n and line[i] in " \t,":
            i += 1
        if i >= n:
            return -1, labels
        if line[i] == "}":
            return i + 1, labels
        j = i
        while j < n and line[j] not in "=}":
            j += 1
        if j >= n or line[j] != "=":
            return -1, labels
        key = line[i:j].strip()
        j += 1
        if j >= n or line[j] != '"':
            return -1, labels
        j += 1
        buf: list[str] = []
        while j < n and line[j] != '"':
            if line[j] == "\\" and j + 1 < n:
                nxt = line[j + 1]
                buf.append({"n": "\n", "\\": "\\", '"': '"'}.get(nxt, "\\" + nxt))
                j += 2
            else:
                buf.append(line[j])
                j += 1
        if j >= n:
            return -1, labels
        labels[key] = "".join(buf)
        i = j + 1
    return -1, labels


def parse_metrics(text: str) -> list[tuple[str, dict[str, str], float]]:
    """``[(name, labels, value)]`` for the ``vllm:`` lines of a Prometheus text page (comments, other series and ``*_created`` skipped)."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("vllm:"):
            continue
        n = len(line)
        i = 0
        while i < n and line[i] not in "{ \t":
            i += 1
        name = line[:i]
        if name.endswith("_created"):
            continue
        labels: dict[str, str] = {}
        if i < n and line[i] == "{":
            i, labels = _parse_labels(line, i + 1)
            if i < 0:
                continue
        rest = line[i:].split()
        if not rest:
            continue
        try:
            value = float(rest[0])
        except ValueError:
            continue
        if math.isnan(value):
            continue
        out.append((name, labels, value))
    return out


def _gauge_name(name: str) -> str:
    return name[len("vllm:"):]


def aggregate(rows: list[tuple[str, dict[str, str], float]]) -> Optional[dict[str, Any]]:
    """One sample (without time) from parsed rows, summed across label sets. None when the page had no ``vllm:`` series."""
    if not rows:
        return None
    s: dict[str, Any] = {k: None for k in ("gen", "prompt", "cached", "preempt", "req", "drafts", "dtok", "acc", "running", "waiting", "kv")}
    s["asleep"] = False
    s["h"] = {}
    kv: list[float] = []
    for name, labels, value in rows:
        bare = _gauge_name(name)
        if bare.endswith("_bucket"):
            key = HISTOGRAMS.get(bare[:-len("_bucket")])
            if key and "le" in labels:
                try:
                    le = float(labels["le"])
                except ValueError:
                    continue
                buckets = s["h"].setdefault(key, {})
                buckets[le] = buckets.get(le, 0.0) + value
            continue
        if bare.endswith(("_sum", "_count")):
            continue
        base = bare[:-len("_total")] if bare.endswith("_total") else bare
        if base in COUNTERS:
            f = COUNTERS[base]
            s[f] = (s[f] or 0.0) + value
        elif base == "num_requests_running":
            s["running"] = (s["running"] or 0.0) + value
        elif base == "num_requests_waiting":
            s["waiting"] = (s["waiting"] or 0.0) + value
        elif base in ("kv_cache_usage_perc", "gpu_cache_usage_perc"):
            kv.append(value)
        elif base == "engine_sleep_state":
            state = labels.get("sleep_state") or labels.get("state")
            if value > 0 and (state is None or state != "awake"):
                s["asleep"] = True
    if kv:
        s["kv"] = sum(kv) / len(kv)
    return s


def metrics_url(base_url: str) -> str:
    """Servers advertise ``http://host:port/v1``; the Prometheus page lives at the root of the server."""
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base + "/metrics"


def http_text(url: str, timeout: float = 2.0) -> tuple[int, str]:
    import httpx

    try:
        r = httpx.get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:200]
    return r.status_code, r.text


# ====================================================================================== arithmetic on samples
def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Increase of a counter between two samples; a counter that went down restarted from zero."""
    if a is None or b is None:
        return None
    return b if b < a else b - a


def _num(x: Optional[float], digits: int = 2) -> Optional[float]:
    return None if x is None or math.isnan(x) or math.isinf(x) else round(x, digits)


def quantile(q: float, buckets: dict[float, float]) -> Optional[float]:
    """Like Prometheus' ``histogram_quantile``: cumulative buckets ``{le: count}``, linear inside the bucket that holds the rank."""
    items = sorted(buckets.items())
    if not items or items[-1][0] != INF:
        return None
    total = items[-1][1]
    if total <= 0:
        return None
    rank = q * total
    prev_le, prev_count = 0.0, 0.0
    for le, count in items:
        if count >= rank:
            if le == INF:
                return prev_le if prev_le > 0 else None
            if count == prev_count:
                return le
            return prev_le + (le - prev_le) * (rank - prev_count) / (count - prev_count)
        prev_le, prev_count = le, count
    return None


def _bucket_delta(old: dict[float, float], new: dict[float, float]) -> Optional[dict[float, float]]:
    out = {}
    for le in set(old) | set(new):
        d = new.get(le, 0.0) - old.get(le, 0.0)
        if d < 0:
            return None      # the server restarted in between
        out[le] = d
    return out


def _base_sample(samples: list[dict[str, Any]], span: float) -> Optional[dict[str, Any]]:
    """The oldest sample inside ``span`` seconds of the last one (None with fewer than two samples)."""
    if len(samples) < 2:
        return None
    last = samples[-1]
    for s in samples[:-1]:
        if s["t"] >= last["t"] - span:
            return s
    return samples[-2]


def compute_now(samples: list[dict[str, Any]], window: float = WINDOW_S) -> dict[str, Any]:
    """The live figures of an endpoint from its samples (oldest first)."""
    out: dict[str, Any] = {"decode_tps": None, "prefill_tps": None, "running": None, "waiting": None, "kv_pct": None, "tokens_per_step": None,
                           "acceptance": None, "spec_cumulative": False, "preemptions": None, "preemptions_5m": None, "preempting": False,
                           "asleep": False, "window_s": None}
    if not samples:
        return out
    last = samples[-1]
    out["running"] = None if last["running"] is None else int(round(last["running"]))
    out["waiting"] = None if last["waiting"] is None else int(round(last["waiting"]))
    out["kv_pct"] = None if last["kv"] is None else _num(last["kv"] * 100, 1)
    out["asleep"] = bool(last["asleep"])
    base = _base_sample(samples, window + 0.5)
    if base is not None:
        dt = last["t"] - base["t"]
        out["window_s"] = _num(dt, 1)
        if dt > 0:
            gen = _delta(base["gen"], last["gen"])
            out["decode_tps"] = _num(gen / dt, 1) if gen is not None else None
            p, c = _delta(base["prompt"], last["prompt"]), _delta(base["cached"], last["cached"])
            if p is not None:
                out["prefill_tps"] = _num(max(0.0, p - (c or 0.0)) / dt, 1)
        pre = _delta(base["preempt"], last["preempt"])
        out["preemptions"] = None if pre is None else int(round(pre))
        out["preempting"] = bool(pre and pre > 0)
        dd, da, dt_ = _delta(base["drafts"], last["drafts"]), _delta(base["acc"], last["acc"]), _delta(base["dtok"], last["dtok"])
        if dd and dd > 0 and da is not None:
            out["tokens_per_step"] = _num(1 + da / dd)
        if dt_ and dt_ > 0 and da is not None:
            out["acceptance"] = _num(100 * da / dt_, 1)
    if out["tokens_per_step"] is None and last["drafts"] and last["acc"] is not None:
        out["tokens_per_step"] = _num(1 + last["acc"] / last["drafts"])
        out["spec_cumulative"] = True
    if out["acceptance"] is None and last["dtok"] and last["acc"] is not None:
        out["acceptance"] = _num(100 * last["acc"] / last["dtok"], 1)
        out["spec_cumulative"] = True
    five = _base_sample(samples, PREEMPT_S)
    if five is not None:
        pre5 = _delta(five["preempt"], last["preempt"])
        out["preemptions_5m"] = None if pre5 is None else int(round(pre5))
    return out


def compute_latency(samples: list[dict[str, Any]], span: float = LATENCY_S) -> dict[str, Any]:
    """p50 and p95 (seconds) of each histogram over the last ``span`` seconds; the whole life of the server when nothing moved."""
    out: dict[str, Any] = {}
    if not samples:
        return out
    last = samples[-1]
    base = _base_sample(samples, span)
    for key in ("ttft", "itl", "e2e", "queue", "prefill"):
        cum = last["h"].get(key)
        if not cum:
            continue
        buckets, cumulative = None, True
        if base is not None and base["h"].get(key) is not None:
            d = _bucket_delta(base["h"][key], cum)
            if d is not None and d.get(INF, 0) > 0:
                buckets, cumulative = d, False
        if buckets is None:
            buckets = cum
        p50, p95 = quantile(0.5, buckets), quantile(0.95, buckets)
        if p50 is None:
            continue
        out[key] = {"p50": _num(p50, 4), "p95": _num(p95, 4), "cumulative": cumulative}
    if "itl" in out and out["itl"]["p50"]:
        out["stream_tps"] = _num(1.0 / out["itl"]["p50"], 1)
    return out


def compute_series(samples: list[dict[str, Any]], span: float = SERIES_S, points: int = SERIES_POINTS) -> list[dict[str, Any]]:
    """Rates between consecutive samples for the last ``span`` seconds, averaged into at most ``points`` points."""
    if len(samples) < 2:
        return []
    cut = samples[-1]["t"] - span
    rows = []
    for a, b in zip(samples, samples[1:]):
        if b["t"] < cut or b["t"] <= a["t"]:
            continue
        dt = b["t"] - a["t"]
        gen = _delta(a["gen"], b["gen"])
        p, c = _delta(a["prompt"], b["prompt"]), _delta(a["cached"], b["cached"])
        rows.append({"t": b["t"], "decode_tps": None if gen is None else gen / dt,
                     "prefill_tps": None if p is None else max(0.0, p - (c or 0.0)) / dt,
                     "running": b["running"], "kv_pct": None if b["kv"] is None else b["kv"] * 100})
    size = max(1, math.ceil(len(rows) / points))
    out = []
    for i in range(0, len(rows), size):
        chunk = rows[i:i + size]
        point: dict[str, Any] = {"t": round(chunk[-1]["t"], 1)}
        for f in ("decode_tps", "prefill_tps", "running", "kv_pct"):
            vals = [r[f] for r in chunk if r[f] is not None]
            point[f] = _num(sum(vals) / len(vals), 2) if vals else None
        out.append(point)
    return out


# ====================================================================================== the sampler
class _Endpoint:
    def __init__(self, info: dict[str, Any]):
        self.info = info
        self.samples: deque[dict[str, Any]] = deque()
        self.ok: Optional[bool] = None
        self.error = ""
        self.last_scrape = 0.0
        self.last_ok = 0.0


class Serving:
    def __init__(self, endpoints: Callable[[], list[dict[str, Any]]], path: Path, *, getter: Optional[Callable[[str, float], tuple[int, str]]] = None,
                 clock: Callable[[], float] = time.time, refresh_s: float = 15.0, fast_s: float = 2.0, idle_s: float = 10.0,
                 touch_s: float = 60.0, save_s: float = 30.0, timeout_s: float = 2.0):
        self._endpoints = endpoints
        self.path = Path(path)
        self.get = getter or http_text
        self.clock = clock
        self.refresh_s, self.fast_s, self.idle_s, self.touch_s, self.save_s, self.timeout_s = refresh_s, fast_s, idle_s, touch_s, save_s, timeout_s
        self._lock = threading.RLock()
        self._eps: dict[str, _Endpoint] = {}
        self._listed = -1e18
        self._touched = -1e18
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="prometheus-serving")
        raw = read_json(self.path, default={}) or {}
        self._store: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if not isinstance(self._store.get("endpoints"), dict):
            self._store["endpoints"] = {}
        if not isinstance(self._store.get("since"), (int, float)):
            self._store["since"] = self.clock()
        self._dirty = not self.path.exists()
        self._saved = -1e18

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="prometheus-serving", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.save(force=True)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def touch(self) -> None:
        self._touched = self.clock()

    @property
    def last_touch(self) -> float:
        """When the page or the tool last asked (other collectors follow the same attention)."""
        return self._touched

    def infos(self) -> list[dict[str, Any]]:
        """The endpoints being followed (the list the sampler already holds; read from ``Recipes`` only when it was never loaded)."""
        if self._listed < 0:
            self.refresh_list()
        with self._lock:
            return [dict(ep.info) for ep in self._eps.values()]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                log.exception("serving sampler")
            self._stop.wait(0.5)

    @property
    def interval(self) -> float:
        return self.fast_s if self.clock() - self._touched <= self.touch_s else self.idle_s

    # ------------------------------------------------------------------ sampling
    def refresh_list(self) -> None:
        try:
            listed = self._endpoints()
        except Exception:  # noqa: BLE001 - keep the last list; the next round tries again
            log.exception("endpoint list")
            return
        # One server can be listed twice (a recipe's deployment and the same server detected on its port): keep the recipe.
        by_url: dict[str, dict[str, Any]] = {}
        for info in listed:
            url = metrics_url(info.get("base_url", "")) or info["recipe"]
            if url not in by_url or (by_url[url].get("detected") and not info.get("detected")):
                by_url[url] = info
        listed = list(by_url.values())
        with self._lock:
            self._listed = self.clock()
            keys = set()
            for info in listed:
                key = info["recipe"]
                keys.add(key)
                if key in self._eps:
                    self._eps[key].info = info
                else:
                    self._eps[key] = _Endpoint(info)
            for key in [k for k in self._eps if k not in keys]:
                del self._eps[key]

    def poll_once(self) -> None:
        """One round: refresh the endpoint list when it is stale, scrape the endpoints that are due, save the totals when it is time."""
        now = self.clock()
        if now - self._listed >= self.refresh_s:
            self.refresh_list()
        with self._lock:
            interval = self.interval
            due = [ep for ep in self._eps.values() if now - ep.last_scrape >= interval]
        if len(due) == 1:
            self._scrape(due[0])
        elif due:
            list(self._pool.map(self._scrape, due))
        self.save()

    def _scrape(self, ep: _Endpoint) -> None:
        t = self.clock()
        ep.last_scrape = t
        try:
            code, text = self.get(metrics_url(ep.info["base_url"]), self.timeout_s)
        except Exception as exc:  # noqa: BLE001
            code, text = 0, str(exc)
        if code != 200:
            self._fail(ep, f"HTTP {code}" if code else (str(text)[:160] or "no answer"))
            return
        self._ingest(ep, t, aggregate(parse_metrics(text)))

    def _fail(self, ep: _Endpoint, error: str) -> None:
        with self._lock:
            ep.ok, ep.error = False, error

    def _ingest(self, ep: _Endpoint, t: float, sample: Optional[dict[str, Any]]) -> None:
        if sample is None:
            self._fail(ep, "no vllm metrics")
            return
        sample["t"] = t
        with self._lock:
            ep.ok, ep.error, ep.last_ok = True, "", t
            ep.samples.append(sample)
            while ep.samples and ep.samples[0]["t"] < t - KEEP_S:
                ep.samples.popleft()
            self._accumulate(ep.info, sample, t)

    def ingest_text(self, key: str, text: str, t: Optional[float] = None, info: Optional[dict[str, Any]] = None) -> None:
        """Feed one scrape of ``key`` by hand (tests, tools)."""
        with self._lock:
            ep = self._eps.setdefault(key, _Endpoint(info or {"recipe": key, "title": key, "base_url": "", "models": [], "nodes": [], "head": "", "engine": "vllm"}))
        self._ingest(ep, self.clock() if t is None else t, aggregate(parse_metrics(text)))

    # ------------------------------------------------------------------ persistent totals
    def _accumulate(self, info: dict[str, Any], sample: dict[str, Any], t: float) -> None:
        key = info["recipe"]
        rec = self._store["endpoints"].setdefault(key, {"since": t, "offset": {}, "last": {}})
        rec["title"] = info.get("title") or key
        for field, src in _TOTAL_SOURCE.items():
            cur = sample.get(src)
            if cur is None:
                continue
            last = rec["last"].get(field)
            if last is not None and cur < last:
                rec["offset"][field] = rec["offset"].get(field, 0) + last
            rec["last"][field] = cur
        self._dirty = True

    def _totals_of(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = {f: int(round(rec.get("offset", {}).get(f, 0) + rec.get("last", {}).get(f, 0))) for f in TOTAL_FIELDS}
        out["since"] = rec.get("since")
        return out

    def save(self, *, force: bool = False) -> None:
        now = self.clock()
        with self._lock:
            if not self._dirty or (not force and now - self._saved < self.save_s):
                return
            data = {"version": 1, **{k: v for k, v in self._store.items() if k != "version"}}
            self._dirty = False
            self._saved = now
        try:
            write_json_atomic(self.path, data)
        except OSError:
            log.exception("saving %s", self.path)
            self._dirty = True

    # ------------------------------------------------------------------ views
    def snapshot(self, recipe: Optional[str] = None, *, series: bool = True, poll: Optional[bool] = None) -> dict[str, Any]:
        """What the page and the tool show. Without a sampler thread (tests) the endpoints are scraped on demand."""
        self.touch()
        running = bool(self._thread and self._thread.is_alive())
        if poll if poll is not None else not running:
            self.poll_once()
        elif running and not self._eps and self.clock() - self._listed >= self.refresh_s:
            self.poll_once()      # the first look must not wait for the thread
        with self._lock:
            eps = [self._endpoint_view(ep, series) for ep in self._eps.values() if not recipe or ep.info["recipe"] == recipe]
            live = set(self._eps)
            past = []
            for key, rec in self._store["endpoints"].items():
                totals = self._totals_of(rec)
                past.append({"recipe": key, "title": rec.get("title") or key, "running": key in live, **totals})
            overall = {f: sum(p[f] for p in past) for f in TOTAL_FIELDS}
        past.sort(key=lambda p: (not p["running"], -p["generation_tokens"]))
        return {"endpoints": eps, "totals": {**overall, "since": self._store["since"], "endpoints": past},
                "poll_s": self.fast_s, "now": self.clock()}

    def _endpoint_view(self, ep: _Endpoint, series: bool) -> dict[str, Any]:
        samples = list(ep.samples)
        info = ep.info
        stale = ep.ok is False and self.clock() - ep.last_ok > 10      # figures of a server that stopped answering are not "now"
        view = {"recipe": info["recipe"], "title": info.get("title"), "base_url": info.get("base_url"), "metrics_url": metrics_url(info.get("base_url", "")),
                "models": info.get("models") or [], "nodes": info.get("nodes") or [], "head": info.get("head"), "engine": info.get("engine"),
                "detected": bool(info.get("detected")), "default": bool(info.get("default")), "ok": ep.ok, "error": ep.error,
                "age_s": _num(self.clock() - ep.last_ok, 1) if ep.last_ok else None, "samples": len(samples),
                "now": compute_now([] if stale else samples), "latency": compute_latency(samples)}
        rec = self._store["endpoints"].get(info["recipe"])
        view["totals"] = self._totals_of(rec) if rec else {**{f: 0 for f in TOTAL_FIELDS}, "since": None}
        if series:
            view["series"] = compute_series(samples)
        return view

    def summary(self) -> dict[str, dict[str, Any]]:
        """Cheap figures per endpoint from the cache, for ``sparks_overview`` (no touch, no network)."""
        with self._lock:
            out = {}
            for key, ep in self._eps.items():
                now = compute_now(list(ep.samples)[-12:])
                out[key] = {"ok": ep.ok, "decode_tps": now["decode_tps"], "running": now["running"], "kv_pct": now["kv_pct"]}
            return out
