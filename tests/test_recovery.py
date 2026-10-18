from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from prometheus_hoard.errors import SparkError
from prometheus_hoard.recovery import (Deferred, HubCoordination, Recovery, SCOPE,
                                       canaries, check_canary)


def response(case, kind="pass"):
    source = (f"def {case['function']}(value):\n    return value\n"
              f"CANARY_RESULT = {case['expected']!r}\nCANARY_COMPLETE = True\n")
    if kind == "syntax": source = "def broken(\n"
    if kind == "logic": source = source.replace(repr(case["expected"]), "{}")
    return {"content": source, "finish_reason": "length" if kind == "length" else "stop"}


class QueuedJobs:
    def start_local(self, node, work, **kwargs):
        self.work = work
        return {"id": "own-job"}

    def run(self):
        self.work(SimpleNamespace(cancelled=False, report=lambda *args: None))


@pytest.fixture
def recovery(tmp_path):
    folder = tmp_path / "recipe"; folder.mkdir()
    (folder / "recipe.json").write_text('{"synthetic":true}')
    recipe = {"name": "own", "title": "Own", "folder": str(folder), "files": ["recipe.json"],
              "served_model_name": "invented-model", "api_path": "/v1", "kind": "chat", "head": "invented"}
    class Recipes:
        state = {"own": {"state": "running", "external": False, "started": 1}}
        stop_calls = 0
        before = None
        def list(self): return [deepcopy(recipe)]
        def base_url(self, r): return "http://invented.invalid:1234"
        def ready(self, r): return {"up": True}
        def restart(self, name, *, wait, before_stop):
            if self.before: self.before()
            before_stop()
            self.stop_calls += 1
            self.state["own"]["started"] += 1
            return {"state": "running", "external": False}
    serving = SimpleNamespace(get=lambda *a: (200, "vllm:num_requests_running 0\nvllm:num_requests_waiting 0"))
    recipes, jobs = Recipes(), QueuedJobs()
    generated = []
    def generate(base, model, case):
        generated.append(case["name"])
        return response(case, "syntax" if recipes.stop_calls == 0 else "pass")
    @contextmanager
    def coordinate(name):
        yield lambda: None
    instance = Recovery(recipes, jobs, serving, tmp_path / "recovery.json", generate=generate,
                        coordinate=coordinate, sleep=lambda seconds: None, clock=lambda: 100)
    instance.generated = generated
    return instance


def report(recovery, **changes):
    return recovery.report(**dict(base_url="http://invented.invalid:1234/v1",
                                  model="invented-model", **changes))


def test_report_is_fast_pending_deduplicated_and_scope_explicit(recovery):
    first = report(recovery)
    second = report(recovery)
    assert first["status"] == "pending" and second["id"] == first["id"] and second["deduplicated"]
    assert recovery.generated == [] and recovery.recipes.stop_calls == 0
    assert {k: first[k] for k in SCOPE} == SCOPE
    recovery.jobs.run()
    final = recovery.status(first["id"])["recoveries"][0]
    assert final["status"] == "recovered" and final["replacement_boot"] == "2"
    assert recovery.recipes.stop_calls == 1
    assert recovery.generated == ["journal", "planner", "journal", "planner"]
    newer = report(recovery)
    assert newer["status"] == "cooldown" and newer["boot"] == "2"
    assert newer["related_recovery_id"] == first["id"]


@pytest.mark.parametrize("change", [{"base_url": "http://other.invalid/v1"}, {"model": "another"},
                                   {"base_url": "http://invented.invalid:1234/v1?redirect=other"}])
def test_unmatched_endpoint_rejected_before_any_network(recovery, change):
    with pytest.raises(SparkError):
        recovery.report(**dict(base_url="http://invented.invalid:1234/v1", model="invented-model") | change)
    assert recovery.generated == [] and recovery.records == {}


@pytest.mark.parametrize("error", ["length", "format_contract_fail", "logical_error"])
def test_nonstructural_symptom_does_not_schedule(recovery, error):
    with pytest.raises(SparkError): report(recovery, error_class=error)
    assert recovery.records == {}


def test_detected_or_external_recipe_is_never_reloaded(recovery):
    recovery.recipes.state["own"]["external"] = True
    with pytest.raises(SparkError): report(recovery)
    assert recovery.generated == [] and recovery.recipes.stop_calls == 0


