import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { clock, elapsed, num, rel } from "../format.js";
import { Chip, Empty, ErrorBox, Icon, ICONS, Section, Spinner, useLoad } from "../components/ui.jsx";
import { usePoll } from "../components/hooks.js";
import HistoryChart, { SPARK_COLORS } from "../components/HistoryChart.jsx";

const RANGES = [1, 6, 24, 168, 720];
const RANGE_KEY = "prometheus.history.hours";
const PEAK_KEY = "prometheus.history.peak";
const readRange = () => { try { const v = Number(localStorage.getItem(RANGE_KEY)); return RANGES.includes(v) ? v : 24; } catch { return 24; } };
const keep = (k, v) => { try { localStorage.setItem(k, String(v)); } catch { /* private window */ } };

/** The Historial page: days of GPU, memory, temperature and power per Spark, energy, and the outages of Sparks and model servers. */
export default function Historial({ param }) {
  const { t, lang, ov } = useApp();
  const [hours, setHours] = useState(readRange);
  const [peak, setPeak] = useState(() => { try { return localStorage.getItem(PEAK_KEY) === "1"; } catch { return false; } });
  const node = param || "";
  const hist = useLoad(() => api.longHistory(node, hours), [node, hours]);
  usePoll(() => hist.reload(), hours <= 6 ? 20000 : 60000, true);
  const d = hist.data;
  const nodes = ov?.nodes || [];

  const ids = useMemo(() => (d ? Object.keys(d.nodes) : []), [d]);
  const colorOf = useMemo(() => {
    const all = nodes.map((n) => n.id);
    return (id) => {
      const conf = nodes.find((n) => n.id === id)?.color;
      return conf || SPARK_COLORS[Math.max(0, all.indexOf(id)) % SPARK_COLORS.length];
    };
  }, [nodes]);
  const nameOf = (id) => d?.names?.[id] || nodes.find((n) => n.id === id)?.name || id;
  const series = (field) => ids.map((id) => ({ id, name: nameOf(id), color: colorOf(id), data: d.nodes[id][field] || [] }));
  const bands = (d?.incidents || []).map((i) => ({ from: i.started, to: i.ended || Date.now() / 1000, tone: i.expected ? "expected" : i.kind }));
  const price = d?.energy_price_kwh || 0;
  const pick = (avg, max) => (peak ? max : avg);

  const setRange = (h) => { setHours(h); keep(RANGE_KEY, h); };
  const togglePeak = (v) => { setPeak(v); keep(PEAK_KEY, v ? "1" : "0"); };
  const go = (id) => { window.location.hash = id ? `#/historial/${encodeURIComponent(id)}` : "#/historial"; };

  const charts = d ? [
    { key: "gpu", title: t(peak ? "hist_gpu_peak" : "hist_gpu"), field: pick("gpu", "gpu_max"), unit: " %", max: 100 },
    { key: "mem", title: t("hist_mem"), field: "mem", unit: " %", max: 100 },
    { key: "temp", title: t(peak ? "hist_temp_peak" : "hist_temp"), field: pick("temp", "temp_max"), unit: " °C" },
    { key: "power", title: t(peak ? "hist_power_peak" : "hist_power"), field: pick("power", "power_max"), unit: " W" },
  ] : [];
  const totalKwh = ids.reduce((s, id) => s + (d?.stats?.[id]?.energy_kwh || 0), 0);
  const active = (ov?.alerts?.active || []);

  return (
    <div className="space-y-6">
      <header className="page-head">
        <span className="spark-ico spark-ico-lg"><Icon d={ICONS.clock} size={30} /></span>
        <div className="min-w-0 flex-1">
          <h1>{t("hist_title")}{node ? ` · ${nameOf(node)}` : ""}</h1>
          <p className="help">{t("hist_help", { since: d?.store?.oldest ? clock(d.store.oldest, lang) : "—" })}</p>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div className="seg" role="group" aria-label={t("hist_range")}>
          {RANGES.map((h) => <button key={h} type="button" aria-pressed={h === hours} onClick={() => setRange(h)}>{t(`hist_r_${h}`)}</button>)}
        </div>
        <div className="seg" role="group" aria-label={t("hist_which")}>
          <button type="button" aria-pressed={!node} onClick={() => go("")}>{t("hist_all")}</button>
          {nodes.map((n) => <button key={n.id} type="button" aria-pressed={node === n.id} onClick={() => go(n.id)}>{n.name}</button>)}
        </div>
        <div className="seg" role="group" aria-label={t("hist_value")}>
          <button type="button" aria-pressed={!peak} onClick={() => togglePeak(false)}>{t("hist_avg")}</button>
          <button type="button" aria-pressed={peak} onClick={() => togglePeak(true)}>{t("hist_peak")}</button>
        </div>
        {hist.loading && d && <Spinner />}
      </div>

      {active.length > 0 && (
        <div className="banner banner-danger" role="alert">
          {active.map((a) => <div key={a.id}>{alertLine(a, t, lang)}</div>)}
        </div>
      )}

      {hist.error && <ErrorBox error={hist.error} onRetry={hist.reload} />}
      {!d && !hist.error && <div className="help flex items-center gap-2"><Spinner /> {t("loading")}</div>}

      {d && (
        <>
          <div className="legend">
            {ids.map((id) => <span key={id}><i style={{ background: colorOf(id) }} />{nameOf(id)}</span>)}
            {bands.length > 0 && <span><i className="hchart-key-band" />{t("hist_band")}</span>}
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            {charts.map((c) => (
              <section key={c.key} className="panel space-y-2" aria-label={c.title}>
                <h2 className="text-[15px]">{c.title}</h2>
                <HistoryChart t={d.t} step={d.step} series={series(c.field)} max={c.max} unit={c.unit} bands={bands} lang={lang} label={c.title} />
              </section>
            ))}
          </div>

          <Section title={t("hist_summary")} sub={t(`hist_r_${hours}`)}>
            <div className="panel p-0 scroll-x">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Spark</th><th className="r">{t("hist_col_data")}</th><th className="r">{t("hist_col_gpu_avg")}</th><th className="r">{t("hist_col_gpu_max")}</th>
                    <th className="r">{t("hist_col_temp_max")}</th><th className="r">{t("hist_col_power_avg")}</th><th className="r">{t("hist_col_power_max")}</th>
                    <th className="r">{t("hist_col_energy")}</th>{price > 0 && <th className="r">{t("hist_col_cost")}</th>}
                  </tr>
                </thead>
                <tbody>
                  {ids.map((id) => {
                    const s = d.stats[id] || {};
                    const v = (x, unit, dg = 0) => (x == null ? "—" : `${num(x, dg, lang)}${unit}`);
                    return (
                      <tr key={id}>
                        <td><span className="inline-flex items-center gap-1.5"><i className="hchart-key" style={{ background: colorOf(id) }} />{node ? nameOf(id) : <button type="button" className="btn-link" onClick={() => go(id)}>{nameOf(id)}</button>}</span></td>
                        <td className="r num">{s.minutes_total ? `${num((100 * s.minutes) / s.minutes_total, 0, lang)} %` : "—"}</td>
                        <td className="r num">{v(s.gpu_avg, " %")}</td>
                        <td className="r num">{v(s.gpu_max, " %")}</td>
                        <td className="r num">{v(s.temp_max, " °C")}</td>
                        <td className="r num">{v(s.power_avg, " W")}</td>
                        <td className="r num">{v(s.power_max, " W")}</td>
                        <td className="r num">{v(s.energy_kwh, " kWh", 2)}</td>
                        {price > 0 && <td className="r num">{num((s.energy_kwh || 0) * price, 2, lang)} €</td>}
                      </tr>
                    );
                  })}
                  {ids.length > 1 && (
                    <tr className="font-semibold">
                      <td>{t("hist_total")}</td><td /><td /><td /><td /><td /><td />
                      <td className="r num">{num(totalKwh, 2, lang)} kWh</td>
                      {price > 0 && <td className="r num">{num(totalKwh * price, 2, lang)} €</td>}
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <p className="help">{t("hist_energy_note")}{price > 0 ? "" : ` ${t("hist_price_hint")}`}</p>
          </Section>

          <Section title={t("hist_incidents")} count={(d.incidents || []).length || undefined}>
            {(d.incidents || []).length ? (
              <div className="panel p-0 scroll-x">
                <table className="tbl">
                  <thead><tr><th>{t("status")}</th><th>{t("hist_inc_what")}</th><th>{t("hist_inc_start")}</th><th className="r">{t("hist_inc_len")}</th><th>{t("hist_inc_detail")}</th></tr></thead>
                  <tbody>
                    {d.incidents.map((i) => (
                      <tr key={i.id}>
                        <td>
                          {!i.ended ? <Chip className="chip-danger">{t("hist_inc_open")}</Chip> : <Chip className="chip-ok">{t("hist_inc_closed")}</Chip>}
                          {i.expected && <Chip className="ml-1">{t("hist_inc_expected")}</Chip>}
                        </td>
                        <td><Icon d={i.kind === "spark" ? ICONS.computer : ICONS.models} size={14} /> {i.kind === "spark" ? i.name : t("hist_inc_server", { name: i.name })}</td>
                        <td className="num">{clock(i.started, lang)}</td>
                        <td className="r num">{elapsed(i.down_s)}</td>
                        <td className="mono help">{i.note === "gone" ? t("hist_inc_gone") : (i.error || "—")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <Empty icon={ICONS.check} title={t("hist_no_incidents")}>{t("hist_no_incidents_help")}</Empty>}
          </Section>
        </>
      )}
    </div>
  );
}

export function alertLine(a, t, lang) {
  const since = rel(a.started, lang);
  return a.kind === "spark" ? t("alert_spark_down", { name: a.name, since }) : t("alert_server_down", { name: a.name, since });
}
