import React, { useCallback, useEffect, useRef, useState } from "react";
import RFB from "@novnc/novnc";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { uptime } from "../format.js";
import { Busy, Chip, CopyButton, ErrorBox, Icon, ICONS, Section, Spinner, useBusy, useLoad } from "../components/ui.jsx";
import { usePoll } from "../components/hooks.js";

const FIT_KEY = "prometheus.desktop.fit";
const readFit = () => { try { return localStorage.getItem(FIT_KEY) !== "0"; } catch { return true; } };
const saveFit = (v) => { try { localStorage.setItem(FIT_KEY, v ? "1" : "0"); } catch { /* private window */ } };

/** The Escritorio page: without a Spark id, the list; with one, that Spark's remote desktop (install, start, view, stop). */
export default function Escritorio({ param }) {
  return param ? <SparkDesktop id={param} /> : <Picker />;
}

function Picker() {
  const { t, ov } = useApp();
  const nodes = ov?.nodes || [];
  return (
    <div className="space-y-6">
      <header className="page-head">
        <span className="spark-ico spark-ico-lg"><Icon d={ICONS.computer} size={30} /></span>
        <div className="min-w-0 flex-1"><h1>{t("desk_title")}</h1><p className="help">{t("desk_pick")}</p></div>
      </header>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {nodes.map((n) => (
          <a key={n.id} className="panel row-item" href={`#/escritorio/${encodeURIComponent(n.id)}`}>
            <span className="dot" style={{ background: n.online ? "var(--ok)" : "var(--hoard-text-dim)" }} />
            <span className="min-w-0 flex-1 trunc font-semibold">{n.name}</span>
            <Icon d={ICONS.chevron} size={14} />
          </a>
        ))}
      </div>
    </div>
  );
}