@pytest.mark.parametrize("kind", ["pass", "logic", "length"])
def test_no_restart_for_good_wrong_manifest_or_truncated_program(recovery, kind):
    recovery.generate = lambda base, model, case: response(case, kind)
    first = report(recovery); recovery.jobs.run()
    assert recovery.status(first["id"])["recoveries"][0]["status"] == "not_confirmed"
    assert recovery.recipes.stop_calls == 0


def test_transport_failure_does_not_confirm(recovery):
    recovery.generate = lambda *args: {"error": "timeout"}
    first = report(recovery); recovery.jobs.run()
    assert recovery.status(first["id"])["recoveries"][0]["before"][0]["kind"] == "transport"
    assert recovery.recipes.stop_calls == 0


def test_owned_health_must_match_before_canary_request(recovery):
    recovery.recipes.ready = lambda recipe: {"up": False, "error": "another server"}
    report(recovery); recovery.jobs.run()
    assert recovery.generated == [] and recovery.recipes.stop_calls == 0


def test_shutdown_after_canaries_cannot_begin_reload(recovery):
    def generate(base, model, case):
        recovery.close()
        return response(case, "syntax")
    recovery.generate = generate
    report(recovery); recovery.jobs.run()
    assert recovery.recipes.stop_calls == 0


def test_two_independent_structural_failures_required(recovery):
    recovery.generate = lambda base, model, case: response(case, "syntax" if case["name"] == "journal" else "pass")
    report(recovery); recovery.jobs.run()
    assert recovery.recipes.stop_calls == 0


@pytest.mark.parametrize("change", ["boot", "files", "busy"])
def test_final_guard_revalidates_before_stop(recovery, change):
    def modify():
        if change == "boot": recovery.recipes.state["own"]["started"] += 1
        elif change == "files":
            (recovery.path.parent / "recipe" / "recipe.json").write_text("changed")
        else:
            recovery.serving.get = lambda *a: (200, "vllm:num_requests_running 1\nvllm:num_requests_waiting 0")
            recovery.close()
    recovery.recipes.before = modify
    first = report(recovery); recovery.jobs.run()
    final = recovery.status(first["id"])["recoveries"][0]
    assert final["status"] in ("failed", "cancelled") and recovery.recipes.stop_calls == 0


def test_new_busy_request_defers_instead_of_interrupting_it(recovery):
    calls = []
    def getter(*args):
        calls.append(1)
        busy = len(calls) == 1
        return 200, f"vllm:num_requests_running {int(busy)}\nvllm:num_requests_waiting 0"
    recovery.serving.get = getter
    report(recovery); recovery.jobs.run()
    assert len(calls) >= 3 and recovery.recipes.stop_calls == 1


def test_missing_metrics_never_counts_as_idle(recovery):
    recovery.serving.get = lambda *a: (200, "not vllm metrics")
    recovery.sleep = lambda _: recovery.close()
    report(recovery); recovery.jobs.run()
    assert recovery.recipes.stop_calls == 0


def test_coordination_conflict_keeps_requests_untouched(recovery):
    @contextmanager
    def occupied(name):
        raise Deferred("principal used by QA")
        yield
    recovery.coordinate = occupied
    recovery.sleep = lambda _: recovery.close()
    first = report(recovery); recovery.jobs.run()
    assert recovery.generated == [] and recovery.recipes.stop_calls == 0
    assert "principal" in recovery.status(first["id"])["recoveries"][0]["deferred_reason"]


def test_post_canaries_must_pass_before_recovered(recovery):
    recovery.generate = lambda base, model, case: response(case, "syntax")
    first = report(recovery); recovery.jobs.run()
    assert recovery.status(first["id"])["recoveries"][0]["status"] == "unverified"
    assert recovery.recipes.stop_calls == 1


def test_existing_async_restart_is_followed_with_coordination_renewal(recovery):
    renewals = []
    @contextmanager
    def coordinate(name):
        yield lambda: renewals.append(1)
    def restart(name, *, wait, before_stop):
        assert wait is False
        before_stop()
        recovery.recipes.stop_calls += 1
        recovery.recipes.state["own"]["state"] = "starting"
        recovery.recipes._restarting = {"own"}
        return {"state": "starting"}
    def finish(seconds):
        recovery.recipes.state["own"].update(state="running", started=2)
        recovery.recipes._restarting.clear()
    recovery.coordinate, recovery.recipes.restart, recovery.sleep = coordinate, restart, finish
    first = report(recovery); recovery.jobs.run()
    assert recovery.status(first["id"])["recoveries"][0]["status"] == "recovered"
    assert len(renewals) >= 4


