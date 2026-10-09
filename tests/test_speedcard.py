import asyncio
import json
import time

import httpx
import pytest

from prometheus_hoard import speedcard
from prometheus_hoard.speedcard import Cancelled, benchmark, capacity_card, percentile, summarize_level, summarize_round


# ------------------------------------------------------------------ a scripted server
class _Body(httpx.AsyncByteStream):
    def __init__(self, parts, delay):
        self.parts, self.delay = parts, delay

    async def __aiter__(self):
        for i, part in enumerate(self.parts):
            if i and self.delay:
                await asyncio.sleep(self.delay)
            yield part

    async def aclose(self):
        return None


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


class Server:
    """Answers chat completions like a vLLM server: `tokens` deltas (the first `reasoning` of them as reasoning), then usage (or not)."""

    def __init__(self, *, tokens=8, reasoning=3, usage=True, delay=0.002, fail=lambda n: None, reject_ignore_eos=False):
        self.tokens, self.reasoning, self.usage, self.delay, self.fail, self.reject = tokens, reasoning, usage, delay, fail, reject_ignore_eos
        self.prompts: list[str] = []
        self.bodies: list[dict] = []
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        body = json.loads(request.content)
        self.bodies.append(body)
        self.prompts.append(body["messages"][0]["content"])
        if self.reject and body.get("ignore_eos"):
            return httpx.Response(422, json={"error": "unknown field ignore_eos"})
        bad = self.fail(self.calls)
        if bad == "http":
            return httpx.Response(500, text="boom")
        n = min(self.tokens, body["max_tokens"])
        parts = []
        for i in range(n):
            key = "reasoning_content" if i < self.reasoning else "content"
            parts.append(sse({"choices": [{"delta": {key: "x"}, "finish_reason": None}]}))
        if bad == "truncate":
            return httpx.Response(200, stream=_Body(parts, self.delay))
        parts.append(sse({"choices": [{"delta": {}, "finish_reason": "length"}]}))
        if self.usage and body.get("stream_options", {}).get("include_usage"):
            parts.append(sse({"choices": [], "usage": {"completion_tokens": n + 2}}))      # the server counts more than the deltas
        parts.append(b"data: [DONE]\n\n")
        return httpx.Response(200, stream=_Body(parts, self.delay))


def run(server, **kw):
    kw.setdefault("levels", (1, 4))
    kw.setdefault("max_tokens", 8)
    return asyncio.run(benchmark("http://sp.test/v1", "m", transport=httpx.MockTransport(server), **kw))


# ------------------------------------------------------------------ arithmetic
def test_percentile_interpolates():
    assert percentile([], 50) is None and percentile([4.0], 95) == 4.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([0, 10], 95) == pytest.approx(9.5)


def test_round_and_level_summaries():
    results = [{"ok": True, "tokens": 101, "decode_s": 1.0, "ttft": 0.2, "source": "usage"},
               {"ok": True, "tokens": 51, "decode_s": 1.0, "ttft": 0.4, "source": "usage"},
               {"ok": False, "tokens": 0, "error": "HTTP 500 boom"}]
    r = summarize_round(results, wall_s=2.0)
    assert (r["requests"], r["ok"], r["errors"], r["tokens"]) == (3, 2, 1, 152)
    assert r["agg_tps"] == 76.0 and r["stream_tps"] == 75.0 and r["ttft_p50"] == 0.3 and r["error_samples"] == ["HTTP 500 boom"]
    dead = summarize_round([{"ok": False, "tokens": 0, "error": "x"}], 1.0)
    assert dead["agg_tps"] is None and dead["ok"] == 0
    lvl = summarize_level(8, [r, dead, {**r, "agg_tps": 80.0}, {**r, "agg_tps": 60.0}])
    assert lvl["agg_tps"] == 76.0 and lvl["errors"] == 4 and lvl["requests"] == 10   # the dead round is out of the median, in the errors
    assert summarize_level(1, [dead])["agg_tps"] is None


# ------------------------------------------------------------------ the benchmark
def test_benchmark_levels_prompts_warmup_and_tokens_from_usage():
    server = Server(tokens=8, reasoning=3)
    out = run(server, levels=(1, 4), rounds=3, warmup=1)
    assert [lv["n"] for lv in out["levels"]] == [1, 4]
    assert server.calls == (1 + 4) * (1 + 3)                      # one warm-up round plus three measured, per level
    assert len(set(server.prompts)) == server.calls               # nobody repeats a prompt: the prefix cache cannot answer
    assert all(p.startswith("[request ") for p in server.prompts)
    assert all(b["stream"] and b["max_tokens"] == 8 and b["stream_options"] == {"include_usage": True} for b in server.bodies)
    for lv in out["levels"]:
        assert lv["errors"] == 0 and lv["tokens_source"] == "usage"
        assert lv["tokens"] == lv["requests"] * 10                 # usage said n + 2: reasoning and content both counted
        assert lv["agg_tps"] > 0 and lv["stream_tps"] > 0 and 0 < lv["ttft_p50"] <= lv["ttft_p95"]
        assert len(lv["rounds"]) == 3
    assert out["levels"][1]["agg_tps"] > out["levels"][0]["agg_tps"]   # four streams at once produce more than one


