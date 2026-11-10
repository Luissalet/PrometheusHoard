"""Long per-minute history of the Sparks, and incidents when a Spark or a model server stops answering."""
import time

from prometheus_hoard.history import MetricsStore


def test_store_averages_minutes_and_leaves_gaps(tmp_path):
    now = [1_800_000_000.0]
    store = MetricsStore(tmp_path / "h.sqlite3", clock=lambda: now[0])
    base = 1_800_000_000 - 3600
    for minute in (0, 1, 2, 10):
        for k in range(4):
            store.add("spark1", {"t": base + minute * 60 + k * 15, "gpu": 50 + k * 10, "cpu": 5, "mem": 40, "mem_used": 1e9, "temp": 60 + k,
                                 "power": 100})
    out = store.query(["spark1"], 1)
    assert out["step"] == 60 and len(out["t"]) == len(out["nodes"]["spark1"]["gpu"])
    i0 = out["t"].index(base)
    s = out["nodes"]["spark1"]
    assert s["gpu"][i0] == 65 and s["gpu_max"][i0] == 80 and s["temp_max"][i0] == 63
    assert s["gpu"][i0 + 5] is None                       # nothing came in that minute: a gap, not a zero
    st = out["stats"]["spark1"]
    assert st["minutes"] == 4 and st["power_avg"] == 100 and st["energy_kwh"] == round(4 * 100 * 60 / 3.6e6, 3)
    week = store.query(["spark1"], 24 * 7)
    assert week["step"] > 60 and len(week["t"]) <= 400


def test_store_prunes_old_minutes(tmp_path):
    now = [1_800_000_000.0]
    store = MetricsStore(tmp_path / "h.sqlite3", days=lambda: 1, clock=lambda: now[0])
    store.add("spark1", {"t": now[0] - 3 * 86400, "gpu": 1})
    store.add("spark1", {"t": now[0] - 60, "gpu": 2})
    store.add("spark1", {"t": now[0], "gpu": 3})
    assert store.prune() == 1
    assert store.size()["rows"] == 1


def test_probes_feed_the_history_tool_and_route(call, client, services):
    out = call("metrics_history", {"hours": 1})
    assert set(out["nodes"]) == {"spark1", "spark2", "spark3"} and out["names"]["spark1"] == "Spark1"
    assert any(v is not None for v in out["nodes"]["spark1"]["gpu"])
    one = client.get("/api/history", params={"node": "spark2", "hours": 6}).json()
    assert list(one["nodes"]) == ["spark2"] and one["stats"]["spark2"]["minutes"] >= 1


def _later(services, seconds):
    t0 = time.time()
    services.alerts.clock = lambda: t0 + seconds


def test_a_spark_that_stops_answering_opens_and_closes_an_incident(call, services, world):
    sent = []
    services.alerts.notifier = lambda title, body, **kw: sent.append((title, body, kw)) or {"ok": True}
    world.power["spark2"] = "off"                        # unplugged: the app did not do it
    services.cluster.probe_all()
    _later(services, 30)
    services.alerts.check()
    assert services.alerts.active() == []                # not yet: it must keep failing alert_after_s
    _later(services, 200)
    services.alerts.check()
    act = call("alerts")["active"]
    assert len(act) == 1 and act[0]["kind"] == "spark" and act[0]["target"] == "spark2" and act[0]["expected"] is False
    assert sent and sent[0][0] == "Spark2 no responde" and sent[0][2]["priority"] == "high"
    assert any(e["type"] == "alert.spark_down" and e["target"] == "spark2" for e in services.recent_events())
    assert call("sparks_overview")["alerts"]["active"][0]["target"] == "spark2"
    world.power.pop("spark2")
    services.cluster.probe_all()
    services.alerts.check()
    out = call("alerts")
    assert out["active"] == [] and out["incidents"][0]["ended"] and out["incidents"][0]["down_s"] >= 0
    assert any(e["type"] == "alert.spark_up" and e["down_s"] >= 0 for e in services.recent_events())
    assert sent[-1][0] == "Spark2 vuelve a responder"


def test_powering_off_from_the_app_is_expected_and_not_notified(services, world):
    sent = []
    services.alerts.notifier = lambda *a, **kw: sent.append(a) or {"ok": True}
    world.power["spark3"] = "off"
    services.cluster.probe_all()
    services.cluster.node("spark3").power_state = "off"
    _later(services, 500)
    services.alerts.check()
    act = services.alerts.active()
    assert act and act[0]["expected"] is True and sent == []


def test_a_model_server_that_stops_serving_is_an_incident_but_unloading_is_not(call, services, world):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    services.recipes.deployments(check=True)
    time.sleep(0.3)
    services.alerts.check()
    assert services.alerts.active() == []
    with world.lock:
        world.serving.pop("qwen38-27b-1m")              # the server died; this app still holds the recipe as running
    services.recipes.probe_health(services.recipes.load("qwen38-27b-1m"))
    services.alerts.check()
    _later(services, 300)
    services.alerts.check()
    act = services.alerts.active()
    assert len(act) == 1 and act[0]["kind"] == "server" and act[0]["target"] == "qwen38-27b-1m" and act[0]["node"] == "spark3"
    assert any(e["type"] == "alert.server_down" for e in services.recent_events())
    call("deploy_stop", {"recipe": "qwen38-27b-1m"})     # unloading it closes the incident without a "back up" notice
    services.alerts.check()
    assert services.alerts.active() == []


def test_alert_settings_and_switching_off(call, services, world):
    s = call("settings_set", {"alert_after_s": 5, "history_days": 0, "energy_price_kwh": 0.18, "alert_watch": False})
    assert s["alert_after_s"] == 20 and s["history_days"] == 1 and s["energy_price_kwh"] == 0.18 and s["alert_watch"] is False
    world.power["spark1"] = "off"
    services.cluster.probe_all()
    _later(services, 500)
    services.alerts.check()
    assert services.alerts.active() == [] and call("alerts")["enabled"] is False
    assert call("metrics_history", {"hours": 1})["energy_price_kwh"] == 0.18


def test_a_server_started_elsewhere_is_watched_once_seen_answering(call, services, world):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    services.recipes.state["qwen38-27b-1m"] = {"name": "qwen38-27b-1m", "state": "stopped"}   # as if another tool had started it
    services.recipes.probe_health(services.recipes.load("qwen38-27b-1m"))
    services.alerts.check()
    with world.lock:
        world.serving.pop("qwen38-27b-1m")
    services.recipes.probe_health(services.recipes.load("qwen38-27b-1m"))
    services.alerts.check()
    _later(services, 300)
    services.alerts.check()
    act = services.alerts.active()
    assert len(act) == 1 and act[0]["target"] == "qwen38-27b-1m"