def test_pending_survives_app_restart_as_interrupted(recovery):
    first = report(recovery)
    other = Recovery(recovery.recipes, recovery.jobs, recovery.serving, recovery.path)
    assert other.status(first["id"])["recoveries"][0]["status"] == "interrupted"


@pytest.mark.parametrize("case", canaries())
def test_cpu_literal_oracle_never_executes_generated_functions(case):
    output = response(case)
    output["content"] = output["content"].replace("return value", "raise RuntimeError('never execute')")
    assert check_canary(output, case)["kind"] == "pass"
    assert check_canary(response(case, "logic"), case)["kind"] == "inconclusive"
    wrapped = dict(output, content=chr(96)*3+"python\n"+output["content"]+chr(96)*3)
    assert check_canary(wrapped, case)["kind"] == "pass"
    assert check_canary(response(case, "length"), case)["kind"] == "truncated"
    assert check_canary(response(case, "syntax"), case)["kind"] == "structural_failure"


def test_hub_coordination_uses_app_identity_lease_and_releases(monkeypatch):
    calls = []
    class FakeLease:
        def __init__(self, *args, **kwargs):
            assert kwargs["owner"] == "prometheus" and kwargs["autostart"] is False
        def __enter__(self):
            return SimpleNamespace(via="hub", state="granted", lease_id="own-lease")
        def __exit__(self, *args): calls.append(("lease_exit", {}))
    def post(path, data, timeout):
        calls.append((path, data))
        return 200, {"ok": True}
    monkeypatch.setattr("prometheus_hoard.recovery.Lease", FakeLease)
    monkeypatch.setattr("prometheus_hoard.recovery.family._post", post)
    monkeypatch.setattr("prometheus_hoard.recovery.family._hub", lambda: "http://hub.invalid")
    with HubCoordination()("own") as renew: renew()
    assert calls[0][1]["agent"] == "prometheus"
    assert calls[0][1]["resources"] == ["model:principal"]
    assert any(p == "/api/lease/renew" for p, _ in calls)
    assert calls[-1][0] == "/api/agora/unlock"


def test_hub_conflict_never_acquires_lease(monkeypatch):
    monkeypatch.setattr("prometheus_hoard.recovery.family._post", lambda *a: (200, {"ok": False, "conflicts": ["QA"]}))
    monkeypatch.setattr("prometheus_hoard.recovery.Lease", lambda *a, **k: pytest.fail("lease must not be requested"))
    with pytest.raises(Deferred):
        with HubCoordination()("own"): pytest.fail("must defer")


def test_hub_local_fallback_cannot_authorize_reload(monkeypatch):
    calls = []
    class LocalLease:
        def __init__(self, *a, **k): pass
        def __enter__(self): return SimpleNamespace(via="local", state="local")
        def __exit__(self, *args): calls.append("lease_exit")
    def post(path, *args):
        calls.append(path)
        return 200, {"ok": True}
    monkeypatch.setattr("prometheus_hoard.recovery.Lease", LocalLease)
    monkeypatch.setattr("prometheus_hoard.recovery.family._post", post)
    monkeypatch.setattr("prometheus_hoard.recovery.family._hub", lambda: "http://hub.invalid")
    with pytest.raises(Deferred):
        with HubCoordination()("own"): pytest.fail("local fallback is not coordination")
    assert calls[-1] == "/api/agora/unlock" and "lease_exit" in calls


def test_agent_report_and_status_use_native_catalogue(call, services):
    call("deploy_start", {"recipe": "glm53-tp3", "wait": True})
    d = next(row for row in services.recipes.deployments(check=False) if row["recipe"] == "glm53-tp3")
    first = call("deploy_report_issue", {"base_url": d["base_url"], "model": d["served_model_name"],
                                       "error_class": "degenerate_output", "reason": "synthetic typed symptom"})
    status = call("deploy_recovery_status", {"recovery_id": first["id"]})
    assert status["recoveries"] and status["algorithm_semantics_verified"] is False


def test_recipe_restart_guard_failure_has_no_stop_steps(services):
    services.recipes.start("glm53-tp3", wait=True)
    before = deepcopy(services.recipes.state["glm53-tp3"])
    jobs = set(services.jobs.jobs)
    def refuse(): raise Deferred("fresh activity")
    with pytest.raises(Deferred):
        services.recipes.restart("glm53-tp3", before_stop=refuse)
    assert services.recipes.state["glm53-tp3"] == before
    assert set(services.jobs.jobs) == jobs
    assert not services.recipes._stopping and not services.recipes._restarting
