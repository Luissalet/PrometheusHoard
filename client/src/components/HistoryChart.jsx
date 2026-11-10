import React, { useMemo, useRef, useState } from "react";
import { locale, num } from "../format.js";

// A time chart for the long history: one line per Spark, gaps where there were no samples, outages shaded, and a crosshair that reads
// every Spark's value at the pointer. Hand-drawn SVG like the other charts of the app.

const W = 720;
const H = 200;
const PAD_L = 0;

export const SPARK_COLORS = ["#76b900", "#5aa9e6", "#e0a84a", "#b48ef0", "#e07a5f", "#4fb2a0", "#e6c35a", "#d46fa7"];

function fmtTick(ts, span, lang) {
  const d = new Date(ts * 1000);
  if (span <= 36 * 3600) return d.toLocaleTimeString(locale(lang), { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(locale(lang), { day: "numeric", month: "short" });
}

function fmtWhen(ts, step, lang) {
  const d = new Date(ts * 1000);
  const opts = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  return d.toLocaleString(locale(lang), opts) + (step > 60 ? ` · ${Math.round(step / 60)} min` : "");
}

/**
 * `t`: bucket start times (s); `series`: [{id, name, color, data: [number|null]}]; `max`: top of the scale (auto when omitted);
 * `unit`: appended to values; `digits`; `bands`: [{from, to, tone}] shaded spans (outages).
 */
export default function HistoryChart({ t: times, step, series, max, unit = "", digits = 0, bands = [], lang = "es", label, height = 200 }) {
  const wrap = useRef(null);
  const [hover, setHover] = useState(null);
  const n = times.length;
  const start = times[0] || 0;
  const end = (times[n - 1] || 0) + (step || 60);
  const span = Math.max(1, end - start);
  const top = useMemo(() => {
    if (max) return max;
    let m = 0;
    for (const s of series) for (const v of s.data) if (v != null && v > m) m = v;
    if (m <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(m));
    for (const k of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (k * p >= m * 1.05) return k * p;
    return 10 * p;
  }, [series, max]);
  const x = (ts) => PAD_L + ((ts - start) / span) * (W - PAD_L);
  const y = (v) => H - (Math.max(0, Math.min(top, v)) / top) * H;

  const dots = series.map(() => []);    // a sample with no neighbour draws no line: show it as a dot
  const paths = series.map((s, k) => {
    let d = "";
    let pen = false;
    s.data.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      const px = x(times[i] + (step || 60) / 2);
      d += `${pen ? "L" : "M"}${px.toFixed(1)} ${y(v).toFixed(1)}`;
      if (!pen && s.data[i + 1] == null) dots[k].push([px, y(v)]);
      pen = true;
    });
    return d;
  });
  const ticks = [];
  for (let i = 0; i <= 4; i += 1) ticks.push(start + (span * i) / 4);

  const onMove = (e) => {
    const box = wrap.current?.getBoundingClientRect();
    if (!box || !n) return;
    const fx = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
    const i = Math.max(0, Math.min(n - 1, Math.floor(fx * n)));
    setHover({ i, fx });
  };
  const hasData = series.some((s) => s.data.some((v) => v != null));

  return (
    <figure className="hchart" aria-label={label}>
      <div className="hchart-plot" ref={wrap} onMouseMove={onMove} onMouseLeave={() => setHover(null)} style={{ height }}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
          <g className="bigchart-grid">
            {[1, 2, 3].map((k) => <line key={`h${k}`} x1="0" x2={W} y1={(H / 4) * k} y2={(H / 4) * k} />)}
            {[1, 2, 3].map((k) => <line key={`v${k}`} x1={(W / 4) * k} x2={(W / 4) * k} y1="0" y2={H} />)}
          </g>
          {bands.map((b, i) => {
            const x1 = x(Math.max(start, b.from));
            const x2 = x(Math.min(end, b.to));
            if (x2 <= x1) return null;
            return <rect key={i} x={x1} y="0" width={Math.max(1.5, x2 - x1)} height={H} className={`hchart-band ${b.tone || ""}`} />;
          })}
          {series.map((s, i) => (
            <g key={s.id}>
              <path d={paths[i]} fill="none" stroke={s.color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
              {dots[i].map(([cx, cy], j) => <rect key={j} x={cx - 1.5} y={cy - 3} width="3" height="6" fill={s.color} />)}
            </g>
          ))}
          {hover && <line x1={x(times[hover.i] + (step || 60) / 2)} x2={x(times[hover.i] + (step || 60) / 2)} y1="0" y2={H} className="hchart-cursor" />}
          <rect x="0" y="0" width={W} height={H} fill="none" className="bigchart-frame" />
        </svg>
        <span className="hchart-y hchart-y-top num">{num(top, digits, lang)}{unit}</span>
        <span className="hchart-y hchart-y-mid num">{num(top / 2, digits, lang)}{unit}</span>
        {!hasData && <span className="hchart-empty help">—</span>}
        {hover && (
          <div className={`hchart-tip ${hover.fx > 0.6 ? "left" : ""}`} style={{ left: `${hover.fx * 100}%` }} role="status">
            <div className="help">{fmtWhen(times[hover.i], step, lang)}</div>
            {series.map((s) => (
              <div key={s.id} className="flex items-center gap-1.5">
                <i className="hchart-key" style={{ background: s.color }} />
                <span className="trunc">{s.name}</span>
                <b className="num ml-auto">{s.data[hover.i] == null ? "—" : `${num(s.data[hover.i], digits, lang)}${unit}`}</b>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="hchart-x help num">
        {ticks.map((ts, i) => <span key={i}>{fmtTick(ts, span, lang)}</span>)}
      </div>
    </figure>
  );
}
