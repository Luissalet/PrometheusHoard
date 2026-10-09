import React from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { clock, compact, latency, num } from "../format.js";
import { Bar, Chip, CopyButton, Empty, ErrorBox, ICONS, Spinner, useLoad } from "../components/ui.jsx";
import { Sparkline, niceMax } from "../components/charts.jsx";
import { usePoll } from "../components/hooks.js";
import Clients from "../components/Clients.jsx";
import SpeedPanel from "../components/Speed.jsx";
import XidPanel from "../components/Xid.jsx";

const C = { decode: "var(--hoard-accent)", prefill: "#5aa9e6", running: "#e0a84a", kv: "#b48ef0" };

const has = (v) => v !== null && v !== undefined;

export default function Sirviendo() {
  const { t, lang } = useApp();
  const load = useLoad(() => api.serving(), []);
  // Every 2 s while the page is open and the tab visible (the backend samples as often only while someone looks).
  usePoll(() => load.reload(), 2000, true);
  const data = load.data;

  if (!data) {
    if (load.error) return <ErrorBox error={load.error} onRetry={load.reload} />;
    return <div className="flex items-center gap-2 help"><Spinner /> {t("loading")}</div>;
  }
  const eps = data.endpoints || [];
  const total = data.totals || {};
  return (
    <div className="space-y-6">
      <header className="page-head">
        <div className="min-w-0">
          <h1>{t("sv_title")}</h1>
          <p className="help">{t("sv_help")}</p>
        </div>
        <div className="kpi serve-total" title={t("sv_served_since", { when: clock(total.since, lang) })}>
          <span>{t("sv_served_total")}</span>
          <b className="num">{compact(total.generation_tokens, lang)}</b>
          <span className="help">{t("sv_served_since", { when: clock(total.since, lang) })}</span>
        </div>
      </header>

      {load.error && <ErrorBox error={load.error} onRetry={load.reload} />}

      <XidPanel />

      {!eps.length ? (
        <Empty icon={ICONS.activity} title={t("sv_empty_title")} action={<a className="btn btn-primary" href="#/modelos">{t("go_models")}</a>}>{t("sv_empty_help")}</Empty>
      ) : (
        <div className="serve-grid">
          {eps.map((e) => <EndpointCard key={e.recipe} e={e} onChanged={load.reload} />)}
        </div>
      )}
    </div>
  );
}

