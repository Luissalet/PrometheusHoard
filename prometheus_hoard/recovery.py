"""Reported structural generation failures, confirmed and recovered by their recipe owner."""
from __future__ import annotations

import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

from .errors import SparkError
from .hoard_link import family
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.lease import Lease, LeaseError, LeaseTimeout
from .serving import aggregate, metrics_url, parse_metrics

SCOPE = {"verified_scope": "structural_generation_and_literal_manifest",
         "algorithm_semantics_verified": False, "canaries_discriminatively_validated": False}
ACTIVE = {"pending", "waiting_coordination", "confirming", "waiting_idle", "restarting", "checking"}
ERROR_CLASSES = {"degenerate_output", "structurally_invalid_output"}


class Deferred(RuntimeError):
    """Coordination is occupied or unavailable; no reload may begin."""


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise SparkError("invalid", "Use the managed recipe's HTTP(S) base URL.")
    if parts.query or parts.fragment:
        raise SparkError("invalid", "The endpoint must not contain a query or fragment.")
    # Do not resolve names, follow redirects or infer arbitrary suffixes.
    return value.rstrip("/")


def recipe_fingerprint(recipe: dict[str, Any]) -> str:
    folder = Path(recipe["folder"])
    files = {}
    for name in recipe["files"]:
        path = folder / name
        if path.is_symlink():
            raise SparkError("invalid", "Recovery does not follow linked recipe files.")
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"recipe": recipe, "files": files}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def canaries() -> list[dict[str, Any]]:
    journals = [
        [{"seq": 2, "op": "set", "key": "a", "value": [1, True]},
         {"seq": 0, "op": "set", "key": "b", "value": None},
         {"seq": 1, "op": "delete", "key": "b"}],
        [{"seq": 7, "op": "set", "key": "ñ", "value": {"v": 3}},
         {"seq": 7, "op": "set", "key": "ñ", "value": {"v": 3}}],
        [{"seq": 0, "op": "delete", "key": "absent"}],
        [{"seq": 1, "op": "set", "key": "x", "value": 2},
         {"seq": 3, "op": "set", "key": "x", "value": 9},
         {"seq": 4, "op": "delete", "key": "x"}],
    ]
    plans = ["Task_A <-\nB2 <- Task_A", "z <-\na <- z\nb <-",
             "b <- a,a\na <-\nz <-\nc <- a", "x <- y\ny <- z\nz <-"]
    journal_contract = (
        "Implement apply_events(events): sort by nonnegative integer seq (not bool), set/delete, "
        "same seq with different typed JSON content raises ValueError; identical duplicates apply once. "
        "Validate fields and string keys; delete missing is harmless; deep copies; no input mutation.")
    planner_contract = (
        "Implement plan(text): NAME <- dep,dep; ASCII names [A-Za-z][A-Za-z0-9_]*, "
        "ignore blank/comment lines, duplicate dependencies count once. Reject undefined references, "
        "duplicate tasks and cycles. Each step picks the smallest currently-ready name.")
    expected = [{"states": [{"a": [1, True]}, {"ñ": {"v": 3}}, {}, {}]},
                {"plans": [["Task_A", "B2"], ["b", "z", "a"], ["a", "b", "c", "z"], ["z", "y", "x"]]}]
    out = []
    for name, function, contract, fixtures, answer in [
        ("journal", "apply_events", journal_contract, journals, expected[0]),
        ("planner", "plan", planner_contract, plans, expected[1]),
    ]:
        prompt = (
            "Write one complete Python module, code only. " + contract +
            " Include several runnable asserts covering malformed records, Unicode, duplicates and ordering. "
            "Also assign CANARY_RESULT to a literal dictionary (not a function call or comprehension) "
            "with the computed outputs for ALL fixtures below. Use key states for the journal or plans "
            "for the planner. Last statement: CANARY_COMPLETE = True. Do not omit code or use ellipses.\n"
            "Fixtures: " + json.dumps(fixtures, ensure_ascii=False))
        out.append({"name": name, "function": function, "prompt": prompt, "expected": answer})
    return out