function SparkDesktop({ id }) {
  const { t, lang, ov, confirm, notify, changed } = useApp();
  const [busy, run] = useBusy();
  const st = useLoad(() => api.call("desktop_status", { node: id }), [id]);
  const s = st.data && st.data.node === id ? st.data : null;
  const job = s?.install_job;
  const installing = job?.state === "running";
  usePoll(() => st.reload(), installing ? 1500 : 3000, true);
  const base = (ov?.nodes || []).find((n) => n.id === id);
  const name = s?.name || base?.name || id;

  const install = async () => {
    const list = (s?.missing || []).join(", ");
    const ok = await confirm({ title: t("desk_install_title", { name }), message: t("desk_install_msg", { name, n: (s?.missing || []).length, list }), confirmLabel: t("desk_install") });
    if (!ok) return;
    await run("install", async () => { await api.call("desktop_install", { node: id, confirm: true }); changed(); await st.reload(); });
  };
  const start = () => run("start", async () => {
    await api.call("desktop_start", { node: id });
    notify(t("desk_started", { name }));
    await st.reload();
  });
  const stop = async () => {
    const ok = await confirm({ title: t("desk_stop_title", { name }), message: t("desk_stop_msg"), confirmLabel: t("desk_stop") });
    if (!ok) return;
    await run("stop", async () => { await api.call("desktop_stop", { node: id }); notify(t("desk_stopped", { name })); await st.reload(); });
  };

  return (
    <div className="space-y-5">
      <div><a className="btn btn-sm btn-ghost" href={`#/spark/${encodeURIComponent(id)}`}><Icon d={ICONS.back} size={14} />{name}</a></div>
      <header className="page-head">
        <span className="spark-ico spark-ico-lg"><Icon d={ICONS.computer} size={30} /></span>
        <div className="min-w-0 flex-1">
          <h1 className="trunc">{t("desk_title")} · {name}</h1>
          <p className="help">{t("desk_loopback_note")}</p>
        </div>
        {s?.running && <Chip className="chip-ok">{t("desk_state_running")}</Chip>}
        {s && !s.running && s.installed && <Chip>{t("desk_state_stopped")}</Chip>}
        {s?.running && (
          <Busy className="btn btn-danger" busy={busy.stop} onClick={stop}><Icon d={ICONS.stop} size={14} />{t("desk_stop")}</Busy>
        )}
      </header>

      {st.error && !s && <ErrorBox error={st.error} onRetry={st.reload} />}
      {!s && !st.error && <div className="help flex items-center gap-2"><Spinner /> {t("loading")}</div>}
      {s && !s.online && <div className="banner banner-warn">{t("desk_offline", { name })}{s.error && <span className="mono block">{s.error}</span>}</div>}
      {s?.error && s.online && <div className="banner banner-warn">{s.error}</div>}

      {s?.online && !s.installed && !s.error && (
        <section className="panel space-y-3" aria-label={t("desk_not_installed", { name })}>
          <h2>{t("desk_not_installed", { name })}</h2>
          <p className="help">{t("desk_not_installed_help", { n: (s.missing || []).length })}</p>
          {(s.missing || []).length > 0 && <p className="help mono">{t("desk_missing", { list: s.missing.join(" ") })}</p>}
          {s.sudo_ok === false && <div className="banner banner-warn">{t("desk_no_sudo")}</div>}
          {installing ? (
            <div className="space-y-2" role="status">
              <div className="flex items-center gap-2"><Spinner /> {t("desk_installing")}</div>
              {job.log && <pre className="mono help desk-log">{job.log}</pre>}
            </div>
          ) : (
            <>
              {job?.state === "failed" && <div className="banner banner-danger" role="alert">{t("desk_install_failed")}{job.log && <pre className="mono desk-log">{job.log}</pre>}</div>}
              <Busy className="btn btn-primary" busy={busy.install} disabled={s.sudo_ok === false} onClick={install}><Icon d={ICONS.download} size={15} />{t("desk_install")}</Busy>
            </>
          )}
        </section>
      )}

      {s?.online && s.installed && !s.running && (
        <section className="panel space-y-3">
          <p>{t("desk_ready", { name })}</p>
          <p className="help">{t("desk_settings_note", { geometry: s.geometry, display: s.display })} {s.idle_min ? t("desk_idle_note", { n: s.idle_min }) : t("desk_idle_never")}</p>
          <Busy className="btn btn-primary" busy={busy.start} onClick={start}><Icon d={ICONS.play} size={14} />{t("desk_start")}</Busy>
        </section>
      )}

      {s?.running && (
        <>
          <p className="help flex flex-wrap gap-x-3">
            <span>{s.geometry} · {s.display}</span>
            {s.uptime_s != null && <span>{t("desk_up", { d: uptime(s.uptime_s, lang) })}</span>}
            <span>{t("desk_viewers", { n: s.viewers })}</span>
            <span>{s.idle_min ? t("desk_idle_note", { n: s.idle_min }) : t("desk_idle_never")}</span>
          </p>
          <Viewer key={id} node={id} />
        </>
      )}
    </div>
  );
}

const PHASE_CHIP = { idle: "", connecting: "chip-amber", connected: "chip-ok", closed: "", error: "chip-danger" };

