import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { clock, latency, num, rel } from "../format.js";
import { Bar, Busy, Chip, ErrorBox, Icon, ICONS, useBusy, useLoad } from "./ui.jsx";
import { usePoll } from "./hooks.js";

const has = (v) => v !== null && v !== undefined;

/** The «Velocidad» and «Capacidad» cards of one model server: what it was measured at (a table the person starts) and what it can hold (read live). */
export default function SpeedPanel({ recipe }) {
  const load = useLoad(() => api.call("speed_card", { recipe, history: 6 }), [recipe]);
  const e = load.data?.endpoints?.[0];
  const running = !!e?.job;
  // 2.5 s while a measurement runs, otherwise now and then (the capacity is a live read, but it hardly moves)
  usePoll(() => load.reload(), running ? 2500 : 20000, true);
  if (!e) return load.error ? <ErrorBox error={load.error} onRetry={load.reload} /> : null;
  return (
    <div className="sc-pair">
      <SpeedCard e={e} recipe={recipe} onChanged={load.reload} />
      <CapacityCard cap={e.capacity} />
    </div>
  );
}

function SpeedCard({ e, recipe, onChanged }) {
  const { t, lang, confirm, notify } = useApp();
  const [busy, run] = useBusy();
  const [older, setOlder] = useState(false);
  const job = e.job;
  const latest = e.latest;
  const start = () => run("start", async () => {
    const ok = await confirm({ title: t("sc_confirm_title", { title: e.title }), message: t("sc_confirm_msg"), confirmLabel: t("sc_measure"), danger: false });
    if (!ok) return;
    await api.call("speed_card_run", { recipe, confirm: true });
    notify(t("sc_started"));
    await onChanged();
  });
  const cancel = () => run("cancel", async () => {
    await api.call("job_cancel", { job: job.id });
    await onChanged();
  });
  const lastLine = job ? (job.log || "").split("\n").filter(Boolean).pop() : "";
  const s = latest?.settings || {};

  return (
    <section className="sc-card" aria-label={t("sc_speed_title")}>
      <div className="sc-head">
        <h3>{t("sc_speed_title")}</h3>
        {job ? (
          <Busy className="btn btn-sm" busy={busy.cancel} onClick={cancel}>{t("cancel")}</Busy>
        ) : (
          <Busy className="btn btn-sm btn-primary" busy={busy.start} onClick={start}><Icon d={ICONS.activity} size={14} />{t("sc_measure")}</Busy>
        )}
      </div>

      {job && (
        <div className="sc-progress" role="status">
          <Bar value={job.progress ?? 0} alarm={false} thin />
          <div className="help"><span className="spinner spinner-xs" /> {t("sc_measuring", { pct: `${Math.round((job.progress || 0) * 100)} %` })}{lastLine ? ` · ${lastLine}` : ""}</div>
        </div>
      )}

      {!latest ? (
        <p className="help">{job ? t("sc_first_running") : t("sc_none")}</p>
      ) : (
        <>
          <div className="sc-scroll">
            <table className="tbl sc-table">
              <thead>
                <tr>
                  <th className="r">{t("sc_col_n")}</th>
                  <th className="r">{t("sc_col_agg")}</th>
                  <th className="r">{t("sc_col_stream")}</th>
                  <th className="r">{t("sc_col_ttft")}</th>
                </tr>
              </thead>
              <tbody>
                {latest.levels.map((lv) => (
                  <tr key={lv.n}>
                    <td className="r num"><b>{lv.n}</b></td>
                    <td className="r num">{has(lv.agg_tps) ? num(lv.agg_tps, 1, lang) : "—"}</td>
                    <td className="r num">{has(lv.stream_tps) ? num(lv.stream_tps, 1, lang) : "—"}</td>
                    <td className="r num">{has(lv.ttft_p50) ? `${latency(lv.ttft_p50, lang)} / ${latency(lv.ttft_p95, lang)}` : "—"}
                      {lv.errors > 0 && <span title={(lv.error_samples || []).join(" · ")}> <Chip className="chip-danger">{t("sc_errors_n", { n: lv.errors, total: lv.requests })}</Chip></span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="help sc-foot">
            {t("sc_measured", { when: clock(latest.t, lang), ago: rel(latest.t, lang) })}
            {" · "}{t("sc_settings", { tokens: s.max_tokens, rounds: s.rounds })}
            {latest.levels.some((lv) => lv.tokens_source === "deltas") ? ` · ${t("sc_deltas")}` : ""}
          </p>
          {(e.history || []).length > 0 && (
            <>
              <button type="button" className="btn-link sc-more" aria-expanded={older} onClick={() => setOlder(!older)}>
                {older ? t("sc_hide_older") : t("sc_show_older", { n: e.history.length })}
              </button>
              {older && (
                <ul className="sc-older">
                  {e.history.map((c) => (
                    <li key={c.id}>
                      <span className="help">{clock(c.t, lang)}</span>
                      <span className="num">{c.levels.map((lv) => `${lv.n}: ${has(lv.agg_tps) ? num(lv.agg_tps, 0, lang) : "—"}`).join(" · ")} {t("sc_tps")}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function CapacityCard({ cap }) {
  const { t, lang } = useApp();
  if (!cap) return null;
  const v = cap.verified;
  return (
    <section className="sc-card" aria-label={t("sc_cap_title")}>
      <div className="sc-head"><h3>{t("sc_cap_title")}</h3></div>
      <dl className="sc-facts">
        <div>
          <dt>{t("sc_cap_context")}</dt>
          <dd className="num">{has(cap.max_model_len) ? `${num(cap.max_model_len, 0, lang)} ${t("sc_tokens")}` : "—"}</dd>
        </div>
        <div>
          <dt>{t("sc_cap_kv")}</dt>
          <dd className="num">{has(cap.kv_pool_tokens) ? `${num(cap.kv_pool_tokens, 0, lang)} ${t("sc_tokens")}` : "—"}</dd>
          <dd className="help">
            {has(cap.kv_pool_tokens)
              ? [t("sc_cap_blocks", { blocks: num(cap.kv_blocks, 0, lang), size: cap.block_size }),
                has(cap.concurrency_at_max_len) && t("sc_cap_conc", { n: num(cap.concurrency_at_max_len, 2, lang) }),
                cap.cache_dtype && t("sc_cap_dtype", { dtype: cap.cache_dtype })].filter(Boolean).join(" · ")
              : t("sc_cap_no_kv")}
          </dd>
        </div>
        <div>
          <dt>{t("sc_cap_verified")}</dt>
          <dd>
            {v ? (
              <>
                {v.context_verified === true && <Chip className="chip-ok"><Icon d={ICONS.check} size={11} />{t("sc_cap_verified_yes")}</Chip>}
                {v.context_verified === false && <Chip className="chip-amber">{t("sc_cap_verified_no")}</Chip>}
                {v.context_verified == null && <Chip>{t("sc_cap_verified_unknown")}</Chip>}
              </>
            ) : <Chip>{t("sc_cap_verified_none")}</Chip>}
          </dd>
          {v && (has(v.prompt_tokens) || has(v.needle_pass)) && (
            <dd className="help">
              {[has(v.prompt_tokens) && t("sc_cap_read", { n: num(v.prompt_tokens, 0, lang) }),
                v.needle_pass === true && t("sc_cap_needle_ok"), v.needle_pass === false && t("sc_cap_needle_bad")].filter(Boolean).join(" · ")}
            </dd>
          )}
        </div>
      </dl>
      {(cap.errors || []).length > 0 && !has(cap.kv_pool_tokens) && <p className="help sc-err" title={cap.errors.join(" · ")}>{t("sc_cap_partial")}</p>}
    </section>
  );
}
