import React from "react";

// Small SVG charts drawn by hand (no chart library): a sparkline for cards and a Task Manager style graph for the detail page.

function points(data, max, w, h, pad = 1) {
  const n = data.length;
  if (!n) return [];
  const step = n > 1 ? w / (n - 1) : 0;
  return data.map((v, i) => {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return null;
    const y = h - pad - (Math.max(0, Math.min(max, Number(v))) / (max || 1)) * (h - 2 * pad);
    return [i * step, y];
  });
}

function pathOf(pts) {
  let d = "";
  let pen = false;
  for (const p of pts) {
    if (!p) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${p[0].toFixed(2)} ${p[1].toFixed(2)}`;
    pen = true;
  }
  return d;
}

function areaOf(pts, h) {
  const segs = [];
  let cur = [];
  for (const p of pts) {
    if (!p) { if (cur.length) segs.push(cur); cur = []; continue; }
    cur.push(p);
  }
  if (cur.length) segs.push(cur);
  return segs.map((s) => `M${s[0][0].toFixed(2)} ${h}` + s.map((p) => `L${p[0].toFixed(2)} ${p[1].toFixed(2)}`).join("") + `L${s[s.length - 1][0].toFixed(2)} ${h}Z`).join("");
}

/** `series`: [{data:[numbers|null], color, dashed}]; values are clamped to [0, max]. */
export function Sparkline({ series, max = 100, height = 34, className = "", label }) {
  const W = 120;
  const H = height;
  const list = (series || []).filter((s) => s && s.data && s.data.length);
  return (
    <svg className={`spark ${className}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" height={H} role="img" aria-label={label}>
      {list.map((s, i) => {
        const pts = points(s.data, max, W, H);
        return (
          <g key={i}>
            {!s.dashed && <path d={areaOf(pts, H)} fill={s.color} opacity="0.16" />}
            <path d={pathOf(pts)} fill="none" stroke={s.color} strokeWidth="1.4" vectorEffect="non-scaling-stroke" strokeDasharray={s.dashed ? "3 2" : undefined} />
          </g>
        );
      })}
    </svg>
  );
}

/** A Task Manager style graph: grid, filled area, labels for the top value and the time span. */
export function BigChart({ series, max = 100, topLabel, spanLabel, bottomLabel = "0", height = 220, label }) {
  const W = 600;
  const H = 200;
  const list = (series || []).filter((s) => s && s.data && s.data.length);
  const grid = [];
  for (let i = 1; i < 10; i += 1) grid.push(<line key={`v${i}`} x1={(W / 10) * i} x2={(W / 10) * i} y1="0" y2={H} />);
  for (let i = 1; i < 5; i += 1) grid.push(<line key={`h${i}`} x1="0" x2={W} y1={(H / 5) * i} y2={(H / 5) * i} />);
  return (
    <figure className="bigchart" aria-label={label}>
      <div className="bigchart-top">
        <span>{spanLabel}</span>
        <span>{topLabel}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height }} role="img" aria-label={label}>
        <g className="bigchart-grid">{grid}</g>
        {list.map((s, i) => {
          const pts = points(s.data, max, W, H, 0);
          return (
            <g key={i}>
              {!s.dashed && <path d={areaOf(pts, H)} fill={s.color} opacity="0.18" />}
              <path d={pathOf(pts)} fill="none" stroke={s.color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeDasharray={s.dashed ? "5 3" : undefined} />
            </g>
          );
        })}
        <rect x="0" y="0" width={W} height={H} fill="none" className="bigchart-frame" />
      </svg>
      <div className="bigchart-bottom">
        <span />
        <span>{bottomLabel}</span>
      </div>
    </figure>
  );
}

/** Per-core usage as a row of small vertical bars. */
export function CoreBars({ values, max = 100, label }) {
  if (!values || !values.length) return null;
  return (
    <div className="cores" role="img" aria-label={label}>
      {values.map((v, i) => (
        <span key={i} className="core"><span style={{ height: `${Math.max(4, Math.min(100, ((v || 0) / max) * 100))}%` }} /></span>
      ))}
    </div>
  );
}

/** A nice upper bound for a rate chart (bytes/s): 1, 2, 5 × 10^n. */
export function niceMax(values, floor = 1) {
  const m = Math.max(floor, ...values.filter((v) => v !== null && v !== undefined).map(Number));
  const p = 10 ** Math.floor(Math.log10(m));
  for (const k of [1, 2, 5, 10]) if (k * p >= m) return k * p;
  return 10 * p;
}
