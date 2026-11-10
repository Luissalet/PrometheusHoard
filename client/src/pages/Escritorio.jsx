import React, { useCallback, useEffect, useRef, useState } from "react";
import RFB from "@novnc/novnc";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { uptime } from "../format.js";
import { Busy, Chip, CopyButton, ErrorBox, Icon, ICONS, Section, Spinner, useBusy, useLoad } from "../components/ui.jsx";
import { usePoll } from "../components/hooks.js";

const FIT_KEY = "prometheus.desktop.fit";      // older choice (scale yes/no), read once to pick the first mode
const MODE_KEY = "prometheus.desktop.mode";
const AUTO_CLIP_KEY = "prometheus.desktop.autoclip";
const MODES = ["remote", "scale", "real"];
const readMode = () => {
  try {
    const m = localStorage.getItem(MODE_KEY);
    if (MODES.includes(m)) return m;
    if (localStorage.getItem(FIT_KEY) === "0") return "real";
  } catch { /* private window */ }
  return window.innerWidth >= 900 ? "remote" : "scale";
};
const saveMode = (m) => { try { localStorage.setItem(MODE_KEY, m); } catch { /* private window */ } };
const readAuto = () => { try { return localStorage.getItem(AUTO_CLIP_KEY) !== "0"; } catch { return true; } };
const saveAuto = (v) => { try { localStorage.setItem(AUTO_CLIP_KEY, v ? "1" : "0"); } catch { /* private window */ } };

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
          <Viewer key={id} node={id} name={name} desktopDir={s.desktop_dir} />
        </>
      )}
    </div>
  );
}

const PHASE_CHIP = { idle: "", connecting: "chip-amber", connected: "chip-ok", closed: "", error: "chip-danger" };
const XK_V = 0x0076;
const XK_INSERT = 0xff63;

async function digest(buf) {
  try {
    const h = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(h)).map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return `${buf.byteLength}`;   // no subtle crypto (plain http on another host): the size is a weak but harmless stand-in
  }
}

/** Any image blob as PNG bytes (the clipboard of a browser usually already gives PNG). */
async function asPng(blob) {
  if (blob.type === "image/png") return blob;
  const bmp = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  canvas.getContext("2d").drawImage(bmp, 0, 0);
  return await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

const b64ToBlob = (b64) => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/png" });
};

async function clipboardReadGranted() {
  try {
    const p = await navigator.permissions.query({ name: "clipboard-read" });
    return p.state === "granted";
  } catch {
    return false;
  }
}

