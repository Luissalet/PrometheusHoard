import threading
import time

import pytest


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


@pytest.mark.parametrize("pause_during_preflight", [False, True])
def test_a_completed_later_stop_invalidates_the_restart_handoff(call, services, monkeypatch, world, pause_during_preflight):
    name = "qwen38-27b-1m"
    call("deploy_start", {"recipe": name, "wait": True})
    entered, release = threading.Event(), threading.Event()
    original = services.recipes._start_locked
    original_busy = services.recipes.busy_nodes

    def pause():
        entered.set()
        assert release.wait(10)

    def held(recipe, **kwargs):
        if not pause_during_preflight:
            pause()
        return original(recipe, **kwargs)

    def held_busy(*args, **kwargs):
        if pause_during_preflight and name in services.recipes._restarting:
            pause()
        return original_busy(*args, **kwargs)

    monkeypatch.setattr(services.recipes, "_start_locked", held)
    monkeypatch.setattr(services.recipes, "busy_nodes", held_busy)
    try:
        call("deploy_restart", {"recipe": name})
        assert entered.wait(3)
        assert call("deploy_stop", {"recipe": name})["state"] == "stopped"
        jobs_after_stop = set(world.jobs)
        release.set()
        for _ in range(100):
            if name not in services.recipes._restarting:
                break
            time.sleep(.02)
        assert name not in services.recipes._restarting
        assert services.recipes.state[name]["state"] == "stopped"
        assert not [j for key, j in world.jobs.items() if key not in jobs_after_stop and "start.sh" in j["cmd"]]
    finally:
        release.set()


def test_an_active_later_stop_keeps_ownership_when_old_restart_resumes(call, services, monkeypatch, world):
    name = "qwen38-27b-1m"
    call("deploy_start", {"recipe": name, "wait": True})
    handoff, reload_release = threading.Event(), threading.Event()
    stop_entered, stop_release = threading.Event(), threading.Event()
    original_start = services.recipes._start_locked
    original_step = services.recipes._run_step

    def held_start(recipe, **kwargs):
        handoff.set()
        assert reload_release.wait(10)
        return original_start(recipe, **kwargs)

    def held_stop(recipe, node, script, *args, **kwargs):
        if handoff.is_set() and script == recipe["scripts"]["stop"]:
            stop_entered.set()
            assert stop_release.wait(10)
        return original_step(recipe, node, script, *args, **kwargs)

    monkeypatch.setattr(services.recipes, "_start_locked", held_start)
    monkeypatch.setattr(services.recipes, "_run_step", held_stop)
    try:
        call("deploy_restart", {"recipe": name})
        assert handoff.wait(3)
        services.recipes.stop(name, wait=False)
        assert stop_entered.wait(3)
        jobs_before_release = set(world.jobs)
        reload_release.set()
        for _ in range(100):
            if name not in services.recipes._restarting:
                break
            time.sleep(.02)
        assert name not in services.recipes._restarting
        assert name in services.recipes._stopping
        assert services.recipes.state[name]["state"] == "stopping"
        stop_release.set()
        services.recipes._threads[name].join(timeout=5)
        assert services.recipes.state[name]["state"] == "stopped"
        assert name not in services.recipes._stopping
        assert not [j for key, j in world.jobs.items() if key not in jobs_before_release and "start.sh" in j["cmd"]]
    finally:
        reload_release.set()
        stop_release.set()


def test_stop_cannot_complete_between_reload_registration_and_thread_start(call, services, monkeypatch, world):
    name = "qwen38-27b-1m"
    call("deploy_start", {"recipe": name, "wait": True})
    entered, release = threading.Event(), threading.Event()
    stop_requested, stopped = threading.Event(), threading.Event()
    original_start = threading.Thread.start
    results = []
    errors = []

    def held_start(thread):
        if thread.name == f"start-{name}":
            entered.set()
            assert release.wait(10)
        return original_start(thread)

    def stop():
        try:
            stop_requested.set()
            results.append(services.recipes.stop(name, wait=True))
        except Exception as exc:
            errors.append(exc)
        finally:
            stopped.set()

    monkeypatch.setattr(threading.Thread, "start", held_start)
    world.job_s = .01
    try:
        # Its HTTP response may wait on the handoff's registration lock.
        restarting = threading.Thread(target=lambda: services.recipes.restart(name), name="review-restart")
        restarting.start()
        assert entered.wait(3)
        stopping = threading.Thread(target=stop, name="review-stop")
        stopping.start()
        assert stop_requested.wait(1)
        assert not stopped.wait(1), "stop completed while the old reload thread was still unstarted"
        release.set()
        stopping.join(timeout=5)
        restarting.join(timeout=5)
        assert stopped.is_set() and not errors
        assert results[0]["state"] == "stopped"
        assert services.recipes.state[name]["state"] == "stopped"
        assert name not in world.serving
    finally:
        release.set()