def test_benchmark_counts_deltas_when_the_server_sends_no_usage():
    out = run(Server(tokens=8, usage=False), levels=(2,), rounds=1, warmup=0)
    lv = out["levels"][0]
    assert lv["tokens_source"] == "deltas" and lv["tokens"] == 16 and lv["errors"] == 0


def test_benchmark_errors_are_counted_and_do_not_add_throughput():
    server = Server(tokens=6, fail=lambda n: "http" if n % 4 == 0 else ("truncate" if n % 4 == 1 else None))
    out = run(server, levels=(4,), rounds=2, warmup=0)
    lv = out["levels"][0]
    assert lv["requests"] == 8 and lv["errors"] == 4
    assert lv["tokens"] == 4 * 8                                    # only the two good requests per round count
    assert any("HTTP 500" in e for e in lv["error_samples"]) and any("before it finished" in e for e in lv["error_samples"])


def test_benchmark_without_ignore_eos_when_the_server_refuses_it():
    server = Server(reject_ignore_eos=True)
    out = run(server, levels=(2,), rounds=1, warmup=1)
    assert out["ignore_eos"] is False and out["levels"][0]["errors"] == 0
    assert any(b.get("ignore_eos") for b in server.bodies) and not server.bodies[-1].get("ignore_eos")


def test_benchmark_stops_when_cancelled():
    server = Server(tokens=200, delay=0.01)
    started = time.monotonic()
    flag = {"stop": False}

    def cancel():
        flag["stop"] = flag["stop"] or time.monotonic() - started > 0.3
        return flag["stop"]

    with pytest.raises(Cancelled):
        run(server, levels=(8,), max_tokens=200, rounds=3, warmup=1, cancel=cancel)
    assert time.monotonic() - started < 3


def test_benchmark_reports_progress():
    seen = []
    run(Server(), levels=(1, 2), rounds=1, warmup=1, progress=lambda p, line: seen.append((p, line)))
    assert seen[0][0] == 0.0 and seen[-1][0] == 1.0 and any("warm-up" in line for _, line in seen)


# ------------------------------------------------------------------ the capacity card
def _json(models):
    return lambda url, timeout: (200, {"data": models}) if url.endswith("/models") else (404, "")


def test_capacity_card_reads_models_metrics_and_recipe():
    metrics = 'vllm:cache_config_info{block_size="16",cache_dtype="fp8",num_gpu_blocks="65536",num_cpu_blocks="None"} 1.0\n'
    recipe = {"context_verified": True, "validation_status": "context_verified",
              "measured": {"prompt_tokens": 1000000, "needle_pass": True, "needle_prompt_tokens": [999995, 1000000], "kv_cache_bytes": 8589934592}}
    card = capacity_card({"base_url": "http://h:8000/v1", "max_model_len": 4096}, _json([{"id": "m", "max_model_len": 1048576}]), lambda u, t: (200, metrics),
                         recipe, "m")
    assert card["max_model_len"] == 1048576 and card["kv_blocks"] == 65536 and card["block_size"] == 16
    assert card["kv_pool_tokens"] == 1048576 and card["concurrency_at_max_len"] == 1.0 and card["cache_dtype"] == "fp8"
    assert card["verified"] == {"context_verified": True, "status": "context_verified", "prompt_tokens": 1000000, "needle_pass": True,
                                "kv_cache_bytes": 8589934592, "source": "recipe"}
    assert set(card["sources"]) == {"models", "metrics", "recipe"} and card["errors"] == []


def test_capacity_card_survives_missing_sources():
    card = capacity_card({"base_url": "http://h:8000/v1", "max_model_len": 32768}, lambda u, t: (0, "refused"), lambda u, t: (404, "nope"))
    assert card["max_model_len"] == 32768 and card["kv_pool_tokens"] is None and card["verified"] is None
    assert len(card["errors"]) == 2
    other = capacity_card({"base_url": "http://h:8000/v1"}, _json([{"id": "m"}]), lambda u, t: (200, "vllm:num_requests_running 1\n"))
    assert other["kv_pool_tokens"] is None and "no vllm:cache_config_info" in other["errors"][0]
    odd = capacity_card({"base_url": "http://h/v1"}, _json([{"id": "m"}]), lambda u, t: (200, 'vllm:cache_config_info{block_size="None",num_gpu_blocks="None"} 1\n'))
    assert odd["kv_pool_tokens"] is None and odd["concurrency_at_max_len"] is None