function EndpointCard({ e, onChanged }) {
  const { t, lang, nodeName } = useApp();
  const now = e.now || {};
  const lat = e.latency || {};
  const series = e.series || [];
  const col = (f) => series.map((p) => p[f]);
  const decode = col("decode_tps");
  const prefill = col("prefill_tps");
  const tot = e.totals || {};
  const waiting = has(now.waiting) ? now.waiting : null;
  const reading = e.ok === null || (e.ok && !has(now.decode_tps) && !has(now.running));
  const failed = e.ok === false;
  const errorText = e.error === "no vllm metrics" ? t("sv_no_metrics") : t("sv_failed", { error: e.error });
  const lag = (key) => lat[key] && has(lat[key].p50) ? lat[key] : null;
  const ttft = lag("ttft");
  const itl = lag("itl");

  return (
    <article className={`serve-card ${failed ? "is-offline" : ""}`} aria-label={e.title}>
      <header className="serve-head">
        <span className="dot" style={{ background: failed ? "var(--danger)" : e.ok ? "var(--ok)" : "var(--hoard-text-dim)" }} />
        <div className="min-w-0 flex-1">
          <h2 className="trunc">{e.title}</h2>
          <div className="help mono">{(e.models || []).join(", ")}</div>
        </div>
        {e.engine && <Chip>{e.engine}</Chip>}
        {now.asleep && <Chip className="chip-amber">{t("sv_asleep")}</Chip>}
        {e.detected && <Chip className="chip-amber">{t("detected_label")}</Chip>}
      </header>

      <div className="serve-nodes">
        {(e.nodes || []).map((n) => (
          <Chip key={n} className="chip-accent">{nodeName(n)}<span className="role">{t(n === e.head ? "role_head" : "role_worker")}</span></Chip>
        ))}
      </div>
      <div className="serve-url">
        <span className="help">{t("sv_base_url")}</span>
        <span className="mono trunc flex-1">{e.base_url}</span>
        <CopyButton text={e.base_url || ""} label={t("copy_url")} />
      </div>

      {failed && <div className="banner banner-danger" role="alert">{errorText}</div>}
      {now.preempting || (now.preemptions_5m || 0) > 0 ? (
        <div className="banner banner-warn" role="status">{t("sv_preempt", { n: now.preemptions_5m || now.preemptions })}</div>
      ) : null}

      {reading && !failed ? (
        <p className="help">{t("sv_first_reading")}</p>
      ) : (
        <>
          <div className="serve-hero" style={{ "--metric": C.decode }}>
            <div>
              <span className="metric-label">{t("sv_generation")}</span>
              <div className="serve-big num">{has(now.decode_tps) ? num(now.decode_tps, 1, lang) : "—"} <small>{t("sv_tps")}</small></div>
            </div>
            <div className="serve-hero-chart">
              <Sparkline series={[{ data: decode, color: C.decode }]} max={niceMax(decode.filter(has), 10)} height={52} label={t("sv_generation")} />
            </div>
          </div>

          <div className="metric-grid">
            <Metric label={t("sv_prefill")} color={C.prefill} value={<>{has(now.prefill_tps) ? num(now.prefill_tps, 1, lang) : "—"} <small>{t("sv_tps")}</small></>}
              sub={t("sv_prefill_sub")} chart={<Sparkline series={[{ data: prefill, color: C.prefill }]} max={niceMax(prefill.filter(has), 10)} label={t("sv_prefill")} />} />
            <Metric label={t("sv_requests")} color={C.running} value={has(now.running) ? t("sv_running_n", { n: now.running }) : "—"}
              sub={waiting !== null ? t("sv_waiting_n", { n: waiting }) : t("sv_no_data")}
              chart={<Sparkline series={[{ data: col("running"), color: C.running }]} max={niceMax(col("running").filter(has), 2)} label={t("sv_requests")} />} />
            <Metric label={t("sv_kv")} color={C.kv} value={has(now.kv_pct) ? `${num(now.kv_pct, 1, lang)} %` : "—"} sub={t("sv_kv_sub")}
              extra={<Bar value={(now.kv_pct || 0) / 100} />}
              chart={<Sparkline series={[{ data: col("kv_pct"), color: C.kv }]} max={100} label={t("sv_kv")} />} />
            {has(now.tokens_per_step) ? (
              <Metric label={t("sv_spec")} color={C.decode} value={`× ${num(now.tokens_per_step, 2, lang)}`}
                sub={<>{t("sv_spec_sub", { pct: has(now.acceptance) ? `${num(now.acceptance, 1, lang)} %` : "—" })}{now.spec_cumulative ? ` · ${t("sv_spec_all")}` : ""}</>} />
            ) : null}
            {ttft && (
              <Metric label={t("sv_ttft")} color={C.prefill} value={latency(ttft.p50, lang)}
                sub={`${t("sv_median")} · ${t("sv_p95", { value: latency(ttft.p95, lang) })} · ${ttft.cumulative ? t("sv_all_time") : t("sv_last_5")}`} />
            )}
            {itl && (
              <Metric label={t("sv_itl")} color={C.running} value={latency(itl.p50, lang)}
                sub={[`${t("sv_median")} · ${t("sv_p95", { value: latency(itl.p95, lang) })}`,
                  has(lat.stream_tps) && t("sv_per_stream", { n: num(lat.stream_tps, 1, lang) }),
                  itl.cumulative ? t("sv_all_time") : t("sv_last_5")].filter(Boolean).join(" · ")} />
            )}
          </div>
        </>
      )}

      <SpeedPanel recipe={e.recipe} />

      <Clients clients={e.clients} onChanged={onChanged} />

      <p className="help serve-totals">
        {tot.generation_tokens || tot.requests
          ? t("sv_totals_line", { when: clock(tot.since, lang), gen: compact(tot.generation_tokens, lang), prompt: compact(tot.prompt_tokens, lang), cached: compact(tot.cached_tokens, lang), req: t("sv_requests_total", { n: tot.requests, text: compact(tot.requests, lang) }) })
          : t("sv_totals_none")}
      </p>
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
      {chart && <div className="metric-chart">{chart}</div>}
      <div className="help metric-sub">{sub}</div>
    </div>
  );
}
