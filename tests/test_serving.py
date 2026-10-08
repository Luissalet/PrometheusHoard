import json
import math
import time

import pytest

from prometheus_hoard.fake import fake_metrics
from prometheus_hoard.serving import INF, Serving, aggregate, compute_series, metrics_url, parse_metrics, quantile


# ------------------------------------------------------------------ the text format
def test_parse_labels_with_commas_quotes_and_special_values():
    text = "\n".join([
        "# HELP vllm:foo ignored",
        'vllm:num_requests_running{engine="0",model_name="a,b \\"quoted\\" \\\\ c"} 3.0',
        'vllm:time_to_first_token_seconds_bucket{engine="0",le="+Inf"} 7',
        'vllm:time_to_first_token_seconds_bucket{le="0.5",engine="0"} 5 1700000000',
        "vllm:num_requests_waiting NaN",
        'vllm:generation_tokens_created{engine="0"} 1.7e9',
        "vllm:kv_cache_usage_perc 0.25",
        'python_gc_objects_collected_total{generation="0"} 9',
        'vllm:broken{engine="0 5',
        "vllm:bare_without_value",
    ])
    rows = parse_metrics(text)
    names = [r[0] for r in rows]
    assert names == ["vllm:num_requests_running", "vllm:time_to_first_token_seconds_bucket", "vllm:time_to_first_token_seconds_bucket",
                     "vllm:kv_cache_usage_perc"]
    assert rows[0][1]["model_name"] == 'a,b "quoted" \\ c' and rows[0][2] == 3.0
    assert rows[1][1]["le"] == "+Inf" and rows[1][2] == 7.0
    assert rows[2][2] == 5.0 and rows[2][1]["engine"] == "0"


def test_aggregate_sums_engines_and_label_sets():
    text = "\n".join([
        'vllm:generation_tokens_total{engine="0",model_name="m"} 100.0',
        'vllm:generation_tokens_total{engine="1",model_name="m"} 50.0',
        'vllm:generation_tokens_created{engine="1",model_name="m"} 1.7e9',
        'vllm:prompt_tokens_total{engine="0"} 30',
        'vllm:prompt_tokens_cached_total{engine="0"} 10',
        'vllm:request_success_total{engine="0",finished_reason="stop"} 4',
        'vllm:request_success_total{engine="0",finished_reason="length"} 1',
        'vllm:num_requests_running{engine="0"} 2',
        'vllm:num_requests_running{engine="1"} 1',
        'vllm:kv_cache_usage_perc{engine="0"} 0.2',
        'vllm:kv_cache_usage_perc{engine="1"} 0.4',
        'vllm:engine_sleep_state{sleep_state="awake"} 1',
        'vllm:engine_sleep_state{sleep_state="weights_offloaded"} 0',
        'vllm:inter_token_latency_seconds_bucket{engine="0",le="0.05"} 4',
        'vllm:inter_token_latency_seconds_bucket{engine="1",le="0.05"} 6',
        'vllm:inter_token_latency_seconds_bucket{engine="1",le="+Inf"} 10',
        'vllm:inter_token_latency_seconds_sum{engine="1"} 1.5',
    ])
    s = aggregate(parse_metrics(text))
    assert (s["gen"], s["prompt"], s["cached"], s["req"], s["running"]) == (150.0, 30.0, 10.0, 5.0, 3.0)
    assert math.isclose(s["kv"], 0.3) and s["asleep"] is False
    assert s["h"]["itl"] == {0.05: 10.0, INF: 10.0}
    assert aggregate(parse_metrics("no metrics here\n")) is None
    assert aggregate(parse_metrics('vllm:engine_sleep_state{sleep_state="discard_all"} 1'))["asleep"] is True


def test_metrics_url_drops_the_api_prefix():
    assert metrics_url("http://spark1:8000/v1") == "http://spark1:8000/metrics"
    assert metrics_url("http://spark1:8000/") == "http://spark1:8000/metrics"


