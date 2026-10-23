export const SPEED_METRICS = [
  { key: 'agg_tps', label: 'sc_col_agg', unit: 'rate', higher: true },
  { key: 'stream_tps', label: 'sc_col_stream', unit: 'rate', higher: true },
  { key: 'ttft_p50', label: 'sc_cmp_p50', unit: 'latency', higher: false },
  { key: 'ttft_p95', label: 'sc_cmp_p95', unit: 'latency', higher: false },
  { key: 'errors', label: 'sc_cmp_errors', unit: 'count', higher: false },
  { key: 'requests', label: 'sc_cmp_requests', unit: 'count', higher: null },
];

export function concurrencyLevels(base, selected) {
  return [...new Set([...(base?.levels || []), ...(selected?.levels || [])].map(level => level.n))].sort((a, b) => a - b);
}

/** A missing reading stays unknown; zero is a real result, with no percentage denominator. */
export function speedComparison(base, selected, n) {
  const before = base?.levels?.find(level => level.n === n);
  const after = selected?.levels?.find(level => level.n === n);
  const rows = SPEED_METRICS.map(metric => {
    const a = Number.isFinite(before?.[metric.key]) ? before[metric.key] : null;
    const b = Number.isFinite(after?.[metric.key]) ? after[metric.key] : null;
    const delta = a === null || b === null ? null : b - a;
    const percent = delta === null || a === 0 ? null : delta / Math.abs(a) * 100;
    const tone = delta === null || delta === 0 || metric.higher === null ? 'neutral'
      : (delta > 0) === metric.higher ? 'better' : 'worse';
    return { ...metric, n, base: a, selected: b, delta, percent, tone };
  });
  const conditions = [
    { key: 'model', base: base?.model, selected: selected?.model },
    ...['max_tokens', 'rounds', 'warmup', 'ignore_eos'].map(key => ({ key, base: base?.settings?.[key], selected: selected?.settings?.[key] })),
    { key: 'tokens_source', base: before?.tokens_source, selected: after?.tokens_source },
  ].map(condition => ({ ...condition, changed: (condition.base ?? null) !== (condition.selected ?? null) }));
  return { rows, conditions, bothLevels: !!before && !!after };
}
