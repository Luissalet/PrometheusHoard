"""Serial UI polling: slow refreshes must not stack and starve useLoad."""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLLING = ROOT / "client" / "src" / "polling.js"


def _node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed ({proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
    line = proc.stdout.strip().splitlines()[-1]
    return json.loads(line)


def test_polling_module_is_present():
    text = POLLING.read_text(encoding="utf-8")
    assert "createSerialPoll" in text
    assert "queued" in text
    assert "force" in text


def test_slow_run_coalesces_overlapping_wakes():
    """Deployments > interval: many wakes while busy become one trailing run."""
    script = textwrap.dedent(
        f"""
        import {{ createSerialPoll }} from {json.dumps(POLLING.as_uri())};

        const events = [];
        let resolveCurrent;
        const gate = () => new Promise((resolve) => {{ resolveCurrent = resolve; }});

        let runs = 0;
        const timers = new Map();
        let nextId = 1;
        const schedule = (fn, ms) => {{
          const id = nextId++;
          timers.set(id, {{ fn, ms }});
          return id;
        }};
        const cancel = (id) => timers.delete(id);

        const poll = createSerialPoll(async () => {{
          runs += 1;
          events.push("start-" + runs);
          await gate();
          events.push("end-" + runs);
        }}, {{ intervalMs: 1000, schedule, cancel, isHidden: () => false }});

        await Promise.resolve();
        poll.wake();
        poll.wake();
        poll.wake();
        assert(runs === 1, "overlapping wakes must not start a second run");

        resolveCurrent();
        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 2, "exactly one trailing run after the slow one finishes");
        resolveCurrent();
        await Promise.resolve();
        await Promise.resolve();

        poll.stop();
        for (const t of [...timers.values()]) t.fn();
        assert(runs === 2, "stop must prevent further runs");

        console.log(JSON.stringify({{ runs, events }}));
        function assert(cond, msg) {{ if (!cond) throw new Error(msg); }}
        """
    )
    out = _node(script)
    assert out["runs"] == 2
    assert out["events"] == ["start-1", "end-1", "start-2", "end-2"]


def test_error_in_run_does_not_stop_the_loop():
    script = textwrap.dedent(
        f"""
        import {{ createSerialPoll }} from {json.dumps(POLLING.as_uri())};

        let runs = 0;
        const timers = new Map();
        let nextId = 1;
        const schedule = (fn, ms) => {{
          const id = nextId++;
          timers.set(id, fn);
          return id;
        }};
        const cancel = (id) => timers.delete(id);

        const poll = createSerialPoll(async () => {{
          runs += 1;
          if (runs === 1) throw new Error("deployments timed out");
        }}, {{ intervalMs: 10, schedule, cancel, isHidden: () => false }});

        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 1, "first run happened");
        assert(timers.size === 1, "error still arms the next tick");

        const next = [...timers.values()][0];
        timers.clear();
        next();
        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 2, "second run after an error");
        poll.stop();
        console.log(JSON.stringify({{ runs }}));
        function assert(cond, msg) {{ if (!cond) throw new Error(msg); }}
        """
    )
    assert _node(script)["runs"] == 2


def test_first_run_is_unconditional_then_hidden_skips_ticks():
    """Background tabs still get the first load; only later ticks wait for visibility."""
    script = textwrap.dedent(
        f"""
        import {{ createSerialPoll }} from {json.dumps(POLLING.as_uri())};

        let hidden = true;
        let runs = 0;
        const timers = new Map();
        let nextId = 1;
        const schedule = (fn, ms) => {{
          const id = nextId++;
          timers.set(id, fn);
          return id;
        }};
        const cancel = (id) => timers.delete(id);

        const poll = createSerialPoll(async () => {{ runs += 1; }}, {{
          intervalMs: 10,
          schedule,
          cancel,
          isHidden: () => hidden,
        }});
        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 1, "first run is unconditional even while hidden");
        assert(timers.size === 1, "next tick is armed");

        const later = [...timers.values()][0];
        timers.clear();
        later();
        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 1, "later ticks while hidden do no extra work");
        assert(timers.size === 1, "still armed while hidden");

        hidden = false;
        poll.wake();
        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 2, "wake after visible runs once more");
        poll.stop();
        console.log(JSON.stringify({{ runs }}));
        function assert(cond, msg) {{ if (!cond) throw new Error(msg); }}
        """
    )
    assert _node(script)["runs"] == 2


def test_stop_cancels_armed_timer():
    script = textwrap.dedent(
        f"""
        import {{ createSerialPoll }} from {json.dumps(POLLING.as_uri())};

        let runs = 0;
        const timers = new Map();
        let nextId = 1;
        const schedule = (fn, ms) => {{
          const id = nextId++;
          timers.set(id, fn);
          return id;
        }};
        const cancel = (id) => timers.delete(id);

        const poll = createSerialPoll(async () => {{ runs += 1; }}, {{
          intervalMs: 10,
          schedule,
          cancel,
          isHidden: () => false,
        }});
        await Promise.resolve();
        await Promise.resolve();
        assert(runs === 1);
        assert(timers.size === 1);
        poll.stop();
        assert(timers.size === 0, "stop clears the armed timer");
        console.log(JSON.stringify({{ runs, armed: timers.size }}));
        function assert(cond, msg) {{ if (!cond) throw new Error(msg); }}
        """
    )
    out = _node(script)
    assert out["runs"] == 1
    assert out["armed"] == 0


def test_hooks_and_app_use_serial_poll():
    hooks = (ROOT / "client" / "src" / "components" / "hooks.js").read_text(encoding="utf-8")
    app = (ROOT / "client" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "createSerialPoll" in hooks and "setInterval" not in hooks
    assert "from \"./polling.js\"" in app or "from './polling.js'" in app
    assert "createSerialPoll" in app and "setInterval" not in app