# ------------------------------------------------------------------ figures from synthetic samples
def page(gen=0, prompt=0, cached=0, req=0, running=0, waiting=0, kv=0.0, drafts=None, dtok=0, acc=0, preempt=0, itl=None, ttft=None):
    lab = '{engine="0",model_name="m"'
    lines = [f"vllm:generation_tokens_total{lab}}} {gen}", f"vllm:prompt_tokens_total{lab}}} {prompt}", f"vllm:prompt_tokens_cached_total{lab}}} {cached}",
             f'vllm:request_success_total{lab},finished_reason="stop"}} {req}', f"vllm:num_preemptions_total{lab}}} {preempt}",
             f"vllm:num_requests_running{lab}}} {running}", f"vllm:num_requests_waiting{lab}}} {waiting}", f"vllm:kv_cache_usage_perc{lab}}} {kv}"]
    if drafts is not None:
        lines += [f"vllm:spec_decode_num_drafts_total{lab}}} {drafts}", f"vllm:spec_decode_num_draft_tokens_total{lab}}} {dtok}",
                  f"vllm:spec_decode_num_accepted_tokens_total{lab}}} {acc}"]
    for name, buckets in (("inter_token_latency_seconds", itl), ("time_to_first_token_seconds", ttft)):
        for le, count in (buckets or {}).items():
            lines.append(f'vllm:{name}_bucket{lab},le="{le}"}} {count}')
    return "\n".join(lines)


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def sv(tmp_path, clock):
    s = Serving(lambda: [], tmp_path / "serving.json", clock=lambda: clock[0])
    yield s
    s._pool.shutdown(wait=False)


def feed(sv, clock, t, text, key="m"):
    clock[0] = t
    sv.ingest_text(key, text, t=t)


def view(sv, key="m", series=True):
    return sv.snapshot(key, series=series, poll=False)["endpoints"][0]


def test_rates_and_tokens_per_step(sv, clock):
    feed(sv, clock, 1000, page(gen=1000, prompt=500, cached=100, running=1, kv=0.10, drafts=100, dtok=500, acc=300))
    feed(sv, clock, 1005, page(gen=1200, prompt=900, cached=300, running=2, waiting=1, kv=0.125, drafts=145, dtok=725, acc=420))
    feed(sv, clock, 1010, page(gen=1400, prompt=1300, cached=500, running=2, waiting=1, kv=0.15, drafts=190, dtok=950, acc=540))
    now = view(sv)["now"]
    assert now["decode_tps"] == 40.0                  # 400 tokens in 10 s
    assert now["prefill_tps"] == 40.0                 # (800 - 400) / 10
    assert (now["running"], now["waiting"], now["kv_pct"]) == (2, 1, 15.0)
    assert now["tokens_per_step"] == pytest.approx(1 + 240 / 90, abs=0.01)
    assert now["acceptance"] == pytest.approx(100 * 240 / 450, abs=0.1)
    assert now["spec_cumulative"] is False and now["preempting"] is False and now["asleep"] is False
    assert now["window_s"] == 10.0


def test_speculative_figures_fall_back_to_the_whole_life_and_are_absent_without_a_drafter(sv, clock):
    feed(sv, clock, 1000, page(gen=100, drafts=100, dtok=500, acc=300))
    feed(sv, clock, 1010, page(gen=100, drafts=100, dtok=500, acc=300))    # idle: nothing moved
    now = view(sv)["now"]
    assert now["tokens_per_step"] == 4.0 and now["acceptance"] == 60.0 and now["spec_cumulative"] is True
    assert now["decode_tps"] == 0.0
    feed(sv, clock, 1000, page(gen=100), key="plain")
    feed(sv, clock, 1010, page(gen=200), key="plain")
    plain = view(sv, "plain")["now"]
    assert plain["tokens_per_step"] is None and plain["acceptance"] is None and plain["decode_tps"] == 10.0


