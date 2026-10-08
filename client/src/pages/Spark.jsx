import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { bytes, gib, num, pct, rate, linkSpeed, uptime, rel } from "../format.js";
import { Bar, Chip, CopyButton, ErrorBox, Icon, ICONS, Section, Spinner, useLoad } from "../components/ui.jsx";
import { BigChart, CoreBars, Sparkline, niceMax } from "../components/charts.jsx";
import { useHistories, usePoll } from "../components/hooks.js";
import { PowerButton, powerState } from "../components/power.jsx";
import { columns, fabricOf, mainDisk, memFrac } from "../components/node.js";

const C = { gpu: "var(--hoard-accent)", cpu: "#5aa9e6", mem: "#b48ef0", net: "#e0a84a", net2: "#e07a5f", lan: "#4fb2a0", lan2: "#7fd3c3", power: "#e6c35a" };
const SPANS = [1, 5, 10, 30];

export default function Spark({ param }) {
  const { t, lang, ov } = useApp();
  const id = param;
  const [minutes, setMinutes] = useState(5);
  const [res, setRes] = useState("gpu");
  const detail = useLoad(() => api.call("spark_status", { node: id }), [id]);
  usePoll(() => detail.reload(), 2000, true);
  const hist = useHistories(id ? [id] : [], minutes, 2000);
  const base = (ov?.nodes || []).find((n) => n.id === id);
  const n = detail.data && detail.data.id === id ? { ...detail.data, deployments: base?.deployments || detail.data.deployments || [] } : base;

  if (!n) {
    if (detail.error) return <div className="space-y-3"><a className="btn btn-sm" href="#/"><Icon d={ICONS.back} size={14} />{t("nav_cluster")}</a><ErrorBox error={detail.error} onRetry={detail.reload} /></div>;
    return <div className="flex items-center gap-2 help"><Spinner /> {t("loading")}</div>;
  }
  const ps = powerState(n, t);
  const col = columns(hist[id]);
  const fab = fabricOf(n);
  const lan = Object.entries(n.net || {}).filter(([, v]) => !v.fabric);
  const lanRx = lan.reduce((s, [, v]) => s + (v.rx_bps || 0), 0);
  const lanTx = lan.reduce((s, [, v]) => s + (v.tx_bps || 0), 0);
  const disk = mainDisk(n);
  const span = t("span_minutes", { n: minutes });
  const fabMax = niceMax([...col.fabricRx, ...col.fabricTx], 1024);
  const lanMax = niceMax([...col.lanRx, ...col.lanTx], 1024);
  const powMax = niceMax(col.power.concat([n.gpu?.power_limit_w || 0]), 50);

  const resources = [
    { id: "gpu", label: t("m_gpu"), value: pct(n.gpu?.util, lang), sub: n.gpu?.name || "", color: C.gpu, series: [{ data: col.gpu, color: C.gpu }], max: 100, top: "100 %" },
    { id: "cpu", label: t("m_cpu"), value: pct(n.cpu?.percent, lang), sub: t("n_cores", { n: n.cpu?.cores ?? "—" }), color: C.cpu, series: [{ data: col.cpu, color: C.cpu }], max: 100, top: "100 %" },
    { id: "mem", label: t("m_memory"), value: `${gib(n.memory?.used, lang)}`, sub: `${pct(n.memory?.percent, lang)} · ${gib(n.memory?.total, lang, 0)}`, color: C.mem, series: [{ data: col.mem, color: C.mem }], max: 100, top: gib(n.memory?.total, lang, 0) },
    { id: "fabric", label: t("m_fabric"), value: `↓ ${rate(fab.rx, lang)}`, sub: `↑ ${rate(fab.tx, lang)}`, color: C.net, series: [{ data: col.fabricRx, color: C.net }, { data: col.fabricTx, color: C.net2, dashed: true }], max: fabMax, top: rate(fabMax, lang) },
    { id: "lan", label: t("m_lan"), value: `↓ ${rate(lanRx, lang)}`, sub: `↑ ${rate(lanTx, lang)}`, color: C.lan, series: [{ data: col.lanRx, color: C.lan }, { data: col.lanTx, color: C.lan2, dashed: true }], max: lanMax, top: rate(lanMax, lang) },
    { id: "power", label: t("m_power"), value: n.gpu?.power_w == null ? "—" : `${num(n.gpu.power_w, 0, lang)} W`, sub: n.gpu?.temp_c == null ? "" : `${num(n.gpu.temp_c, 0, lang)} °C`, color: C.power, series: [{ data: col.power, color: C.power }], max: powMax, top: `${num(powMax, 0, lang)} W` },
  ];
  const sel = resources.find((r) => r.id === res) || resources[0];

  const stats = {
    gpu: [[t("st_util"), pct(n.gpu?.util, lang)], [t("st_temp"), n.gpu?.temp_c == null ? "—" : `${num(n.gpu.temp_c, 0, lang)} °C`],
      [t("st_power"), n.gpu?.power_w == null ? "—" : `${num(n.gpu.power_w, 1, lang)} W${n.gpu.power_limit_w ? ` / ${num(n.gpu.power_limit_w, 0, lang)} W` : ""}`],
      [t("st_clock"), n.gpu?.clock_mhz ? `${num(n.gpu.clock_mhz, 0, lang)} / ${num(n.gpu.clock_max_mhz, 0, lang)} MHz` : "—"],
      [t("st_pstate"), n.gpu?.pstate || "—"], [t("st_driver"), n.gpu?.driver || "—"], [t("st_gpu_apps"), String((n.gpu?.apps || []).length)]],
    cpu: [[t("st_util"), pct(n.cpu?.percent, lang)], [t("st_cores"), String(n.cpu?.cores ?? "—")],
      [t("st_load"), (n.cpu?.load || []).map((x) => num(x, 2, lang)).join(" · ") || "—"], [t("st_temp"), n.cpu?.temp_c == null ? "—" : `${num(n.cpu.temp_c, 0, lang)} °C`],
      [t("st_uptime"), uptime(n.uptime_s, lang)]],
    mem: [[t("st_in_use"), gib(n.memory?.used, lang)], [t("st_available"), gib(n.memory?.available, lang)], [t("st_cached"), gib(n.memory?.cached, lang)],
      [t("st_gpu_mem"), n.memory?.gpu_apps ? gib(n.memory.gpu_apps, lang) : "—"], [t("st_total"), gib(n.memory?.total, lang)]],
    fabric: [[t("st_rx"), rate(fab.rx, lang)], [t("st_tx"), rate(fab.tx, lang)], [t("st_links"), `${fab.up} / ${fab.total}`], [t("st_speed"), linkSpeed(fab.speed, lang)]],
    lan: [[t("st_rx"), rate(lanRx, lang)], [t("st_tx"), rate(lanTx, lang)], [t("st_ifaces"), String(lan.length)]],
    power: [[t("st_power"), n.gpu?.power_w == null ? "—" : `${num(n.gpu.power_w, 1, lang)} W`], [t("st_limit"), n.gpu?.power_limit_w ? `${num(n.gpu.power_limit_w, 0, lang)} W` : "—"],
      [t("st_temp_gpu"), n.gpu?.temp_c == null ? "—" : `${num(n.gpu.temp_c, 0, lang)} °C`], [t("st_temp_cpu"), n.cpu?.temp_c == null ? "—" : `${num(n.cpu.temp_c, 0, lang)} °C`]],
  }[sel.id];

  const nets = Object.entries(n.net || {}).map(([name, v]) => ({ name, ...v })).sort((a, b) => Number(b.fabric) - Number(a.fabric) || a.name.localeCompare(b.name));

  return (
    <div className="space-y-6">
      <div><a className="btn btn-sm btn-ghost" href="#/"><Icon d={ICONS.back} size={14} />{t("nav_cluster")}</a></div>
      <header className="page-head">
        <span className="spark-ico spark-ico-lg"><Icon d={ICONS.computer} size={30} /><span className="dot" style={{ background: n.online ? "var(--ok)" : "var(--hoard-text-dim)" }} /></span>
        <div className="min-w-0 flex-1">
          <h1 className="trunc">{n.name}</h1>
          <p className="help">{[n.hostname || n.host, n.os, n.online && n.uptime_s ? t("up_for", { d: uptime(n.uptime_s, lang) }) : null].filter(Boolean).join(" · ")}</p>
        </div>
        <Chip className={ps.tone}>{ps.busy && <span className="spinner spinner-xs" />}{ps.label}</Chip>
        <a className="btn" href={`#/archivos/${encodeURIComponent(n.id)}`}><Icon d={ICONS.folder} size={15} />{t("open_files")}</a>
        <PowerButton node={n} />
      </header>

      {!n.online && (
        <div className="banner banner-warn">
          {n.power_state === "off" ? t("spark_is_off") : t("spark_unreachable")}
          {n.error && <span className="mono block">{n.error}</span>}
          {n.error_since && <span className="help block">{t("since", { when: rel(n.error_since, lang) })}</span>}
        </div>
      )}

      <section className="panel perf" aria-label={t("performance")}>
        <div className="perf-list" role="tablist" aria-label={t("performance")}>
          {resources.map((r) => (
            <button key={r.id} type="button" role="tab" aria-selected={r.id === sel.id} className="perf-item" onClick={() => setRes(r.id)} style={{ "--metric": r.color }}>
              <Sparkline series={r.series} max={r.max} height={30} className="perf-mini" label={r.label} />
              <span className="min-w-0">
                <span className="block font-semibold">{r.label}</span>
                <span className="block help num trunc">{r.value}</span>
                <span className="block help num trunc">{r.sub}</span>
              </span>
            </button>
          ))}
        </div>
        <div className="perf-main min-w-0">
          <div className="flex flex-wrap items-baseline gap-3">
            <h2 className="text-[20px]">{sel.label}</h2>
            <span className="help trunc">{sel.id === "gpu" ? n.gpu?.name : sel.id === "cpu" ? `${n.cpu?.cores ?? ""} ${t("cores")}` : ""}</span>
            <div className="ml-auto seg" role="group" aria-label={t("span")}>
              {SPANS.map((m) => <button key={m} type="button" aria-pressed={m === minutes} onClick={() => setMinutes(m)}>{m} min</button>)}
            </div>
          </div>
          <BigChart series={sel.series} max={sel.max} topLabel={sel.top} spanLabel={span} label={sel.label} />
          {(sel.id === "fabric" || sel.id === "lan") && (
            <div className="legend"><span><i style={{ background: sel.series[0].color }} />{t("st_rx")}</span><span><i className="dashed" style={{ borderColor: sel.series[1].color }} />{t("st_tx")}</span></div>
          )}
          {sel.id === "cpu" && (n.cpu?.per_core || []).length > 0 && (
            <div className="space-y-1">
              <span className="label">{t("per_core")}</span>
              <CoreBars values={n.cpu.per_core} label={t("per_core")} />
            </div>
          )}
          {sel.id === "mem" && <div className="space-y-1"><span className="label">{t("unified_memory")}</span><Bar value={memFrac(n)} /><p className="help">{t("unified_memory_help")}</p></div>}
          <dl className="stats">
            {stats.map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="num">{v}</dd></div>)}
          </dl>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <Section title={t("system")}>
          <dl className="panel props">
            <Prop k={t("hostname")} v={n.hostname} mono />
            <Prop k={t("os")} v={n.os} />
            <Prop k={t("kernel")} v={n.kernel} mono />
            <Prop k={t("user_home")} v={n.user ? `${n.user} · ${n.home || ""}` : n.home} mono />
            <Prop k={t("ssh_alias")} v={n.ssh} mono />
            <Prop k={t("host")} v={n.host} mono />
            <Prop k={t("api_host")} v={n.api_host} mono />
            <Prop k={t("gpu_name")} v={n.gpu?.name} />
            <Prop k={t("probe")} v={n.probe_ms != null ? `${num(n.probe_ms, 0, lang)} ms` : "—"} />
            {disk && <Prop k={t("disk_label", { path: disk.path })} v={t("free_of", { free: bytes(disk.free, lang), total: bytes(disk.total, lang) })} />}
          </dl>
        </Section>
        <Section title={t("loaded_models")} count={(n.deployments || []).length || undefined}>
          {(n.deployments || []).length ? (
            <div className="panel p-0 divide-list">
              {n.deployments.map((d) => (
                <div key={d.recipe} className="row-item flex-wrap">
                  <Icon d={ICONS.models} size={16} />
                  <div className="min-w-0 flex-1">
                    <div className="trunc font-semibold">{d.title}</div>
                    <div className="help mono trunc">{d.base_url}</div>
                  </div>
                  <Chip>{t(`role_${d.role}`)}</Chip>
                  <Chip className={d.state === "running" ? "chip-ok" : d.state === "failed" ? "chip-danger" : "chip-amber"}>{t(`dep_${d.state}`)}</Chip>
                  {d.base_url && <CopyButton text={d.base_url} label={t("copy_url")} />}
                </div>
              ))}
            </div>
          ) : <p className="panel help">{t("none_loaded_here")} <a className="btn-link" href="#/modelos">{t("go_models")}</a></p>}
          {(n.servers || []).length > 0 && (
            <>
              <h3 className="pt-2">{t("servers_detected")}</h3>
              <div className="panel p-0 scroll-x">
                <table className="tbl">
                  <thead><tr><th>{t("engine")}</th><th>{t("model")}</th><th>{t("served_name")}</th><th className="r">{t("port")}</th><th className="r">{t("context")}</th><th className="r">TP</th><th className="r">PID</th><th className="r">{t("memory")}</th></tr></thead>
                  <tbody>
                    {n.servers.map((s, i) => (
                      <tr key={i}><td>{s.engine}</td><td className="mono">{s.model}</td><td className="mono">{s.served_name}</td><td className="r num">{s.port}</td>
                        <td className="r num">{s.max_len ? num(s.max_len, 0, lang) : "—"}</td><td className="r num">{s.tp ?? "—"}</td><td className="r num">{s.pid}</td><td className="r num">{bytes(s.rss, lang)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Section>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Section title={t("processes")} sub={t("processes_help")}>
          {(n.top || []).length ? (
            <div className="panel p-0 scroll-x">
              <table className="tbl">
                <thead><tr><th className="r">PID</th><th className="r">{t("memory")}</th><th>{t("gpu")}</th><th>{t("command")}</th></tr></thead>
                <tbody>
                  {n.top.map((p) => (
                    <tr key={p.pid}><td className="r num">{p.pid}</td><td className="r num">{bytes(p.rss, lang)}</td><td>{p.gpu ? <Chip className="chip-accent">GPU</Chip> : ""}</td><td className="mono">{p.cmd}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="panel help">{n.online ? t("no_processes") : "—"}</p>}
          {(n.gpu?.apps || []).length > 0 && (
            <div className="panel p-0 scroll-x">
              <table className="tbl">
                <thead><tr><th className="r">PID</th><th>{t("gpu_apps")}</th><th className="r">{t("memory")}</th></tr></thead>
                <tbody>{n.gpu.apps.map((a) => <tr key={a.pid}><td className="r num">{a.pid}</td><td className="mono">{a.name}</td><td className="r num">{a.mem_mb != null ? gib(a.mem_mb * 1024 * 1024, lang) : "—"}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </Section>
        <Section title={t("containers")} count={(n.containers || []).length || undefined}>
          {(n.containers || []).length ? (
            <div className="panel p-0 scroll-x">
              <table className="tbl">
                <thead><tr><th>{t("name")}</th><th>{t("image")}</th><th>{t("status")}</th><th>ID</th></tr></thead>
                <tbody>{n.containers.map((c) => <tr key={c.id}><td className="mono">{c.name}</td><td className="mono">{c.image}</td><td>{c.status}</td><td className="mono help">{String(c.id).slice(0, 12)}</td></tr>)}</tbody>
              </table>
            </div>
          ) : <p className="panel help">{n.online ? (n.docker === false ? t("no_docker") : t("no_containers")) : "—"}</p>}
        </Section>
      </div>

      <Section title={t("network")} count={nets.length || undefined}>
        {nets.length ? (
          <div className="panel p-0 scroll-x">
            <table className="tbl">
              <thead><tr><th>{t("iface")}</th><th>{t("status")}</th><th className="r">{t("speed")}</th><th>IPv4</th><th className="r">MTU</th><th className="r">↓</th><th className="r">↑</th></tr></thead>
              <tbody>
                {nets.map((v) => (
                  <tr key={v.name}>
                    <td><span className="mono">{v.name}</span>{v.fabric && <Chip className="chip-accent ml-1">CX7</Chip>}<div className="help mono">{v.mac}</div></td>
                    <td><span className="inline-flex items-center gap-1.5"><span className="dot" style={{ background: v.up ? "var(--ok)" : "var(--danger)" }} />{v.up ? t("link_up") : t("link_down")}</span></td>
                    <td className="r num">{linkSpeed(v.speed_mbps, lang)}</td>
                    <td className="mono">{(v.v4 || []).join(", ") || "—"}</td>
                    <td className="r num">{v.mtu ?? "—"}</td>
                    <td className="r num">{rate(v.rx_bps, lang)}</td>
                    <td className="r num">{rate(v.tx_bps, lang)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="panel help">—</p>}
      </Section>

      {detail.error && <ErrorBox error={detail.error} onRetry={detail.reload} />}
      <p className="help">{t("refreshing_every", { s: 2 })}</p>
    </div>
  );
}

function Prop({ k, v, mono }) {
  return (
    <div className="prop">
      <dt>{k}</dt>
      <dd className={mono ? "mono" : ""}>{v || "—"}</dd>
    </div>
  );
}