function Viewer({ node }) {
  const { t } = useApp();
  const wrapRef = useRef(null);
  const boxRef = useRef(null);
  const rfbRef = useRef(null);
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState(null);
  const [fit, setFit] = useState(readFit);
  const [full, setFull] = useState(false);
  const [clip, setClip] = useState("");
  const [remoteClip, setRemoteClip] = useState("");
  const [showClip, setShowClip] = useState(false);

  const drop = useCallback(() => {
    const rfb = rfbRef.current;
    rfbRef.current = null;
    if (rfb) { try { rfb.disconnect(); } catch { /* already closed */ } }
  }, []);

  const connect = useCallback(async () => {
    drop();
    setPhase("connecting");
    setError(null);
    let session;
    try {
      session = await api.desktopSession(node);
    } catch (e) {
      setError(e);
      setPhase("error");
      return;
    }
    const box = boxRef.current;
    if (!box) return;
    box.replaceChildren();
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const rfb = new RFB(box, `${proto}://${window.location.host}${session.ws}`, { credentials: { password: session.password }, wsProtocols: ["binary"] });
    rfb.scaleViewport = readFit();
    rfb.clipViewport = false;
    rfb.background = "#0b0d0a";
    rfb.addEventListener("connect", () => { setPhase("connected"); setError(null); rfb.focus(); });
    rfb.addEventListener("disconnect", (e) => {
      if (rfbRef.current !== rfb) return;
      rfbRef.current = null;
      setPhase(e.detail?.clean ? "closed" : "error");
      if (!e.detail?.clean) setError((cur) => cur || { message: t("desk_conn_lost") });
    });
    rfb.addEventListener("securityfailure", () => setError({ message: t("desk_pw_failed") }));
    rfb.addEventListener("clipboard", (e) => setRemoteClip(e.detail?.text || ""));
    rfbRef.current = rfb;
  }, [node, drop, t]);

  useEffect(() => {
    connect();      // looking at this page of a running desktop means wanting to see it
    return drop;
  }, [node]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const rfb = rfbRef.current;
    if (rfb) rfb.scaleViewport = fit;
    saveFit(fit);
  }, [fit]);

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === wrapRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.().catch(() => {});
  };
  const live = phase === "connected";
  const sendClip = () => { if (rfbRef.current && clip) rfbRef.current.clipboardPasteFrom(clip); };

  return (
    <Section title={t("desk_screen")}>
      <div className={`vnc-wrap panel ${full ? "vnc-full" : ""}`} ref={wrapRef}>
        <div className="vnc-bar" role="toolbar" aria-label={t("desk_toolbar")}>
          <Chip className={PHASE_CHIP[phase]}>{phase === "connecting" && <span className="spinner spinner-xs" />}{t(`desk_state_${phase}`)}</Chip>
          {live || phase === "connecting"
            ? <button type="button" className="btn btn-sm" onClick={() => { drop(); setPhase("closed"); }}>{t("desk_disconnect")}</button>
            : <button type="button" className="btn btn-sm btn-primary" onClick={connect}>{t("desk_connect")}</button>}
          <button type="button" className="btn btn-sm" disabled={!live} onClick={() => rfbRef.current?.sendCtrlAltDel()}>{t("desk_cad")}</button>
          <button type="button" className="btn btn-sm" aria-pressed={fit} onClick={() => setFit((v) => !v)}>{fit ? t("desk_fit_on") : t("desk_fit_off")}</button>
          <button type="button" className="btn btn-sm" aria-pressed={showClip} onClick={() => setShowClip((v) => !v)}><Icon d={ICONS.paste} size={13} />{t("desk_clipboard")}</button>
          <button type="button" className="btn btn-sm" onClick={toggleFull}><Icon d={ICONS.external} size={13} />{full ? t("desk_exit_full") : t("desk_fullscreen")}</button>
        </div>
        {showClip && (
          <div className="vnc-clip">
            <label className="block">
              <span className="label">{t("desk_clip_send_label")}</span>
              <textarea className="field mono" rows={3} value={clip} onChange={(e) => setClip(e.target.value)} placeholder={t("desk_clip_ph")} />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn btn-sm btn-primary" disabled={!live || !clip} onClick={sendClip}>{t("desk_clip_send")}</button>
              {remoteClip && <><span className="help">{t("desk_clip_remote")}:</span><span className="mono trunc help vnc-remote">{remoteClip.slice(0, 120)}</span><CopyButton text={remoteClip} /></>}
            </div>
          </div>
        )}
        {error && <div className="banner banner-danger" role="alert">{error.message || String(error)}{error.hint && <span className="help block">{error.hint}</span>}</div>}
        <div className="vnc-screen" ref={boxRef} aria-label={t("desk_screen")} />
      </div>
    </Section>
  );
}
