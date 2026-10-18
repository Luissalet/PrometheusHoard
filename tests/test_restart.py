import threading
import time


def test_restart_stops_every_rank_before_start_and_preserves_recipe(call, services, world):
    call("deploy_start", {"recipe": "glm53-tp3", "wait": True})
    recipe = services.recipes.folder / "glm53-tp3" / "recipe.json"
    before = recipe.read_bytes()
    old_jobs = set(world.jobs)
    result = call("deploy_restart", {"recipe": "glm53-tp3", "wait": True})
    assert result["state"] == "running"
    jobs = [j for key, j in world.jobs.items() if key not in old_jobs]
    stops = [j for j in jobs if "stop.sh" in j["cmd"]]
    starts = [j for j in jobs if "start.sh" in j["cmd"]]
    assert {j["node"] for j in stops} == {"spark1", "spark2", "spark3"}
    assert {j["node"] for j in starts} == {"spark1", "spark2", "spark3"}
    assert max(j["start"] for j in stops) < min(j["start"] for j in starts)
    assert recipe.read_bytes() == before
    call("deploy_stop", {"recipe": "glm53-tp3"})


def test_restart_returns_progress_and_refuses_a_duplicate(call, services, monkeypatch):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    entered, release = threading.Event(), threading.Event()
    original = services.recipes._run_step

    def held(recipe, node, script, *args, **kwargs):
        if script == recipe["scripts"]["stop"]:
            entered.set()
            assert release.wait(10)
        return original(recipe, node, script, *args, **kwargs)

    monkeypatch.setattr(services.recipes, "_run_step", held)
    try:
        result = call("deploy_restart", {"recipe": "qwen38-27b-1m"})
        assert entered.wait(2)
        assert result["state"] == "stopping"
        assert "Reiniciando" in services.recipes.state["qwen38-27b-1m"]["message"]
        assert services.recipes.detected() == []
        assert call("deploy_restart", {"recipe": "qwen38-27b-1m"}, status=409)["code"] == "busy"
        assert call("deploy_start", {"recipe": "qwen38-27b-1m"}, status=409)["code"] == "busy"
    finally:
        release.set()
    for _ in range(100):
        if services.recipes.state["qwen38-27b-1m"]["state"] == "running":
            break
        time.sleep(0.1)
    assert services.recipes.state["qwen38-27b-1m"]["state"] == "running"
    call("deploy_stop", {"recipe": "qwen38-27b-1m"})


def test_restart_never_starts_after_a_failed_worker_stop(call, services, monkeypatch, world):
    call("deploy_start", {"recipe": "glm53-tp3", "wait": True})
    original = services.recipes._run_step
    starts = []

    def fail_worker(recipe, node, script, *args, **kwargs):
        if script == recipe["scripts"]["start"]:
            starts.append(node.id)
        result = original(recipe, node, script, *args, **kwargs)
        if script == recipe["scripts"]["stop"] and node.id == "spark2":
            return {**result, "state": "failed", "rc": 1}
        return result

    monkeypatch.setattr(services.recipes, "_run_step", fail_worker)
    result = call("deploy_restart", {"recipe": "glm53-tp3", "wait": True})
    assert result["state"] == "failed"
    assert "stop.sh" in result["message"]
    assert starts == []
    assert "glm53-tp3" not in services.recipes._stopping
    monkeypatch.setattr(services.recipes, "_run_step", original)
    retried = call("deploy_restart", {"recipe": "glm53-tp3", "wait": True})
    assert retried["state"] == "running", retried
    call("deploy_stop", {"recipe": "glm53-tp3"})


def test_restart_refreshes_memory_after_unload(call, services):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    services.cluster.node("spark3").metrics["memory"]["available"] = 1
    result = call("deploy_restart", {"recipe": "qwen38-27b-1m", "wait": True})
    assert result["state"] == "running", result
    call("deploy_stop", {"recipe": "qwen38-27b-1m"})


def test_duplicate_restart_during_reload_does_not_cancel_the_first(call, services, monkeypatch):
    name = "qwen38-27b-1m"
    call("deploy_start", {"recipe": name, "wait": True})
    entered, release = threading.Event(), threading.Event()
    original = services.recipes._run_step

    def held(recipe, node, script, *args, **kwargs):
        if script == recipe["scripts"]["start"]:
            entered.set()
            assert release.wait(10)
        return original(recipe, node, script, *args, **kwargs)

    monkeypatch.setattr(services.recipes, "_run_step", held)
    services.recipes.cancel_wait_s = 0.03
    try:
        call("deploy_restart", {"recipe": name})
        assert entered.wait(3)
        assert services.recipes.state[name]["state"] == "starting"
        assert call("deploy_restart", {"recipe": name}, status=409)["code"] == "busy"
        assert name not in services.recipes._cancel, "duplicate restart cancelled the first reload"
    finally:
        release.set()
    for _ in range(100):
        if services.recipes.state[name]["state"] == "running":
            break
        time.sleep(0.1)
    assert services.recipes.state[name]["state"] == "running"
    call("deploy_stop", {"recipe": name})


def test_stopped_event_already_has_released_stop_ownership(call, services):
    name = "qwen38-27b-1m"
    call("deploy_start", {"recipe": name, "wait": True})
    observed = []
    original = services.recipes.on_event

    def observe(kind, payload):
        if kind == "deploy.stopped":
            observed.append(name in services.recipes._stopping)
        if original:
            original(kind, payload)

    services.recipes.on_event = observe
    call("deploy_stop", {"recipe": name})
    assert observed == [False], "stopped was published with stop ownership still held"
