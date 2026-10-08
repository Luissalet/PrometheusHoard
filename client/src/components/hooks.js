import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Calls `fn` now and then every `ms` while `active` is true. The first call is unconditional: a page that only gets its data
// through polling must never wait for a tab to be visible. Later ticks are skipped while the tab is hidden, and one tick runs
// as soon as it becomes visible again. The latest `fn` is always used.
export function usePoll(fn, ms, active = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return undefined;
    let stopped = false;
    const run = () => { if (!stopped) ref.current(); };
    const tick = () => { if (!document.hidden) run(); };
    const onVisible = () => { if (!document.hidden) run(); };
    run();
    const timer = setInterval(tick, ms);
    document.addEventListener("visibilitychange", onVisible);
    return () => { stopped = true; clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [ms, active]);
}

/** Recent samples of several Sparks ({id: samples[]}), refreshed every `ms` while the tab is visible. */
export function useHistories(ids, minutes = 5, ms = 2000) {
  const [data, setData] = useState({});
  const key = (ids || []).join(",");
  usePoll(async () => {
    const list = key ? key.split(",") : [];
    const results = await Promise.all(list.map((id) => api.history(id, minutes).then((r) => [id, r.samples || []]).catch(() => [id, null])));
    setData((cur) => {
      const next = { ...cur };
      for (const [id, samples] of results) if (samples) next[id] = samples;
      return next;
    });
  }, ms, !!key);
  useEffect(() => { setData({}); }, [minutes]);
  return data;
}
