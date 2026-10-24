import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

export const jobQueryArguments = query => ({ node: query.node, state: query.state === "finished" ? "" : query.state, limit: 50 });

// A polling tick for the same query shares its pending read instead of superseding it.
export function useJobHistory(query, version) {
  const key = JSON.stringify([query.node, query.state]);
  const current = useRef(null), pending = useRef(null), serial = useRef(0), alive = useRef(false);
  current.current = { key, query: { ...query } };
  const [state, setState] = useState({ key: null, data: null, error: null, loading: true, query: null });
  const reload = () => {
    const scope = current.current;
    if (pending.current?.key === scope.key) return pending.current.promise;
    const ticket = ++serial.current;
    setState(previous => ({ key: scope.key, query: scope.query, data: previous.key === scope.key ? previous.data : null, error: null, loading: true }));
    const args = jobQueryArguments(scope.query);
    const promise = api.call("jobs_list", args).then(data => {
      if (alive.current && current.current.key === scope.key && serial.current === ticket) setState({ key: scope.key, query: scope.query, data, error: null, loading: false });
      return data;
    }).catch(error => {
      if (alive.current && current.current.key === scope.key && serial.current === ticket) setState(previous => ({ ...previous, error, loading: false }));
    }).finally(() => { if (pending.current?.ticket === ticket) pending.current = null; });
    pending.current = { key: scope.key, ticket, promise };
    return promise;
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; serial.current++; pending.current = null; }; }, []);
  useEffect(() => { reload(); }, [key, version]);
  return { data: state.key === key ? state.data : null, error: state.key === key ? state.error : null, loading: state.key !== key || state.loading, resultQuery: state.key === key && state.data ? state.query : null, reload };
}
