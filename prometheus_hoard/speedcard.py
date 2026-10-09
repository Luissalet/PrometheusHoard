"""Speed card and capacity card of a model server.

Two separate things, on purpose:

* the **speed card** is a measurement: ``benchmark`` sends streaming chat requests to an OpenAI-compatible server at 1, 8, 16 and 64 concurrent
  streams and reports, per level, the aggregate output tokens per second, the median decode tokens per second of one stream, and the time
  to the first token (p50 and p95). It loads the server for minutes, so it only runs when asked (``SpeedCards.run``), as a local job, one
  at a time per endpoint, and every result is kept (``<data>/speedcards.json``, the last 20 per endpoint);
* the **capacity card** is a read: the context the server accepts (``/v1/models`` → ``max_model_len``), the KV pool in tokens
  (``vllm:cache_config_info`` → ``num_gpu_blocks`` × ``block_size``) and what the recipe recorded as verified. It costs two small GETs.

How a level is measured. Every request has its own random nonce at the start of the prompt, so the prefix cache never answers for
another request. Each level runs one warm-up round (discarded: kernels, CUDA graphs for that batch size) and then ``rounds`` measured rounds;
the reported figure is the median of the rounds. ``max_tokens`` is fixed and ``ignore_eos`` asks the server to keep generating up to it, so
every stream does the same work. Every generated token counts, reasoning included: tokens come from the server's ``usage`` when it sends
one (``stream_options.include_usage``) and from the number of non-empty deltas otherwise (``tokens_source`` says which). The time to the
first token is the first non-empty delta of any kind. A request that fails is an error and does not count towards the throughput."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from .errors import SparkError
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.ids import new_id
from .jobs import FINAL, Jobs, LocalJob
from .serving import metrics_url, parse_metrics

log = logging.getLogger("prometheus.speedcard")

LEVELS = (1, 8, 16, 64)
MAX_LEVEL = 256
KEEP = 20                 # results kept per endpoint
CAPACITY_TTL_S = 10.0

_TOPICS = ("the history of lighthouses", "how a river delta forms", "the life of a medieval cartographer", "the economics of a small harbour town",
           "a day in a mountain observatory", "the chemistry of bread", "how glaciers move", "the design of a public library",
           "the rise and fall of a trading city", "a letter from a lighthouse keeper", "how bees choose a new nest", "the engineering of old bridges")


class Cancelled(Exception):
    """The caller asked to stop; nothing is saved."""


# ====================================================================================== arithmetic
def percentile(values: list[float], q: float) -> Optional[float]:
    """Linear interpolation between closest ranks (``q`` in 0..100); None for an empty list."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _median(values: list[Optional[float]]) -> Optional[float]:
    xs = [v for v in values if v is not None]
    return statistics.median(xs) if xs else None


def _round(x: Optional[float], digits: int = 3) -> Optional[float]:
    return None if x is None else round(x, digits)


def make_prompt() -> str:
    """A prompt that is unique per request (random nonce first, so no prefix is shared) and asks for far more text than ``max_tokens``."""
    nonce = secrets.token_hex(12)
    topic = _TOPICS[secrets.randbelow(len(_TOPICS))]
    return (f"[request {nonce}] Write a long, detailed and continuous essay about {topic}. Do not stop, do not summarise and do not use "
            f"lists: keep writing flowing paragraphs until you are cut off.")


def summarize_round(results: list[dict[str, Any]], wall_s: float) -> dict[str, Any]:
    """One round: aggregate tokens per second over its wall time, median tokens per second of one stream, TTFT percentiles, errors."""
    ok = [r for r in results if r.get("ok")]
    tokens = sum(r["tokens"] for r in ok)
    streams = [(r["tokens"] - 1) / r["decode_s"] for r in ok if r["tokens"] >= 2 and r.get("decode_s", 0) > 0]
    ttfts = [r["ttft"] for r in ok if r.get("ttft") is not None]
    return {"requests": len(results), "ok": len(ok), "errors": len(results) - len(ok), "tokens": tokens, "wall_s": _round(wall_s),
            "agg_tps": _round(tokens / wall_s, 2) if ok and wall_s > 0 else None, "stream_tps": _round(_median(streams), 2),
            "ttft_p50": _round(percentile(ttfts, 50)), "ttft_p95": _round(percentile(ttfts, 95)),
            "sources": sorted({r.get("source", "") for r in ok}), "error_samples": sorted({r["error"] for r in results if r.get("error")})[:3]}


