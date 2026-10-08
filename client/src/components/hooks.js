import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { createSerialPoll } from "../polling.js";

// Calls `fn` now and then every `ms` while `active` is true. The first call is
// unconditional: a page that only gets its data through polling must never wait
// for a tab to be visible. Later ticks are skipped while the tab is hidden, and
// one tick runs as soon as it becomes visible again. Overlapping calls are
// serialized — a slow deployments refresh must finish before the next starts.
export function usePoll(fn, ms, active = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return undefined;
    const poll = createSerialPoll(() => ref.current(), {
      intervalMs: ms,
      isHidden: () => document.hidden,
    });
    const onVisible = () => poll.wake();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      poll.stop();
      document.removeEventListener("visibilitychange", onVisible);
    };
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