def test_one_sample_has_levels_but_no_rates(sv, clock):
    feed(sv, clock, 1000, page(gen=5, running=1, kv=0.5))
    now = view(sv)["now"]
    assert now["decode_tps"] is None and now["running"] == 1 and now["kv_pct"] == 50.0


def test_preemptions_raise_the_flag(sv, clock):
    feed(sv, clock, 1000, page(preempt=0))
    feed(sv, clock, 1002, page(preempt=0))
    assert view(sv)["now"]["preempting"] is False
    feed(sv, clock, 1004, page(preempt=2))
    now = view(sv)["now"]
    assert now["preemptions"] == 2 and now["preempting"] is True and now["preemptions_5m"] == 2
    feed(sv, clock, 1030, page(preempt=2))
    now = view(sv)["now"]
    assert now["preempting"] is False and now["preemptions_5m"] == 2        # the last five minutes still remember it


def test_histogram_quantile_matches_prometheus():
    buckets = {0.1: 10.0, 0.5: 50.0, 1.0: 90.0, INF: 100.0}
    assert quantile(0.5, buckets) == pytest.approx(0.5)
    assert quantile(0.25, buckets) == pytest.approx(0.25)
    assert quantile(0.95, buckets) == 1.0               # falls in +Inf: the highest finite bound
    assert quantile(0.05, buckets) == pytest.approx(0.05)    # first bucket starts at zero
    assert quantile(0.5, {0.1: 0.0, INF: 0.0}) is None
    assert quantile(0.5, {0.1: 4.0}) is None


def test_latency_percentiles_use_the_last_five_minutes(sv, clock):
    old = {0.025: 100, 0.05: 100, 0.5: 100, INF: 100}                 # an old, fast server life
    feed(sv, clock, 1000, page(itl=old, ttft={0.5: 10, 1.0: 10, INF: 10}))
    new = {0.025: 100, 0.05: 100 + 100, 0.5: 100 + 100, INF: 100 + 100}  # since then: 100 observations between 0.025 and 0.05
    feed(sv, clock, 1400, page(itl=new, ttft={0.5: 10, 1.0: 10, INF: 10}))
    feed(sv, clock, 1500, page(itl=new, ttft={0.5: 10, 1.0: 10, INF: 10}))
    lat = view(sv)["latency"]
    # the delta between 1400 and 1500 is empty -> cumulative; the sample at 1000 is outside the five minutes
    assert lat["itl"]["cumulative"] is True
    feed(sv, clock, 1510, page(itl={0.025: 100, 0.05: 260, 0.5: 300, INF: 300}, ttft={0.5: 10, 1.0: 10, INF: 10}))
    lat = view(sv)["latency"]
    assert lat["itl"]["cumulative"] is False
    assert 0.025 <= lat["itl"]["p50"] <= 0.05 and lat["itl"]["p95"] <= 0.5
    assert lat["stream_tps"] == pytest.approx(1 / lat["itl"]["p50"], abs=0.1)
    assert lat["ttft"]["cumulative"] is True and 0.25 <= lat["ttft"]["p50"] <= 0.5


def test_latency_delta_ignores_what_happened_before_the_window(sv, clock):
    feed(sv, clock, 1000, page(itl={0.01: 1000, 0.5: 1000, INF: 1000}))                     # 1000 very fast tokens long ago
    feed(sv, clock, 1400, page(itl={0.01: 1000, 0.5: 1000, INF: 1000}))
    feed(sv, clock, 1410, page(itl={0.01: 1000, 0.5: 1100, INF: 1100}))                     # 100 slow ones just now
    lat = view(sv)["latency"]["itl"]
    assert lat["cumulative"] is False and lat["p50"] > 0.1


