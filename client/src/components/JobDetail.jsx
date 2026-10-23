import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { bytes, locale, pct } from "../format.js";
import { CopyButton, ErrorBox, Spinner } from "./ui.jsx";

export default function JobDetail({ jobId, onClose }) {
  const { t, lang } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  async function load() {
    const current = ++request.current;
    setData(null); setError(null); setBusy(true);
    try {
      const result = await api.call("job_get", { job: jobId });
      if (current === request.current) setData(result);
    } catch (cause) {
      if (current === request.current) setError(cause);
    } finally {
      if (current === request.current) setBusy(false);
    }
  }
  useEffect(() => { load(); return () => { request.current++; }; }, [jobId]);
  const date = (value) => value == null ? t("job_detail_missing") : new Date(value * 1000).toLocaleString(locale(lang));
  const row = (label, value) => <div key={label}><dt className="help">{label}</dt><dd style={{ overflowWrap: "anywhere" }}>{value}</dd></div>;
  return <section id={`job-detail-${jobId}`} className="mt-4 min-w-0 space-y-3 border-t pt-4" style={{ borderColor: "var(--hoard-border)" }} aria-label={t("job_detail")}>
    <div className="flex flex-wrap items-center gap-2">
      <h3 className="min-w-0 flex-1 font-semibold">{t("job_detail")}</h3>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={load}>{t("refresh")}</button>
      <button type="button" className="btn btn-sm" onClick={onClose}>{t("close")}</button>
    </div>
    <p className="help" style={{ overflowWrap: "anywhere" }}>{t("job_detail_id")}: {jobId}</p>
    {busy && <div role="status"><Spinner /> {t("loading")}</div>}
    <ErrorBox error={error} onRetry={load} />
    {data && data.id === jobId && <>
      <p className="font-semibold" style={{ overflowWrap: "anywhere" }}>{data.title}</p>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        {row(t("job_detail_node"), data.node ?? t("job_detail_missing"))}
        {row(t("job_detail_kind"), t(`jobkind_${data.kind}`))}
        {row(t("job_detail_state"), t(`job_${data.state}`))}
        {row(t("job_detail_rc"), data.rc ?? t("job_detail_missing"))}
        {row(t("job_detail_created"), date(data.created))}
        {row(t("job_detail_finished"), date(data.finished))}
        {data.progress != null && row(t("job_detail_progress"), pct(data.progress * 100, lang))}
        {data.watched_bytes != null && row(t("job_detail_bytes"), bytes(data.watched_bytes, lang))}
        {data.expected_bytes != null && row(t("job_detail_expected"), bytes(data.expected_bytes, lang))}
      </dl>
      <p className="help">{t("job_detail_dates_hint")}</p>
      <div className="flex flex-wrap items-center gap-2"><h4 className="flex-1 font-semibold">{t("job_detail_log")}</h4>{data.log && <CopyButton text={data.log} label={t("job_detail_copy_log")} />}</div>
      {data.log ? <pre className="pre" tabIndex={0} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{data.log}</pre> : <p className="help">{t("job_detail_log_empty")}</p>}
      {(data.watch || (data.meta && Object.keys(data.meta).length > 0)) && <details>
        <summary className="help">{t("job_detail_metadata")}</summary>
        <pre className="pre mt-2" tabIndex={0} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ ...(data.watch ? { watch: data.watch } : {}), ...(data.meta && Object.keys(data.meta).length ? { meta: data.meta } : {}) }, null, 2)}</pre>
      </details>}
    </>}
  </section>;
}
