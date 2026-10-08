/**
 * Serial polling for UI refresh.
 *
 * A timer that fires while a previous `run()` is still open used to stack
 * overlapping calls. Callers that ignore stale results (useLoad's seq) then
 * drop every answer and leave the panel on Loading forever.
 *
 * This helper starts at most one `run` at a time. Wakes that arrive while busy
 * coalesce into a single trailing run after the current one settles. The first
 * run is unconditional (a page that only loads via polling must not wait for
 * the tab to be visible); later ticks skip work while hidden. `wake()` covers
 * visibilitychange.
 */

export function createSerialPoll(run, {
  intervalMs,
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (id) => clearTimeout(id),
  isHidden = () => false,
} = {}) {
  let stopped = false;
  let timer = null;
  let inFlight = false;
  let queued = false;
  let started = false;

  const clearTimer = () => {
    if (timer != null) {
      cancel(timer);
      timer = null;
    }
  };

  const arm = (ms) => {
    clearTimer();
    if (stopped) return;
    timer = schedule(() => {
      timer = null;
      void kick();
    }, ms);
  };

  const kick = async ({ force = false } = {}) => {
    if (stopped) return;
    if (inFlight) {
      queued = true;
      return;
    }
    // First run always goes through; only later ticks honour the hidden tab.
    if (!force && started && isHidden()) {
      arm(intervalMs);
      return;
    }
    inFlight = true;
    queued = false;
    started = true;
    try {
      await run();
    } catch {
      // The caller reports errors; the loop must keep going.
    } finally {
      inFlight = false;
    }
    if (stopped) return;
    if (queued) {
      queued = false;
      void kick();
      return;
    }
    arm(intervalMs);
  };

  const wake = () => {
    if (stopped || isHidden()) return;
    void kick();
  };

  const stop = () => {
    stopped = true;
    clearTimer();
  };

  void kick({ force: true });
  return {
    stop,
    wake,
    get busy() {
      return inFlight;
    },
  };
}