# ------------------------------------------------------------------ through the app (demo cluster, tools)
def _wait_job(call, job_id, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        job = call("job_get", {"job": job_id})
        if job["state"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("the speed card job did not finish")


SMALL = {"levels": [1, 4], "max_tokens": 16, "rounds": 2}


def test_speed_card_run_needs_confirm_and_a_server(call, services):
    assert call("speed_card_run", {}, status=400)["code"] == "confirm_required"
    assert call("speed_card_run", {"confirm": True}, status=404)["code"] == "not_found"        # nothing is running
    assert call("speed_card")["endpoints"] == []
    assert call("speed_card_run", {"confirm": True, "base_url": "file:///etc/passwd"}, status=400)["code"] == "invalid"


def test_speed_card_run_rejects_bad_levels(call):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    for levels in ([0], [1, 9999]):
        assert call("speed_card_run", {"confirm": True, "recipe": "qwen38-27b-1m", "levels": levels}, status=400)["code"] == "invalid"


def test_speed_card_end_to_end(call, services):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    before = call("speed_card", {"recipe": "qwen38-27b-1m"})["endpoints"][0]
    assert before["latest"] is None and before["job"] is None
    cap = before["capacity"]
    assert cap["max_model_len"] == 1048576 and cap["block_size"] == 16 and cap["kv_pool_tokens"] == cap["kv_blocks"] * 16 > 0

    started = call("speed_card_run", {"recipe": "qwen38-27b-1m", "confirm": True, **SMALL})["job"]
    assert started["kind"] == "speedcard" and started["state"] == "running" and started["node"] == "spark3"
    job = _wait_job(call, started["id"])
    assert job["state"] == "done" and job["progress"] == 1.0 and "saved" in job["log"]

    card = call("speed_card", {"recipe": "qwen38-27b-1m"})["endpoints"][0]
    latest = card["latest"]
    assert latest["recipe"] == "qwen38-27b-1m" and latest["model"] == "qwen3.8-27b" and latest["settings"]["max_tokens"] == 16
    assert [lv["n"] for lv in latest["levels"]] == [1, 4] and all(lv["agg_tps"] > 0 and lv["errors"] == 0 for lv in latest["levels"])
    assert latest["levels"][0]["tokens_source"] == "usage" and card["job"] is None
    saved = json.loads(services.config.speedcards_path.read_text())
    assert list(saved["cards"].values())[0][0]["id"] == latest["id"]
    assert call("jobs_list", {"state": "done"})["jobs"][0]["id"] == started["id"]


def test_second_run_on_the_same_endpoint_is_refused_and_a_run_can_be_cancelled(call, services):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    args = {"recipe": "qwen38-27b-1m", "confirm": True, "levels": [8], "max_tokens": 512, "rounds": 7}
    first = call("speed_card_run", args)["job"]
    refused = call("speed_card_run", args, status=409)
    assert refused["code"] == "busy" and first["id"] in refused["error"]
    assert call("speed_card", {"recipe": "qwen38-27b-1m"})["endpoints"][0]["job"]["id"] == first["id"]
    assert call("job_cancel", {"job": first["id"]})["state"] == "cancelled"
    deadline = time.time() + 10
    while services.speedcards._active and time.time() < deadline:
        time.sleep(0.05)
    assert not services.speedcards._active
    assert call("speed_card", {"recipe": "qwen38-27b-1m"})["endpoints"][0]["latest"] is None          # a cancelled run saves nothing
    assert call("job_get", {"job": first["id"]})["state"] == "cancelled"
    again = call("speed_card_run", {**args, "levels": [1], "max_tokens": 16, "rounds": 1})["job"]
    assert _wait_job(call, again["id"])["state"] == "done"


def test_history_keeps_the_last_twenty_per_endpoint(call, services):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    cards = services.speedcards
    key = speedcard.endpoint_key(services.recipes.endpoints()[0]["base_url"])
    cards._cards[key] = [{"id": f"old{i}", "t": i} for i in range(25)]
    again = call("speed_card_run", {"recipe": "qwen38-27b-1m", "confirm": True, "levels": [1], "max_tokens": 16, "rounds": 1})["job"]
    _wait_job(call, again["id"])
    assert len(cards._cards[key]) == speedcard.KEEP
    shown = call("speed_card", {"recipe": "qwen38-27b-1m", "history": 3})["endpoints"][0]
    assert shown["latest"]["job"] == again["id"] and len(shown["history"]) == 2


def test_speed_card_by_base_url_and_history_survives_a_restart(call, services, tmp_path):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    url = services.recipes.endpoints()[0]["base_url"]
    job = call("speed_card_run", {"base_url": url, "confirm": True, **SMALL})["job"]
    _wait_job(call, job["id"])
    reloaded = speedcard.SpeedCards(lambda: [], services.jobs, services.config.speedcards_path, get_json=lambda u, t: (0, ""), get_text=lambda u, t: (0, ""))
    assert reloaded.history(url)[0]["job"] == job["id"]
    adhoc = call("speed_card", {"base_url": "http://192.0.2.99:9000/v1"})["endpoints"][0]
    assert adhoc["latest"] is None and adhoc["capacity"]["errors"]