def test_series_is_downsampled(sv, clock):
    for i in range(400):
        feed(sv, clock, 1000 + i * 1.5, page(gen=i * 60, running=i % 3, kv=0.01 * (i % 5)))
    s = view(sv)["series"]
    assert 0 < len(s) <= 120
    assert all(set(p) == {"t", "decode_tps", "prefill_tps", "running", "kv_pct"} for p in s)
    assert s[-1]["decode_tps"] == pytest.approx(40.0, abs=0.1)
    assert compute_series([]) == []


# ------------------------------------------------------------------ persistent totals
def test_counter_reset_keeps_the_totals_growing(tmp_path, clock):
    path = tmp_path / "serving.json"
    sv = Serving(lambda: [], path, clock=lambda: clock[0])
    feed(sv, clock, 1000, page(gen=100, prompt=50, cached=10, req=2))
    feed(sv, clock, 1010, page(gen=300, prompt=150, cached=40, req=5))
    assert view(sv)["totals"]["generation_tokens"] == 300
    feed(sv, clock, 1020, page(gen=20, prompt=10, cached=0, req=1))          # the server restarted
    tot = view(sv)["totals"]
    assert (tot["generation_tokens"], tot["prompt_tokens"], tot["cached_tokens"], tot["requests"]) == (320, 160, 40, 6)
    feed(sv, clock, 1030, page(gen=70, prompt=30, cached=5, req=2))
    assert view(sv)["totals"]["generation_tokens"] == 370
    sv.save(force=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["endpoints"]["m"]["offset"]["generation_tokens"] == 300 and saved["since"] == 1000.0

    # the app restarted too: the totals come back, and a counter that kept growing continues without a jump
    clock[0] = 2000
    again = Serving(lambda: [], path, clock=lambda: clock[0])
    feed(again, clock, 2000, page(gen=100, prompt=40, cached=5, req=3))
    snap = again.snapshot(poll=False)
    assert snap["endpoints"][0]["totals"]["generation_tokens"] == 400
    assert snap["totals"]["generation_tokens"] == 400 and snap["totals"]["since"] == 1000.0
    assert snap["totals"]["endpoints"][0]["recipe"] == "m" and snap["totals"]["endpoints"][0]["running"] is True
    # a stopped endpoint stays in the overall total
    quiet = Serving(lambda: [], path, clock=lambda: clock[0]).snapshot(poll=False)
    assert quiet["endpoints"] == [] and quiet["totals"]["generation_tokens"] == 370
    assert quiet["totals"]["endpoints"][0]["running"] is False


def test_saves_are_rate_limited(tmp_path, clock):
    path = tmp_path / "serving.json"
    sv = Serving(lambda: [], path, clock=lambda: clock[0])
    feed(sv, clock, 1000, page(gen=1))
    sv.save()
    first = path.read_text(encoding="utf-8")
    feed(sv, clock, 1010, page(gen=500))
    sv.save()
    assert path.read_text(encoding="utf-8") == first            # 10 s later: not yet
    feed(sv, clock, 1040, page(gen=900))
    sv.save()
    assert json.loads(path.read_text(encoding="utf-8"))["endpoints"]["m"]["last"]["generation_tokens"] == 900


# ------------------------------------------------------------------ the sampler
def test_a_failing_endpoint_does_not_break_the_others(tmp_path, clock):
    eps = [{"recipe": "good", "title": "Good", "base_url": "http://a:8000/v1", "models": ["g"], "nodes": ["spark1"], "head": "spark1", "engine": "vllm"},
           {"recipe": "down", "title": "Down", "base_url": "http://b:8000/v1", "models": ["d"], "nodes": ["spark2"], "head": "spark2", "engine": "vllm"},
           {"recipe": "boom", "title": "Boom", "base_url": "http://c:8000/v1", "models": ["b"], "nodes": ["spark3"], "head": "spark3", "engine": "vllm"},
           {"recipe": "other", "title": "Other", "base_url": "http://d:8000/v1", "models": ["o"], "nodes": ["spark3"], "head": "spark3", "engine": "sglang"}]
    asked = []

    def getter(url, timeout):
        asked.append(url)
        if "//a:" in url:
            return 200, page(gen=int(clock[0]))
        if "//b:" in url:
            return 0, "connection refused"
        if "//d:" in url:
            return 200, "sglang_metric 1\n"
        raise RuntimeError("kaboom")

    sv = Serving(lambda: eps, tmp_path / "s.json", getter=getter, clock=lambda: clock[0], fast_s=0, refresh_s=0)
    sv.poll_once()
    clock[0] += 2
    snap = sv.snapshot(poll=True)
    by = {e["recipe"]: e for e in snap["endpoints"]}
    assert by["good"]["ok"] is True and by["good"]["now"]["decode_tps"] == 1.0
    assert by["down"]["ok"] is False and "refused" in by["down"]["error"]
    assert by["boom"]["ok"] is False and "kaboom" in by["boom"]["error"]
    assert by["other"]["ok"] is False and by["other"]["error"] == "no vllm metrics"
    assert "http://a:8000/metrics" in asked
    # the list follows the recipes: a server that stopped disappears, its totals stay
    eps.pop(0)
    sv.refresh_list()
    assert "good" not in {e["recipe"] for e in sv.snapshot(poll=False)["endpoints"]}
    assert any(p["recipe"] == "good" for p in sv.snapshot(poll=False)["totals"]["endpoints"])
    sv._pool.shutdown(wait=False)


def test_figures_of_a_server_that_stopped_answering_go_blank(tmp_path, clock):
    ok = [True]
    eps = [{"recipe": "x", "title": "X", "base_url": "http://a:8000/v1", "models": [], "nodes": [], "head": "", "engine": "vllm"}]

    def getter(url, timeout):
        return (200, page(gen=int(clock[0] * 10))) if ok[0] else (500, "")

    sv = Serving(lambda: eps, tmp_path / "s.json", getter=getter, clock=lambda: clock[0], fast_s=0, idle_s=0, refresh_s=0)
    sv.poll_once()
    clock[0] += 2
    sv.poll_once()
    assert sv.snapshot(poll=False)["endpoints"][0]["now"]["decode_tps"] == 10.0
    ok[0] = False
    clock[0] += 2
    sv.poll_once()
    assert sv.snapshot(poll=False)["endpoints"][0]["now"]["decode_tps"] == 10.0     # one missed scrape is not an outage
    clock[0] += 20
    sv.poll_once()
    e = sv.snapshot(poll=False)["endpoints"][0]
    assert e["ok"] is False and e["error"] == "HTTP 500" and e["now"]["decode_tps"] is None
    sv._pool.shutdown(wait=False)


def test_scrape_interval_follows_the_last_look(tmp_path, clock):
    sv = Serving(lambda: [], tmp_path / "s.json", clock=lambda: clock[0])
    assert sv.interval == 10.0
    sv.touch()
    assert sv.interval == 2.0
    clock[0] += 61
    assert sv.interval == 10.0
    sv._pool.shutdown(wait=False)


def test_the_background_thread_samples_and_stops(tmp_path):
    seen = []

    def getter(url, timeout):
        seen.append(url)
        return 200, page(gen=len(seen) * 10)

    eps = [{"recipe": "x", "title": "X", "base_url": "http://a:8000/v1", "models": [], "nodes": [], "head": "", "engine": "vllm"}]
    sv = Serving(lambda: eps, tmp_path / "s.json", getter=getter, fast_s=0.05, idle_s=0.05)
    sv.start()
    try:
        deadline = time.time() + 5
        while len(seen) < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert len(seen) >= 3
        assert sv.snapshot()["endpoints"][0]["samples"] >= 2
    finally:
        sv.stop()
    assert (tmp_path / "s.json").exists()
    n = len(seen)
    time.sleep(0.2)
    assert len(seen) == n


# ------------------------------------------------------------------ the demo cluster, the route and the tool
def test_the_demo_server_speaks_prometheus():
    rows = parse_metrics(fake_metrics("glm", 5.0, drafter=True, seed=8000))
    names = {r[0] for r in rows}
    assert {"vllm:generation_tokens_total", "vllm:spec_decode_num_drafts_total", "vllm:time_to_first_token_seconds_bucket"} <= names
    assert not any(n.endswith("_created") for n in names)
    plain = {r[0] for r in parse_metrics(fake_metrics("qwen", 5.0, drafter=False))}
    assert "vllm:spec_decode_num_drafts_total" not in plain
    s = aggregate(parse_metrics(fake_metrics("glm", 125.0, drafter=True)))
    assert 1 + s["acc"] / s["drafts"] == pytest.approx(4.35, abs=0.1) and s["acc"] / s["dtok"] == pytest.approx(0.588, abs=0.01)


def test_api_serving_with_the_demo_cluster(client, services, call, world):
    empty = client.get("/api/serving").json()
    assert empty["endpoints"] == [] and empty["totals"]["generation_tokens"] == 0 and empty["poll_s"] == 2.0
    call("deploy_start", {"recipe": "glm53-tp3", "wait": True})
    services.serving.fast_s = 0
    services.serving.refresh_s = 0
    client.get("/api/serving")
    time.sleep(0.6)
    body = client.get("/api/serving").json()
    assert [e["recipe"] for e in body["endpoints"]] == ["glm53-tp3"]
    e = body["endpoints"][0]
    assert e["ok"] is True and e["models"] == ["glm-5.3-flash"] and e["nodes"] == ["spark1", "spark2", "spark3"] and e["head"] == "spark1"
    assert e["metrics_url"].endswith(":8000/metrics") and e["base_url"].endswith(":8000/v1")
    assert e["now"]["decode_tps"] > 10 and e["now"]["running"] >= 1 and e["now"]["kv_pct"] > 0
    assert 1 < e["now"]["tokens_per_step"] < 8 and 30 < e["now"]["acceptance"] < 90
    assert e["totals"]["generation_tokens"] > 0 and e["totals"]["since"]
    assert isinstance(e["series"], list) and e["series"]
    assert body["totals"]["generation_tokens"] == e["totals"]["generation_tokens"]
    assert [x["recipe"] for x in client.get("/api/serving?recipe=nope").json()["endpoints"]] == []
    assert "series" not in client.get("/api/serving?series=false").json()["endpoints"][0]
    ov = client.get("/api/overview").json()
    assert set(ov["serving"]["glm53-tp3"]) == {"ok", "decode_tps", "running", "kv_pct"}
    services.serving.save(force=True)
    assert json.loads((services.config.data_dir / "serving.json").read_text(encoding="utf-8"))["endpoints"]["glm53-tp3"]["last"]["generation_tokens"] > 0
    call("deploy_stop", {"recipe": "glm53-tp3"})
    after = client.get("/api/serving").json()
    assert after["endpoints"] == [] and after["totals"]["generation_tokens"] > 0       # what was served stays counted


def test_serving_stats_tool(call, services):
    assert call("serving_stats")["endpoints"] == []
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    services.serving.fast_s = 0
    services.serving.refresh_s = 0
    call("serving_stats")
    time.sleep(0.5)
    out = call("serving_stats", {"recipe": "qwen38-27b-1m"})
    e = out["endpoints"][0]
    assert e["recipe"] == "qwen38-27b-1m" and e["now"]["decode_tps"] > 10 and "series" not in e
    assert e["now"]["tokens_per_step"] is None                     # this demo model has no drafter
    assert "series" in call("serving_stats", {"series": True})["endpoints"][0]
    assert call("serving_stats", {"recipe": "other"})["endpoints"] == []
    assert call("serving_stats", {"recipe": "x" * 80}, status=400)["code"] == "invalid_arguments"
