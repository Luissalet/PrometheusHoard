import React from "react";
import { useApp } from "../context.js";
import { bytes, gib, num, pct, rate, linkSpeed, uptime, rel } from "../format.js";
import { Bar, Chip, Empty, Icon, ICONS, Section, Spinner } from "../components/ui.jsx";
import { CoreBars, Sparkline, niceMax } from "../components/charts.jsx";
import { useHistories } from "../components/hooks.js";
import { PowerButton, powerState } from "../components/power.jsx";
import { columns, fabricOf, mainDisk, memFrac } from "../components/node.js";

const C = { gpu: "var(--hoard-accent)", cpu: "#5aa9e6", mem: "#b48ef0", net: "#e0a84a", net2: "#e07a5f" };

export default function Equipo() {
  const { t, lang, ov } = useApp();
  const nodes = ov?.nodes || [];
  const hist = useHistories(nodes.filter((n) => n.online).map((n) => n.id), 5, 2000);
  if (!ov) return <div className="flex items-center gap-2 help"><Spinner /> {t("loading")}</div>;
  const c = ov.cluster || {};
  const loaded = (ov.deployments || []).filter((d) => d.state === "running");
  return (
    <div className="space-y-6">
      <header className="page-head">
        <div className="min-w-0">
          <h1>{t("cluster_title")}</h1>
          <p className="help">{t("cluster_help")}</p>
        </div>
        <PowerButton nodes={nodes} />
      </header>

      <section className="panel summary" aria-label={t("cluster_summary")}>
        <div className="kpi">
          <span>{t("kpi_online")}</span>
          <b className="num">{t("n_of_total", { n: c.online ?? 0, total: c.total ?? 0 })}</b>
          <div className="summary-dots">
            {nodes.map((n) => <span key={n.id} className="dot" style={{ background: n.online ? "var(--ok)" : "var(--hoard-text-dim)" }} />)}
          </div>
        </div>
        <div className="kpi kpi-wide">
          <span>{t("kpi_memory")}</span>
          <b className="num">{gib(c.memory_used, lang)} <small>/ {gib(c.memory_total, lang)}</small></b>
          <Bar value={c.memory_total ? c.memory_used / c.memory_total : 0} />
        </div>
        <div className="kpi">
          <span>{t("kpi_gpu")}</span>
          <b className="num">{pct(c.gpu_util, lang, 1)}</b>
        </div>
        <div className="kpi">
          <span>{t("kpi_power")}</span>
          <b className="num">{c.power_w === null || c.power_w === undefined ? "—" : `${num(c.power_w, 0, lang)} W`}</b>
        </div>
        <div className="kpi">
          <span>{t("kpi_loaded")}</span>
          <b className="num">{loaded.length}</b>
          <a className="btn-link text-[12px]" href="#/modelos">{t("see_models")}</a>
        </div>
      </section>

      {!nodes.length ? (
        <Empty icon={ICONS.computer} title={t("no_sparks")} action={<a className="btn btn-primary" href="#/ajustes">{t("go_settings")}</a>}>{t("no_sparks_help")}</Empty>
      ) : (
        <div className="spark-grid">
          {nodes.map((n) => <SparkCard key={n.id} node={n} samples={hist[n.id]} />)}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title={t("jobs_running")} count={(ov.jobs || []).length || undefined} actions={<a className="btn btn-sm" href="#/modelos?s=jobs">{t("see_all")}</a>}>
          {(ov.jobs || []).length ? (
            <div className="panel p-0 divide-list">
              {ov.jobs.map((j) => (
                <div key={j.id} className="row-item">
                  <Icon d={j.kind === "download" ? ICONS.cloud : j.kind === "copy" ? ICONS.copy : ICONS.recipes} size={16} />
                  <div className="min-w-0 flex-1">
                    <div className="trunc">{j.title}</div>
                    <Bar value={j.progress ?? 0} alarm={false} thin className={j.progress === null || j.progress === undefined ? "bar-indeterminate" : ""} />
                  </div>
                  <span className="help num">{j.progress === null || j.progress === undefined ? "…" : pct(j.progress * 100, lang)}</span>
                </div>
              ))}
            </div>
          ) : <p className="help panel">{t("no_jobs_running")}</p>}
        </Section>
        <Section title={t("activity")}>
          {(ov.events || []).length ? (
            <div className="panel p-0 divide-list">
              {[...ov.events].reverse().slice(0, 8).map((e, i) => (
                <div key={`${e.t}-${i}`} className="row-item">
                  <span className="dot" style={{ background: eventTone(e.type) }} />
                  <span className="min-w-0 flex-1 trunc">{eventText(e, t, ov)}</span>
                  <span className="help shrink-0">{rel(e.t, lang)}</span>
                </div>
              ))}
            </div>
          ) : <p className="help panel">{t("no_activity")}</p>}
        </Section>
      </div>
    </div>
  );
}

function eventTone(type) {
  if (/failed|offline|lost/.test(type)) return "var(--danger)";
  if (/online|done|running/.test(type)) return "var(--ok)";
  if (/power|stopp|cancel/.test(type)) return "var(--warn)";
  return "var(--hoard-text-dim)";
}

export function eventText(e, t, ov) {
  const name = (ov?.nodes || []).find((n) => n.id === e.node)?.name || e.name || e.node || "";
  const recipe = (ov?.deployments || []).find((d) => d.recipe === e.recipe)?.title || e.recipe || "";
  const key = `ev_${e.type.replace(/\./g, "_")}`;
  const text = t(key, { name, title: e.title || "", recipe });
  return text === key ? `${e.type} ${name || recipe || e.title || ""}` : text;
}

function SparkCard({ node: n, samples }) {
  const { t, lang } = useApp();
  const ps = powerState(n, t);
  const open = () => { window.location.hash = `#/spark/${encodeURIComponent(n.id)}`; };
  const col = columns(samples);
  const fab = fabricOf(n);
  const disk = mainDisk(n);
  const netMax = niceMax([...col.fabricRx, ...col.fabricTx], 1024);
  const offline = !n.online;
  return (
    <article className={`spark-card ${offline ? "is-offline" : ""}`} onClick={open} style={n.color ? { "--node-color": n.color } : undefined}>
      <header className="spark-head">
        <span className="spark-ico"><Icon d={ICONS.computer} size={22} /><span className="dot" style={{ background: n.online ? "var(--ok)" : "var(--hoard-text-dim)" }} /></span>
        <div className="min-w-0 flex-1">
          <h2 className="trunc"><a href={`#/spark/${encodeURIComponent(n.id)}`} onClick={(e) => e.stopPropagation()}>{n.name}</a></h2>
          <div className="help">{n.hostname || n.host || n.ssh}{n.online && n.uptime_s ? ` · ${t("up_for", { d: uptime(n.uptime_s, lang) })}` : ""}</div>
        </div>
        <Chip className={ps.tone}>{ps.busy && <span className="spinner spinner-xs" />}{ps.label}</Chip>
        <span onClick={(e) => e.stopPropagation()}><PowerButton node={n} compact /></span>
      </header>

      {offline ? (
        <div className="spark-off">
          <p>{{ off: t("spark_is_off"), sleeping: t("spark_is_asleep"), restarting: t("spark_restarting"), shutting_down: t("spark_shutting_down"), waking: t("spark_waking") }[n.power_state] || t("spark_unreachable")}</p>
          {n.error && <p className="help mono">{n.error}</p>}
          {n.error_since && <p className="help">{t("since", { when: rel(n.error_since, lang) })}</p>}
        </div>
      ) : (
        <>
          <div className="metric-grid">
            <Metric label={t("m_gpu")} value={pct(n.gpu?.util, lang)} color={C.gpu}
              sub={[n.gpu?.temp_c != null && `${num(n.gpu.temp_c, 0, lang)} °C`, n.gpu?.power_w != null && `${num(n.gpu.power_w, 0, lang)} W`,
                n.gpu?.clock_mhz && `${num(n.gpu.clock_mhz, 0, lang)}${n.gpu.clock_max_mhz ? `/${num(n.gpu.clock_max_mhz, 0, lang)}` : ""} MHz`].filter(Boolean).join(" · ")}
              chart={<Sparkline series={[{ data: col.gpu, color: C.gpu }]} label={t("m_gpu")} />} />
            <Metric label={t("m_cpu")} value={pct(n.cpu?.percent, lang)} color={C.cpu}
              sub={[t("n_cores", { n: n.cpu?.cores ?? "—" }), n.cpu?.temp_c != null && `${num(n.cpu.temp_c, 0, lang)} °C`].filter(Boolean).join(" · ")}
              extra={<CoreBars values={n.cpu?.per_core} label={t("per_core")} />}
              chart={<Sparkline series={[{ data: col.cpu, color: C.cpu }]} label={t("m_cpu")} />} />
            <Metric label={t("m_memory")} value={gib(n.memory?.used, lang)} color={C.mem}
              sub={t("mem_sub", { total: gib(n.memory?.total, lang, 0) })}
              extra={<Bar value={memFrac(n)} />}
              chart={<Sparkline series={[{ data: col.mem, color: C.mem }]} label={t("m_memory")} />} />
            <Metric label={t("m_fabric_card")} value={`↓ ${rate(fab.rx, lang)}`} color={C.net}
              extra={<div className="help num metric-up">↑ {rate(fab.tx, lang)}</div>}
              sub={fab.total ? t("links_up", { up: fab.up, total: fab.total, speed: linkSpeed(fab.speed, lang) }) : t("no_fabric")}
              chart={<Sparkline series={[{ data: col.fabricRx, color: C.net }, { data: col.fabricTx, color: C.net2, dashed: true }]} max={netMax} label={t("m_fabric")} />} />
          </div>

          {disk && (
            <div className="drive">
              <Icon d={ICONS.drive} size={22} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2"><span className="trunc">{t("disk_label", { path: disk.path })}</span></div>
                <Bar value={disk.total ? 1 - disk.free / disk.total : 0} />
                <div className="help">{t("free_of", { free: bytes(disk.free, lang), total: bytes(disk.total, lang) })}</div>
              </div>
            </div>
          )}

          {fab.links.length > 0 && (
            <div className="links">
              {fab.links.map((l) => (
                <span key={l.name} className="link-chip">
                  <span className="dot" style={{ background: l.up ? "var(--ok)" : "var(--danger)" }} />
                  <span className="mono">{l.name}</span>
                  <span className="help">{l.up ? linkSpeed(l.speed_mbps, lang) : t("link_down")}</span>
                </span>
              ))}
            </div>
          )}

          <div className="loaded">
            <span className="help">{(n.deployments || []).some((d) => d.state === "starting") && !(n.deployments || []).some((d) => d.state === "running") ? t("loading_models") : t("loaded_models")}:</span>
            {(n.deployments || []).length ? n.deployments.map((d) => (
              <Chip key={d.recipe} className={d.state === "running" ? "chip-accent" : d.state === "failed" ? "chip-danger" : "chip-amber"}>
                {d.state !== "running" && d.state !== "failed" && <span className="spinner spinner-xs" />}
                {d.title}
                <span className="role">{t(`role_${d.role}`)}</span>
              </Chip>
            )) : <span className="help">{t("none_loaded")}</span>}
          </div>
        </>
      )}
    </article>
  );
}

function Metric({ label, value, sub, chart, extra, color }) {
  return (
    <div className="metric" style={{ "--metric": color }}>
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        <b className="num">{value}</b>
      </div>
      {extra}
      <div className="metric-chart">{chart}</div>
      <div className="help metric-sub">{sub}</div>
    </div>
  );
}