def check_canary(response: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """No generated function is called: AST plus a typed literal-data oracle only."""
    if response.get("error"):
        return {"kind": "transport", "reason": str(response["error"])}
    if response.get("finish_reason") == "length":
        return {"kind": "truncated"}
    if response.get("finish_reason") != "stop" or response.get("tool_calls") or response.get("refusal"):
        return {"kind": "inconclusive", "reason": "non-final answer"}
    text = response.get("content") or ""
    digest = hashlib.sha256(text.encode()).hexdigest()
    fence = re.fullmatch(r"\s*" + chr(96)*3 + r"(?:python)?\s*\n(.*?)\n" + chr(96)*3 + r"\s*",
                         text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence[1]  # Fences alone never indicate degeneration.
    elif chr(96)*3 in text:
        return {"kind": "inconclusive", "reason": "mixed prose/code format", "sha256": digest}
    words = re.findall(r"\w+", text)
    collapsed = any(all(words[j] == words[j-period] for j in range(start+period, start+period+96))
                    for period in range(1, 17) for start in range(0, max(0, len(words)-period-96), 16))
    if collapsed:
        return {"kind": "structural_failure", "reason": "sustained consecutive repetition", "sha256": digest}
    try:
        tree = ast.parse(text)
    except (ValueError, RecursionError, MemoryError):
        return {"kind": "inconclusive", "reason": "unparseable artifact", "sha256": digest}
    except SyntaxError as exc:
        # A plain prose answer is an instruction/format miss, not corrupt Python.
        if not re.search(r"(?m)^\s*(?:def |class |from |import |CANARY_)", text):
            return {"kind": "inconclusive", "reason": "not a Python artifact", "sha256": digest}
        return {"kind": "structural_failure", "reason": f"syntax: {exc.msg}", "sha256": digest}
    assignments = {n.targets[0].id: n.value for n in tree.body
                   if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == case["function"]]
    if not functions or not any(isinstance(n, (ast.Return, ast.Raise)) for n in ast.walk(functions[0])):
        return {"kind": "inconclusive", "reason": "missing implementation", "sha256": digest}
    try:
        complete = ast.literal_eval(assignments.get("CANARY_COMPLETE"))
        actual = ast.literal_eval(assignments.get("CANARY_RESULT"))
        equal = json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(case["expected"], sort_keys=True)
    except (ValueError, TypeError, RecursionError):
        equal, complete = False, False
    final = tree.body[-1] if tree.body else None
    final_marker = (isinstance(final, ast.Assign) and len(final.targets) == 1 and
                    isinstance(final.targets[0], ast.Name) and final.targets[0].id == "CANARY_COMPLETE")
    if complete is not True or not equal or not final_marker:
        return {"kind": "inconclusive", "reason": "incomplete or incorrect literal manifest", "sha256": digest}
    return {"kind": "pass", "sha256": digest}


def request_canary(base_url: str, model: str, case: dict[str, Any]) -> dict[str, Any]:
    import httpx
    try:
        response = httpx.post(base_url + "/chat/completions", json={
            "model": model, "messages": [{"role": "user", "content": case["prompt"]}],
            "temperature": 0, "max_tokens": 8192, "reasoning_effort": "low",
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low"}},
            timeout=httpx.Timeout(300, connect=5), follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        if body.get("model") != model:
            return {"error": "response model differs from managed model"}
        choice = body["choices"][0]
        return {**choice["message"], "finish_reason": choice.get("finish_reason")}
    except Exception as exc:
        return {"error": str(exc)}


class HubCoordination:
    """Use the existing Agora resource lock and renewing Hub lease, as this app."""
    _local = threading.Lock()

    @contextmanager
    def __call__(self, recipe: str):
        if not self._local.acquire(blocking=False):
            raise Deferred("another recovery holds local coordination")
        locked = False
        try:
            code, result = family._post("/api/agora/lock", {
                "agent": "prometheus", "resources": ["model:principal"], "ttl_s": 10800,
                "note": "Recuperación estructural de " + recipe}, 5)
            if code != 200 or not isinstance(result, dict) or not result.get("ok"):
                raise Deferred("principal model occupied or Agora unavailable")
            locked = True
            with Lease(0, purpose="managed recovery " + recipe, owner="prometheus",
                       hub_url=family._hub(), autostart=False, timeout_s=1) as taken:
                if taken.via != "hub" or taken.state != "granted":
                    raise Deferred("Hub lease unavailable; no local fallback for recovery")
                def validate():
                    code, result = family._post("/api/agora/lock", {
                        "agent": "prometheus", "resources": ["model:principal"], "ttl_s": 10800}, 5)
                    lcode, leased = family._post("/api/lease/renew", {"lease_id": taken.lease_id}, 5)
                    if (code != 200 or not isinstance(result, dict) or not result.get("ok") or
                            lcode != 200 or not isinstance(leased, dict) or not leased.get("ok")):
                        raise Deferred("recovery coordination was lost")
                yield validate
        except Deferred:
            raise
        except (LeaseError, LeaseTimeout, OSError) as exc:
            raise Deferred(str(exc)) from exc
        finally:
            try:
                if locked:
                    family._post("/api/agora/unlock", {"agent": "prometheus", "resources": ["model:principal"]}, 5)
            finally:
                self._local.release()


class Recovery:
    def __init__(self, recipes, jobs, serving, path: Path, *, generate=request_canary,
                 coordinate=None, clock=time.time, sleep=time.sleep, cooldown_s=900):
        self.recipes, self.jobs, self.serving, self.path = recipes, jobs, serving, path
        self.generate, self.coordinate = generate, coordinate or HubCoordination()
        self.clock, self.sleep, self.cooldown_s = clock, sleep, cooldown_s
        self._lock = threading.RLock()
        self._lifecycle_done = threading.Condition(self._lock)
        self._lifecycle_workers: set[int] = set()
        self._closed = threading.Event()
        data = read_json(path, default={}) or {}
        self.records = data.get("recoveries", {})
        for record in self.records.values():
            if record.get("status") in ACTIVE:
                record.update(status="interrupted", reason="application restarted; report again")

    def _save(self):
        write_json_atomic(self.path, {"recoveries": self.records})

    def _set(self, record, status, **fields):
        with self._lock:
            record.update(status=status, updated=self.clock(), **fields)
            self._save()

    def _target(self, base_url, model, *, states=("running",)):
        base_url = canonical_url(base_url)
        matches = []
        for recipe in self.recipes.list():  # Disk/settings only: NEVER endpoints()/get(), which probe HTTP.
            if recipe.get("invalid") or recipe.get("kind", "chat") != "chat":
                continue
            expected = canonical_url(self.recipes.base_url(recipe) + recipe["api_path"])
            if expected != base_url or recipe.get("served_model_name") != model:
                continue
            state = self.recipes.state.get(recipe["name"], {})
            if state.get("state") in states and not state.get("external") and state.get("started"):
                matches.append((recipe, state["started"]))
        if len(matches) != 1:
            raise SparkError("not_managed", "URL/model must identify exactly one running, managed recipe.")
        recipe, boot = matches[0]
        return recipe, str(boot), recipe_fingerprint(recipe)

    def report(self, base_url, model, error_class="degenerate_output", reason=""):
        if error_class not in ERROR_CLASSES:
            raise SparkError("invalid", "Only typed degeneration/structural symptoms are eligible; not length, format or logic errors.")
        recipe, boot, fingerprint = self._target(base_url, model)
        now = self.clock()
        with self._lock:
            recent = sorted((r for r in self.records.values() if r["recipe"] == recipe["name"]),
                            key=lambda r: r["created"], reverse=True)
            for old in recent:
                if old["status"] in ACTIVE or (old["boot"] == boot and now-old["updated"] < self.cooldown_s):
                    return {**deepcopy(old), "deduplicated": True}
            last_restart = next((old for old in recent if old.get("restart_started")), None)
            cooling = last_restart and now-last_restart["restart_started"] < self.cooldown_s
            record = {"id": uuid.uuid4().hex, "recipe": recipe["name"], "boot": boot,
                      "fingerprint": fingerprint, "base_url": canonical_url(base_url), "model": model,
                      "status": "pending", "created": now, "updated": now, "error_class": error_class,
                      "reason": reason, **SCOPE}
            self.records[record["id"]] = record
            self._save()
            if cooling:
                self._set(record, "cooldown", related_recovery_id=last_restart["id"],
                          retry_after=last_restart["restart_started"]+self.cooldown_s)
                return {**deepcopy(record), "deduplicated": True}
            job = self.jobs.start_local(recipe["head"], lambda handle: self._work(record, handle),
                                        kind="recovery", title="Verificación estructural de " + recipe["title"],
                                        meta={"recovery_id": record["id"], "recipe": recipe["name"]})
            record["job"] = job["id"]
            self._save()
            return {**deepcopy(record), "deduplicated": False}

    def status(self, recovery_id="", recipe=""):
        with self._lock:
            rows = [deepcopy(r) for r in self.records.values()
                    if (not recovery_id or r["id"] == recovery_id) and (not recipe or r["recipe"] == recipe)]
        return {"recoveries": sorted(rows, key=lambda r: r["created"], reverse=True), **SCOPE}

    def _validate(self, record, *, states=("running",)):
        recipe, boot, fingerprint = self._target(record["base_url"], record["model"], states=states)
        if boot != record.get("replacement_boot", record["boot"]) or fingerprint != record["fingerprint"]:
            raise SparkError("changed", "The managed recipe, boot or files changed; recovery was not applied.")
        return recipe

    def _idle(self, record):
        # Reuse Serving's getter/parser, but request a NEW sample; a cached zero is not an idle lease.
        self._validate(record, states=("running", "stopping"))
        code, text = self.serving.get(metrics_url(record["base_url"]), 2.0)
        sample = aggregate(parse_metrics(text)) if code == 200 else None
        return bool(sample and sample["running"] == 0 and sample["waiting"] == 0)

    def _checks(self, record, job):
        checks = []
        for case in canaries():
            if self._closed.is_set() or job.cancelled:
                return checks + [{"kind": "inconclusive", "reason": "cancelled before next canary"}]
            self._validate(record)
            checks.append(check_canary(self.generate(record["base_url"], record["model"], case), case))
        return checks

    def _work(self, record, job):
        try:
            self._recover(record, job)
        finally:
            job.report(1.0, json.dumps({"recovery_id": record["id"], "status": record["status"], **SCOPE}))

    def _follow_restart(self, record, job, renew):
        """Keep supervising our worker even if coordination or cancellation changes.

        A failed renewal does not establish that the old lock/lease still exists. The
        worker's before_start guard must obtain a fresh confirmation before loading.
        Leaving this context early would voluntarily release any coordination that
        does remain while stop/start is still acting on the deployment.
        """
        while (record["recipe"] in getattr(self.recipes, "_restarting", set()) or
               self.recipes.state[record["recipe"]]["state"] in ("starting", "stopping")):
            try:
                renew()
            except Exception as exc:
                self._set(record, "restarting", coordination_confirmed=False, coordination_error=str(exc),
                          cancellation_requested=self._closed.is_set() or job.cancelled)
            else:
                self._set(record, "restarting", coordination_confirmed=True, coordination_error="",
                          cancellation_requested=self._closed.is_set() or job.cancelled)
            self.sleep(2)

    @contextmanager
    def _lifecycle(self, job):
        worker = threading.get_ident()
        with self._lifecycle_done:
            if self._closed.is_set() or job.cancelled:
                raise Deferred("recovery cancelled before lifecycle")
            self._lifecycle_workers.add(worker)
        try:
            yield
        finally:
            with self._lifecycle_done:
                self._lifecycle_workers.discard(worker)
                self._lifecycle_done.notify_all()

    def _recover(self, record, job):
        while not self._closed.is_set() and not job.cancelled:
            try:
                self._validate(record)
                with self.coordinate(record["recipe"]) as renew:
                    recipe = self._validate(record)
                    if not self.recipes.ready(recipe).get("up"):
                        self._set(record, "not_confirmed", error="managed ownership/readiness not confirmed")
                        return
                    self._set(record, "confirming")
                    pre = self._checks(record, job)
                    self._set(record, "confirming", before=pre)
                    if not all(item["kind"] == "structural_failure" for item in pre):
                        self._set(record, "not_confirmed")
                        return
                    if self._closed.is_set() or job.cancelled:
                        self._set(record, "cancelled")
                        return
                    self._set(record, "waiting_idle")
                    while not self._idle(record):
                        if self._closed.is_set() or job.cancelled:
                            self._set(record, "cancelled")
                            return
                        self._validate(record)
                        renew()
                        self.sleep(2)
                    def before_stop():
                        if self._closed.is_set() or job.cancelled:
                            raise Deferred("recovery cancelled before stop")
                        renew()
                        recipe = self._validate(record, states=("running", "stopping"))
                        if not self.recipes.ready(recipe).get("up"):
                            raise Deferred("recipe ownership no longer confirmed")
                        if not self._idle(record):
                            raise Deferred("new running/waiting requests before stop")
                    before_stop()
                    self._set(record, "restarting", restart_started=self.clock())
                    def before_start():
                        if self._closed.is_set() or job.cancelled:
                            raise Deferred("recovery cancelled before start")
                        try:
                            renew()
                        except Exception as exc:
                            self._set(record, "restarting", coordination_confirmed=False,
                                      coordination_error=str(exc))
                            raise
                        self._validate(record, states=("stopped",))
                    with self._lifecycle(job):
                        try:
                            self.recipes.restart(record["recipe"], wait=False, before_stop=before_stop,
                                                 before_start=before_start)
                        finally:
                            # Also follow a worker if the restart call itself fails after launch.
                            self._follow_restart(record, job, renew)
                    result = self.recipes.state[record["recipe"]]
                    if self._closed.is_set() or job.cancelled:
                        self._set(record, "cancelled")
                        return
                    if result.get("state") != "running" or result.get("external"):
                        self._set(record, "failed", error="managed restart did not reach running")
                        return
                    recipe, boot, fingerprint = self._target(record["base_url"], record["model"])
                    if fingerprint != record["fingerprint"]:
                        self._set(record, "failed", error="recipe files changed during reload")
                        return
                    if self._closed.is_set() or job.cancelled:
                        self._set(record, "cancelled")
                        return
                    self._set(record, "checking", replacement_boot=boot)
                    try:
                        renew()
                    except Exception as exc:
                        # A reload has already happened. Do not re-enter confirmation
                        # and potentially reload again because its postcheck lost Hub access.
                        self._set(record, "unverified", coordination_confirmed=False,
                                  coordination_error=str(exc), error="postcheck coordination unavailable")
                        return
                    self._set(record, "checking", coordination_confirmed=True, coordination_error="")
                    post = self._checks(record, job)
                    self._set(record, "recovered" if all(item["kind"] == "pass" for item in post) else "unverified",
                              after=post)
                    return
            except Deferred as exc:
                self._set(record, "waiting_coordination", deferred_reason=str(exc))
                self.sleep(5)
            except Exception as exc:
                self._set(record, "failed", error=str(exc))
                return
        self._set(record, "cancelled")

    def close(self):
        with self._lifecycle_done:
            self._closed.set()
            # Services.stop must not let process shutdown discard daemon workers
            # halfway through a reload. A worker requesting its own cancellation
            # cannot join itself; its finally block will finish the supervision.
            if threading.get_ident() not in self._lifecycle_workers:
                self._lifecycle_done.wait_for(lambda: not self._lifecycle_workers)