function Viewer({ node, name, desktopDir }) {
  const { t, notify, toastError } = useApp();
  const wrapRef = useRef(null);
  const boxRef = useRef(null);
  const rfbRef = useRef(null);
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState(null);
  const [mode, setMode] = useState(readMode);
  const [full, setFull] = useState(false);
  const [size, setSize] = useState(null);
  const [resizable, setResizable] = useState(null);
  const [clip, setClip] = useState("");
  const [remoteClip, setRemoteClip] = useState("");
  const [remoteImage, setRemoteImage] = useState(null);     // {url, w, h}
  const [showClip, setShowClip] = useState(false);
  const [auto, setAuto] = useState(readAuto);
  const [clipNote, setClipNote] = useState("");
  const [drop, setDrop] = useState(null);                    // {phase: "over" | "upload", pct, n}
  const autoRef = useRef(auto);
  const sent = useRef({ text: null, image: null });          // what this page last put on the remote clipboard
  const seen = useRef({ image: null });                      // the last remote image copied to this PC
  const pulling = useRef(0);
  autoRef.current = auto;

  const note = useCallback((key, vars) => {
    setClipNote(t(key, vars));
    window.clearTimeout(note.timer);
    note.timer = window.setTimeout(() => setClipNote(""), 4000);
  }, [t]);   // eslint-disable-line react-hooks/exhaustive-deps

  const dropRfb = useCallback(() => {
    const rfb = rfbRef.current;
    rfbRef.current = null;
    if (rfb) { try { rfb.disconnect(); } catch { /* already closed */ } }
  }, []);

  const applyMode = (rfb, m) => {
    rfb.scaleViewport = m === "scale";
    rfb.clipViewport = false;
    rfb.resizeSession = m === "remote";
  };

  const connect = useCallback(async () => {
    dropRfb();
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
    applyMode(rfb, readMode());
    rfb.background = "#0b0d0a";
    rfb.addEventListener("connect", () => { setPhase("connected"); setError(null); rfb.focus(); });
    rfb.addEventListener("disconnect", (e) => {
      if (rfbRef.current !== rfb) return;
      rfbRef.current = null;
      setSize(null);
      setPhase(e.detail?.clean ? "closed" : "error");
      if (!e.detail?.clean) setError((cur) => cur || { message: t("desk_conn_lost") });
    });
    rfb.addEventListener("securityfailure", () => setError({ message: t("desk_pw_failed") }));
    rfb.addEventListener("clipboard", (e) => {
      const text = e.detail?.text || "";
      setRemoteClip(text);
      if (!text || text === sent.current.text) return;
      sent.current.text = text;
      if (autoRef.current && navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(() => note("desk_clip_to_pc"), () => {});
      }
    });
    rfbRef.current = rfb;
  }, [node, dropRfb, t, note]);

  useEffect(() => {
    connect();      // looking at this page of a running desktop means wanting to see it
    return dropRfb;
  }, [node]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const rfb = rfbRef.current;
    if (rfb) applyMode(rfb, mode);
    saveMode(mode);
  }, [mode]);

  useEffect(() => saveAuto(auto), [auto]);

  // the size of the remote screen (noVNC keeps it internally; read it while connected to show it)
  useEffect(() => {
    if (phase !== "connected") return undefined;
    const tick = () => {
      const rfb = rfbRef.current;
      if (!rfb) return;
      const w = rfb._fbWidth;
      const h = rfb._fbHeight;
      if (w && h) setSize((cur) => (cur && cur.w === w && cur.h === h ? cur : { w, h }));
      if (typeof rfb._supportsSetDesktopSize === "boolean") setResizable(rfb._supportsSetDesktopSize);
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === wrapRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // ---------------------------------------------------------------- clipboard: this PC -> desktop
  const sendText = useCallback((text) => {
    const rfb = rfbRef.current;
    if (!rfb || !text) return false;
    if (text !== sent.current.text) {
      rfb.clipboardPasteFrom(text);
      sent.current.text = text;
    }
    return true;
  }, []);

  const sendImage = useCallback(async (blob, { force = false } = {}) => {
    const png = await asPng(blob);
    const buf = await png.arrayBuffer();
    const hash = await digest(buf);
    if (!force && hash === sent.current.image) return true;
    await api.desktopClipboardImage(node, buf);
    sent.current.image = hash;
    seen.current.image = hash;
    sent.current.text = null;
    return true;
  }, [node]);

  /** Read this PC's clipboard and put it on the desktop's (image when there is only an image, else text). */
  const pushLocal = useCallback(async () => {
    if (!navigator.clipboard) return false;
    try {
      if (navigator.clipboard.read) {
        const items = await navigator.clipboard.read();
        for (const item of items) {
          const img = item.types.find((x) => x.startsWith("image/"));
          if (item.types.includes("text/plain")) {
            return sendText(await (await item.getType("text/plain")).text());
          }
          if (img) {
            await sendImage(await item.getType(img));
            note("desk_clip_img_sent");
            return true;
          }
        }
        return false;
      }
      return sendText(await navigator.clipboard.readText());
    } catch (e) {
      if (e && e.code) toastError(e);   // an API error (the Spark refused); a denied permission is silent
      return false;
    }
  }, [sendText, sendImage, note, toastError]);

  // Ctrl+V / Ctrl+Shift+V / Shift+Insert inside the screen: bring this PC's clipboard over first, then let the keys through.
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return undefined;
    const onKey = (e) => {
      const paste = (e.ctrlKey && !e.altKey && !e.metaKey && e.code === "KeyV") || (e.shiftKey && !e.ctrlKey && e.code === "Insert");
      if (!paste || !rfbRef.current || !autoRef.current) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      const insert = e.code === "Insert";
      pushLocal().finally(() => {
        const rfb = rfbRef.current;
        if (!rfb) return;
        if (insert) {
          rfb.sendKey(XK_INSERT, "Insert", true);
          rfb.sendKey(XK_INSERT, "Insert", false);
        } else {
          rfb.sendKey(XK_V, "KeyV", true);
          rfb.sendKey(XK_V, "KeyV", false);
        }
      });
    };
    box.addEventListener("keydown", onKey, true);
    return () => box.removeEventListener("keydown", onKey, true);
  }, [pushLocal]);

  // ---------------------------------------------------------------- clipboard: desktop -> this PC (images; text comes with the VNC stream)
  const pullRemote = useCallback(async ({ manual = false } = {}) => {
    if (pulling.current && !manual) return;
    pulling.current += 1;
    try {
      const c = await api.desktopClipboard(node);
      if (c.kind === "image" && c.png) {
        const blob = b64ToBlob(c.png);
        const hash = await digest(await blob.arrayBuffer());
        setRemoteImage((cur) => {
          if (cur) URL.revokeObjectURL(cur.url);
          return { url: URL.createObjectURL(blob), w: c.width, h: c.height, blob };
        });
        if (hash !== seen.current.image || manual) {
          seen.current.image = hash;
          if (navigator.clipboard?.write && window.ClipboardItem) {
            await navigator.clipboard.write([new window.ClipboardItem({ "image/png": blob })]);
            note("desk_clip_img_to_pc", { w: c.width, h: c.height });
          }
        }
      } else if (c.kind === "image" && c.too_big) {
        if (manual) notify(t("desk_clip_too_big"));
      } else if (c.kind === "text") {
        setRemoteClip(c.text);
        if (manual && navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(c.text);
          note("desk_clip_to_pc");
        }
      } else if (manual) {
        note("desk_clip_empty");
      }
    } catch (e) {
      if (manual) toastError(e);
    } finally {
      pulling.current -= 1;
    }
  }, [node, note, notify, t, toastError]);

  const lastLeave = useRef(0);
  const onLeave = () => {
    if (phase !== "connected" || !autoRef.current) return;
    const now = Date.now();
    if (now - lastLeave.current < 3000) return;
    lastLeave.current = now;
    pullRemote();
  };
  const onEnter = async () => {
    if (phase !== "connected" || !autoRef.current) return;
    if (await clipboardReadGranted()) pushLocal();      // so a right-click > Paste over there also gets it
  };

  useEffect(() => () => { setRemoteImage((cur) => { if (cur) URL.revokeObjectURL(cur.url); return null; }); }, []);

  // ---------------------------------------------------------------- files dropped on the screen go to the desktop folder of the Spark
  const onDragOver = (e) => {
    if (!Array.from(e.dataTransfer?.types || []).includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!drop) setDrop({ phase: "over" });
  };
  const onDragLeave = (e) => {
    if (drop?.phase === "over" && !wrapRef.current?.contains(e.relatedTarget)) setDrop(null);
  };
  const onDrop = async (e) => {
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    e.preventDefault();
    const folder = desktopDir || "~";
    setDrop({ phase: "upload", pct: 0, n: files.length });
    try {
      await api.upload({ node, folder, files, onProgress: (done, total) => setDrop({ phase: "upload", pct: total ? Math.round((100 * done) / total) : 0, n: files.length }) });
      notify(t("desk_drop_done", { n: files.length, name, folder }));
    } catch (err) {
      toastError(err);
    } finally {
      setDrop(null);
    }
  };

  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.().catch(() => {});
  };
  const live = phase === "connected";
  const pickImage = async (ev) => {
    const f = ev.target.files?.[0];
    ev.target.value = "";
    if (!f) return;
    try {
      await sendImage(f, { force: true });
      notify(t("desk_clip_img_sent"));
    } catch (e) {
      toastError(e);
    }
  };

  return (
    <Section title={t("desk_screen")}>
      <div className={`vnc-wrap panel ${full ? "vnc-full" : ""}`} ref={wrapRef} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
        <div className="vnc-bar" role="toolbar" aria-label={t("desk_toolbar")}>
          <Chip className={PHASE_CHIP[phase]}>{phase === "connecting" && <span className="spinner spinner-xs" />}{t(`desk_state_${phase}`)}</Chip>
          {live || phase === "connecting"
            ? <button type="button" className="btn btn-sm" onClick={() => { dropRfb(); setPhase("closed"); }}>{t("desk_disconnect")}</button>
            : <button type="button" className="btn btn-sm btn-primary" onClick={connect}>{t("desk_connect")}</button>}
          <button type="button" className="btn btn-sm" disabled={!live} onClick={() => rfbRef.current?.sendCtrlAltDel()}>{t("desk_cad")}</button>
          <div className="seg" role="group" aria-label={t("desk_mode")}>
            {MODES.map((m) => (
              <button key={m} type="button" aria-pressed={mode === m} title={t(`desk_mode_${m}_help`)} onClick={() => setMode(m)}>{t(`desk_mode_${m}`)}</button>
            ))}
          </div>
          <button type="button" className="btn btn-sm" aria-pressed={showClip} onClick={() => setShowClip((v) => !v)}><Icon d={ICONS.paste} size={13} />{t("desk_clipboard")}</button>
          <button type="button" className="btn btn-sm" onClick={toggleFull}><Icon d={ICONS.external} size={13} />{full ? t("desk_exit_full") : t("desk_fullscreen")}</button>
          {live && size && <span className="help num" title={t("desk_size_help")}>{t("desk_size", { w: size.w, h: size.h })}</span>}
          {clipNote && <span className="help vnc-note" role="status">{clipNote}</span>}
        </div>
        {live && mode === "remote" && resizable === false && <div className="banner banner-warn">{t("desk_no_resize")}</div>}
        {showClip && (
          <div className="vnc-clip">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              <span>{t("desk_clip_auto")}</span>
            </label>
            <p className="help">{t("desk_clip_auto_help")}</p>
            <label className="block">
              <span className="label">{t("desk_clip_send_label")}</span>
              <textarea className="field mono" rows={3} value={clip} onChange={(e) => setClip(e.target.value)} placeholder={t("desk_clip_ph")} />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn btn-sm btn-primary" disabled={!live || !clip} onClick={() => { sent.current.text = null; sendText(clip); note("desk_clip_sent"); }}>{t("desk_clip_send")}</button>
              <label className={`btn btn-sm ${live ? "" : "is-disabled"}`}>
                <Icon d={ICONS.image} size={13} />{t("desk_clip_send_image")}
                <input type="file" accept="image/*" className="sr-only" disabled={!live} onChange={pickImage} />
              </label>
              <button type="button" className="btn btn-sm" disabled={!live} onClick={() => pullRemote({ manual: true })}><Icon d={ICONS.download} size={13} />{t("desk_clip_pull")}</button>
            </div>
            {remoteClip && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="help">{t("desk_clip_remote")}:</span><span className="mono trunc help vnc-remote">{remoteClip.slice(0, 120)}</span><CopyButton text={remoteClip} />
              </div>
            )}
            {remoteImage && (
              <div className="flex flex-wrap items-center gap-3">
                <img src={remoteImage.url} alt={t("desk_clip_remote_image")} className="vnc-thumb" />
                <span className="help num">{t("desk_size", { w: remoteImage.w, h: remoteImage.h })}</span>
                <a className="btn btn-sm" href={remoteImage.url} download={`${name}-portapapeles.png`}><Icon d={ICONS.download} size={13} />{t("desk_clip_save_image")}</a>
              </div>
            )}
          </div>
        )}
        {error && <div className="banner banner-danger" role="alert">{error.message || String(error)}{error.hint && <span className="help block">{error.hint}</span>}</div>}
        <div className={`vnc-screen ${mode === "remote" ? "vnc-remote-size" : ""}`} ref={boxRef} aria-label={t("desk_screen")} onMouseLeave={onLeave} onMouseEnter={onEnter} />
        {drop && (
          <div className="vnc-drop" role="status">
            {drop.phase === "over" ? t("desk_drop_here", { folder: desktopDir || "~", name }) : t("desk_drop_uploading", { n: drop.n, pct: drop.pct })}
          </div>
        )}
      </div>
      <p className="help">{t("desk_tips")}</p>
    </Section>
  );
}