def summarize_level(n: int, rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """The median of the measured rounds of one level (rounds where every request failed are left out of the medians but count as errors)."""
    good = [r for r in rounds if r["ok"]]
    sources = sorted({s for r in good for s in r["sources"]})
    return {"n": n, "agg_tps": _round(_median([r["agg_tps"] for r in good]), 2), "stream_tps": _round(_median([r["stream_tps"] for r in good]), 2),
            "ttft_p50": _round(_median([r["ttft_p50"] for r in good])), "ttft_p95": _round(_median([r["ttft_p95"] for r in good])),
            "requests": sum(r["requests"] for r in rounds), "errors": sum(r["errors"] for r in rounds),
            "tokens": sum(r["tokens"] for r in rounds), "tokens_source": "+".join(sources) if sources else "",
            "rounds": [{"agg_tps": r["agg_tps"], "stream_tps": r["stream_tps"], "ttft_p50": r["ttft_p50"], "errors": r["errors"]} for r in rounds],
            "error_samples": sorted({e for r in rounds for e in r["error_samples"]})[:3]}


# ====================================================================================== one streaming request
def _delta_text(choice: dict[str, Any]) -> str:
    delta = choice.get("delta") or {}
    parts = [delta.get("content"), delta.get("reasoning_content"), delta.get("reasoning")]
    return "".join(p for p in parts if isinstance(p, str))


async def one_request(client: Any, url: str, body: dict[str, Any], headers: dict[str, str], t0: float) -> dict[str, Any]:
    """A streaming chat completion: ``{ok, tokens, source, ttft, decode_s, end, error}`` with times relative to ``t0`` (the round's start)."""
    import httpx

    first: Optional[float] = None
    last = time.perf_counter()
    deltas = 0
    usage_tokens: Optional[int] = None
    finished = False
    try:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                text = (await resp.aread()).decode("utf-8", "replace")[:200]
                return {"ok": False, "tokens": 0, "end": time.perf_counter() - t0, "status": resp.status_code, "error": f"HTTP {resp.status_code} {text}".strip()}
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    finished = True
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if isinstance(chunk.get("error"), dict) or (isinstance(chunk.get("error"), str) and chunk["error"]):
                    return {"ok": False, "tokens": deltas, "end": time.perf_counter() - t0, "error": f"stream error: {str(chunk['error'])[:160]}"}
                usage = chunk.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
                    usage_tokens = usage["completion_tokens"]
                for choice in chunk.get("choices") or []:
                    if _delta_text(choice):
                        now = time.perf_counter()
                        if first is None:
                            first = now
                        last = now
                        deltas += 1
                    if choice.get("finish_reason"):
                        finished = True
    except (httpx.HTTPError, OSError) as exc:
        return {"ok": False, "tokens": deltas, "end": time.perf_counter() - t0, "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}
    end = time.perf_counter()
    tokens = usage_tokens if usage_tokens is not None else deltas
    if first is None or tokens <= 0:
        return {"ok": False, "tokens": 0, "end": end - t0, "error": "the server sent no tokens"}
    if not finished:
        return {"ok": False, "tokens": tokens, "end": end - t0, "error": "the stream ended before it finished"}
    return {"ok": True, "tokens": tokens, "source": "usage" if usage_tokens is not None else "deltas", "ttft": first - t0, "decode_s": max(last - first, 0.0),
            "end": end - t0}


def request_body(model: str, max_tokens: int, ignore_eos: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": make_prompt()}], "max_tokens": max_tokens,
                            "temperature": 0.7, "stream": True, "stream_options": {"include_usage": True}}
    if ignore_eos:
        body["ignore_eos"] = True
    return body


async def run_round(client: Any, url: str, model: str, n: int, max_tokens: int, ignore_eos: bool, headers: dict[str, str],
                    cancel: Callable[[], bool]) -> tuple[list[dict[str, Any]], float]:
    """``n`` requests at once; returns their results and the wall time of the round. Raises ``Cancelled`` when ``cancel()`` turns true."""
    t0 = time.perf_counter()
    tasks = [asyncio.ensure_future(one_request(client, url, request_body(model, max_tokens, ignore_eos), headers, t0)) for _ in range(n)]

    async def watch() -> None:
        while not cancel():
            await asyncio.sleep(0.1)
        for t in tasks:
            t.cancel()

    watcher = asyncio.ensure_future(watch())
    try:
        out = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
    if cancel():
        raise Cancelled()
    results = []
    for r in out:
        if isinstance(r, BaseException):
            results.append({"ok": False, "tokens": 0, "end": time.perf_counter() - t0, "error": f"{r.__class__.__name__}: {str(r)[:160]}"})
        else:
            results.append(r)
    ends = [r["end"] for r in results if r.get("ok")]
    wall = max(ends) if ends else time.perf_counter() - t0
    return results, wall


async def benchmark(base_url: str, model: str, *, levels: tuple[int, ...] = LEVELS, max_tokens: int = 256, rounds: int = 3, warmup: int = 1,
                    timeout_s: float = 600.0, api_key: str = "", ignore_eos: bool = True, cancel: Callable[[], bool] = lambda: False,
                    progress: Callable[[float, str], None] = lambda p, line: None, transport: Any = None) -> dict[str, Any]:
    """The speed card of one server. ``transport`` replaces the network (tests, the demo cluster)."""
    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    steps = len(levels) * (rounds + warmup)
    done = 0
    out_levels: list[dict[str, Any]] = []
    started = time.time()
    limits = httpx.Limits(max_connections=max(levels) + 8, max_keepalive_connections=max(levels) + 8)
    kwargs: dict[str, Any] = {"transport": transport} if transport is not None else {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0), limits=limits, trust_env=False, **kwargs) as client:
        for n in levels:
            measured: list[dict[str, Any]] = []
            for i in range(warmup + rounds):
                is_warm = i < warmup
                progress(done / steps, f"N={n}: {'warm-up' if is_warm else f'round {i - warmup + 1}/{rounds}'}")
                results, wall = await run_round(client, url, model, n, max_tokens, ignore_eos, headers, cancel)
                if is_warm and ignore_eos and results and all(r.get("status") in (400, 422) for r in results):
                    # a server that rejects the extension parameter: measure without it (streams may then end early)
                    ignore_eos = False
                    results, wall = await run_round(client, url, model, n, max_tokens, ignore_eos, headers, cancel)
                done += 1
                if not is_warm:
                    measured.append(summarize_round(results, wall))
            level = summarize_level(n, measured)
            out_levels.append(level)
            progress(done / steps, f"N={n}: {level['agg_tps']} tok/s aggregate, {level['stream_tps']} tok/s per stream")
    return {"levels": out_levels, "ignore_eos": ignore_eos, "duration_s": round(time.time() - started, 1)}


# ====================================================================================== capacity
def capacity_card(info: dict[str, Any], get_json: Callable[[str, float], tuple[int, Any]], get_text: Callable[[str, float], tuple[int, str]],
                  recipe: Optional[dict[str, Any]] = None, model: str = "") -> dict[str, Any]:
    """What the server can hold: context length, KV pool in tokens and what the recipe verified. Missing sources leave their fields None."""
    base = str(info.get("base_url") or "").rstrip("/")
    out: dict[str, Any] = {"max_model_len": info.get("max_model_len"), "kv_blocks": None, "block_size": None, "kv_pool_tokens": None,
                           "cache_dtype": None, "concurrency_at_max_len": None, "verified": None, "sources": [], "errors": []}
    if base:
        code, data = get_json(base + "/models", 3.0)
        entries = data.get("data") if code == 200 and isinstance(data, dict) and isinstance(data.get("data"), list) else None
        if entries is not None:
            pick = next((m for m in entries if isinstance(m, dict) and m.get("id") == model), None) or next((m for m in entries if isinstance(m, dict)), None)
            if pick and isinstance(pick.get("max_model_len"), int):
                out["max_model_len"] = pick["max_model_len"]
                out["sources"].append("models")
        else:
            out["errors"].append("models: " + (f"HTTP {code}" if code else str(data)[:80]))
        code, text = get_text(metrics_url(base), 3.0)
        if code == 200:
            for name, labels, _ in parse_metrics(text):
                if name != "vllm:cache_config_info":
                    continue
                blocks, size = _int(labels.get("num_gpu_blocks")), _int(labels.get("block_size"))
                out["kv_blocks"], out["block_size"] = blocks, size
                out["cache_dtype"] = labels.get("cache_dtype") or None
                if blocks and size:
                    out["kv_pool_tokens"] = blocks * size
                    out["sources"].append("metrics")
                break
            else:
                out["errors"].append("metrics: no vllm:cache_config_info")
        else:
            out["errors"].append("metrics: " + (f"HTTP {code}" if code else str(text)[:80]))
    if out["kv_pool_tokens"] and out["max_model_len"]:
        out["concurrency_at_max_len"] = round(out["kv_pool_tokens"] / out["max_model_len"], 2)
    if recipe:
        measured = recipe.get("measured") if isinstance(recipe.get("measured"), dict) else {}
        needles = [v for v in (measured.get("needle_prompt_tokens") or []) if isinstance(v, (int, float))]
        tokens = max(needles) if needles else measured.get("prompt_tokens")
        if recipe.get("context_verified") is not None or tokens or recipe.get("validation_status"):
            out["verified"] = {"context_verified": recipe.get("context_verified"), "status": recipe.get("validation_status") or "",
                               "prompt_tokens": int(tokens) if isinstance(tokens, (int, float)) else None, "needle_pass": measured.get("needle_pass"),
                               "kv_cache_bytes": measured.get("kv_cache_bytes"), "source": "recipe"}
            out["sources"].append("recipe")
    return out


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# ====================================================================================== the service
def endpoint_key(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/").lower()


class SpeedCards:
    """Runs speed cards as local jobs and keeps their history; builds the capacity card on demand."""

    def __init__(self, endpoints: Callable[[], list[dict[str, Any]]], jobs: Jobs, path: Path, *, get_json: Callable[[str, float], tuple[int, Any]],
                 get_text: Callable[[str, float], tuple[int, str]], recipe: Optional[Callable[[str], dict[str, Any]]] = None, transport: Any = None,
                 clock: Callable[[], float] = time.time):
        self._endpoints = endpoints
        self.jobs = jobs
        self.path = Path(path)
        self.get_json, self.get_text = get_json, get_text
        self._recipe = recipe
        self.transport = transport
        self.clock = clock
        self._lock = threading.RLock()
        self._active: dict[str, str] = {}                      # endpoint key -> job id
        self._cap: dict[str, tuple[float, dict[str, Any]]] = {}
        raw = read_json(self.path, default={}) or {}
        cards = raw.get("cards") if isinstance(raw, dict) else None
        self._cards: dict[str, list[dict[str, Any]]] = cards if isinstance(cards, dict) else {}

    # ------------------------------------------------------------------ endpoints
    @staticmethod
    def _adhoc(base_url: str) -> dict[str, Any]:
        if urlsplit(base_url).scheme not in ("http", "https") or not urlsplit(base_url).hostname:
            raise SparkError("invalid", f"{base_url!r} is not an http(s) base URL.", hint="Use the endpoint's base URL, e.g. http://host:8000/v1.")
        return {"recipe": "", "title": base_url, "base_url": base_url, "models": [], "max_model_len": None}

    def _resolve(self, recipe: str, base_url: str, model: str) -> dict[str, Any]:
        base_url = (base_url or "").strip()
        if base_url:
            known = next((e for e in self._endpoints() if endpoint_key(e["base_url"]) == endpoint_key(base_url)), None)
            info = dict(known) if known else self._adhoc(base_url)
        else:
            listed = self._endpoints()
            if recipe:
                info = next((dict(e) for e in listed if e["recipe"] == recipe), None)
                if not info:
                    raise SparkError("not_found", f"No model server is running for {recipe!r}.", hint="endpoints shows what is running; load the recipe first.")
            elif listed:
                info = dict(listed[0])
            else:
                raise SparkError("not_found", "No model server is running.", hint="Load a recipe first (deploy_start), or pass base_url and model.")
        if model:
            info["model"] = model
        else:
            info["model"] = (info.get("models") or [""])[0] or self._first_model(info["base_url"])
        if not info["model"]:
            raise SparkError("invalid", "The model name is unknown.", hint="Pass model (the id /v1/models lists).")
        return info

    def _first_model(self, base_url: str) -> str:
        code, data = self.get_json(base_url.rstrip("/") + "/models", 3.0)
        if code == 200 and isinstance(data, dict):
            for m in data.get("data") or []:
                if isinstance(m, dict) and m.get("id"):
                    return str(m["id"])
        return ""

    def _recipe_of(self, info: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self._recipe or not info.get("recipe") or info.get("detected"):
            return None
        try:
            return self._recipe(info["recipe"])
        except Exception:  # noqa: BLE001 - a recipe that no longer loads only means "nothing verified"
            return None

    # ------------------------------------------------------------------ capacity
    def capacity(self, info: dict[str, Any], *, fresh: bool = False) -> dict[str, Any]:
        key = endpoint_key(info["base_url"])
        now = self.clock()
        with self._lock:
            hit = self._cap.get(key)
        if hit and not fresh and now - hit[0] < CAPACITY_TTL_S:
            return hit[1]
        card = capacity_card(info, self.get_json, self.get_text, self._recipe_of(info), info.get("model") or (info.get("models") or [""])[0])
        card["t"] = now
        with self._lock:
            self._cap[key] = (now, card)
        return card

    # ------------------------------------------------------------------ run
    def run(self, *, recipe: str = "", base_url: str = "", model: str = "", levels: Optional[list[int]] = None, max_tokens: int = 256,
            rounds: int = 3, warmup: int = 1, api_key: str = "") -> dict[str, Any]:
        info = self._resolve(recipe, base_url, model)
        lv = tuple(dict.fromkeys(int(x) for x in (levels or LEVELS)))
        if not lv or any(x < 1 or x > MAX_LEVEL for x in lv):
            raise SparkError("invalid", f"Concurrency levels must be between 1 and {MAX_LEVEL}.")
        key = endpoint_key(info["base_url"])
        node = info.get("head") or (info.get("nodes") or [""])[0] or ""
        settings = {"levels": list(lv), "max_tokens": int(max_tokens), "rounds": int(rounds), "warmup": int(warmup)}
        title = f"Speed card · {info.get('title') or info['base_url']}"
        capacity = self.capacity(info, fresh=True)
        with self._lock:
            running = self._active.get(key)
            if running:
                try:
                    state = self.jobs.get_raw(running)["state"]
                except SparkError:
                    state = "lost"
                if state not in FINAL:
                    raise SparkError("busy", f"A speed card is already running on {info['base_url']} (job {running}).",
                                     hint="Wait for it or cancel it with job_cancel.")

            def work(job: LocalJob) -> None:
                self._work(job, info, key, lv, settings, capacity, api_key)

            view = self.jobs.start_local(node, work, kind="speedcard", title=title,
                                         meta={"endpoint": info["base_url"], "recipe": info.get("recipe", ""), "model": info["model"], "settings": settings})
            self._active[key] = view["id"]
        return view

    def _work(self, job: LocalJob, info: dict[str, Any], key: str, levels: tuple[int, ...], settings: dict[str, Any], capacity: dict[str, Any],
              api_key: str) -> None:
        try:
            result = asyncio.run(benchmark(info["base_url"], info["model"], levels=levels, max_tokens=settings["max_tokens"], rounds=settings["rounds"],
                                           warmup=settings["warmup"], api_key=api_key, cancel=lambda: job.cancelled,
                                           progress=lambda p, line: job.report(p, line), transport=self.transport))
        except Cancelled:
            job.report(None, "cancelled")
            return
        finally:
            with self._lock:
                if self._active.get(key) == job.id:
                    del self._active[key]
        card = {"id": new_id("sc"), "t": self.clock(), "endpoint": info["base_url"], "recipe": info.get("recipe", ""), "model": info["model"],
                "settings": {**settings, "ignore_eos": result["ignore_eos"]}, "levels": result["levels"], "duration_s": result["duration_s"],
                "capacity": {k: capacity.get(k) for k in ("max_model_len", "kv_pool_tokens")}, "job": job.id}
        with self._lock:
            history = self._cards.setdefault(key, [])
            history.append(card)
            del history[:-KEEP]
            write_json_atomic(self.path, {"cards": self._cards})
        errors = sum(lv["errors"] for lv in card["levels"])
        job.report(None, f"saved {card['id']}" + (f" ({errors} failed requests)" if errors else ""))

    # ------------------------------------------------------------------ read
    def history(self, base_url: str, limit: int = KEEP) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._cards.get(endpoint_key(base_url), [])[-max(limit, 0):]))

    def snapshot(self, recipe: str = "", *, base_url: str = "", history: int = 5) -> dict[str, Any]:
        """Per running endpoint (or the one ``base_url`` names): its capacity card, the latest speed card, a few older ones and the job running now."""
        out = []
        listed = self._endpoints()
        if base_url and not any(endpoint_key(e["base_url"]) == endpoint_key(base_url) for e in listed):
            listed = [self._adhoc(base_url)]
        for e in listed:
            if recipe and e["recipe"] != recipe:
                continue
            if base_url and endpoint_key(e["base_url"]) != endpoint_key(base_url):
                continue
            info = dict(e)
            info["model"] = e.get("model") or (e.get("models") or [""])[0]
            key = endpoint_key(e["base_url"])
            with self._lock:
                job_id = self._active.get(key)
            job = None
            if job_id:
                try:
                    job = self.jobs.view(self.jobs.get_raw(job_id), log_lines=6)
                except SparkError:
                    job = None
                if job and job["state"] in FINAL:
                    job = None
            cards = self.history(e["base_url"], max(history, 1))
            out.append({"recipe": e["recipe"], "title": e["title"], "base_url": e["base_url"], "model": info["model"], "capacity": self.capacity(info),
                        "latest": cards[0] if cards else None, "history": cards[1:history] if history > 1 else [], "job": job})
        return {"endpoints": out, "now": self.clock()}
