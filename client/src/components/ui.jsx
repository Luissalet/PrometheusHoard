import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../context.js";
import { clock, rel } from "../format.js";

export function Icon({ d, size = 17, color, fill, className, strokeWidth = 1.8 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill || "none"} stroke={color || "currentColor"} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d={d} />
    </svg>
  );
}

export const ICONS = {
  // navigation
  computer: "M3 4h18v12H3zM8 20h8M12 16v4",
  folder: "M3 6a1 1 0 011-1h5l2 2h9a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1z",
  models: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-1-3-2 .3-1.4-1.4.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L6.8 7.3 5 7 4 10l2 1v2l-2 1 1 3 2-.3 1.4 1.4-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.4-1.4 2 .3 1-3-2-1z",
  // files
  file: "M6 3h8l4 4v14H6zM14 3v4h4",
  text: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 15h6M9 18h4",
  code: "M6 3h8l4 4v14H6zM14 3v4h4M10 12l-2 2.5 2 2.5M14 12l2 2.5-2 2.5",
  image: "M4 5h16v14H4zM4 16l4.5-4.5 3.5 3.5 2.5-2.5L20 18M15.5 9.5h.01",
  video: "M4 6h12v12H4zM16 10l5-3v10l-5-3",
  audio: "M9 18V6l11-2v12M9 18a3 3 0 11-6 0 3 3 0 016 0zM20 16a3 3 0 11-6 0 3 3 0 016 0z",
  archive: "M4 4h16v4H4zM5 8v12h14V8M10 12h4",
  weights: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  pdf: "M6 3h8l4 4v14H6zM14 3v4h4M8.5 16v-4h1.5a1.2 1.2 0 010 2.4H8.5M13 12v4h1a2 2 0 000-4z",
  drive: "M3 13h18v6H3zM5 13l2.5-8h9L19 13M17 16h.01M14 16h.01",
  home: "M4 11l8-7 8 7M6 9.5V20h4.5v-6h3v6H18V9.5",
  star: "M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
  recipes: "M5 4h11l3 3v13H5zM9 9h6M9 13h6M9 17h3",
  warning: "M12 3l10 18H2zM12 10v5M12 18h.01",
  // actions
  plus: "M12 5v14M5 12h14",
  refresh: "M3 12a9 9 0 0115-6.7L21 8M21 3v5h-5M21 12a9 9 0 01-15 6.7L3 16M3 21v-5h5",
  copy: "M9 9h11v11H9zM5 15V4h11",
  cut: "M6 7a3 3 0 100-.01M6 20a3 3 0 100-.01M8.5 8.5L20 19M8.5 15.5L20 5",
  paste: "M9 4h6v3H9zM15 5h3v16H6V5h3M9 12h6M9 16h4",
  pencil: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
  check: "M5 12l5 5 9-10",
  close: "M6 6l12 12M18 6L6 18",
  chevron: "M9 6l6 6-6 6",
  chevronDown: "M6 9l6 6 6-6",
  back: "M15 6l-6 6 6 6",
  arrowLeft: "M20 12H4M10 6l-6 6 6 6",
  arrowRight: "M4 12h16M14 6l6 6-6 6",
  arrowUp: "M12 20V4M6 10l6-6 6 6",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v4h16v-4",
  download: "M12 4v12M7 11l5 5 5-5M4 16v4h16v-4",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-5-5",
  stop: "M6 6h12v12H6z",
  play: "M7 4l13 8-13 8z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  info: "M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v6M12 7.5h.01",
  send: "M4 12l16-8-6 16-2.5-6.5zM11.5 13.5L20 4",
  list: "M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z",
  eyeOff: "M3 3l18 18M10.6 6.1A10 10 0 0112 6c6 0 10 6 10 6a17 17 0 01-3.2 3.8M6.6 6.6C3.8 8.3 2 12 2 12s4 7 10 7a9.6 9.6 0 004.4-1M9.9 9.9a3 3 0 004.2 4.2",
  pane: "M3 4h18v16H3zM15 4v16",
  restore: "M4 9h11a5 5 0 010 10H8M4 9l4-4M4 9l4 4",
  link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  terminal: "M4 5h16v14H4zM7 10l3 2.5L7 15M12 15h5",
  save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6",
  folderPlus: "M3 6a1 1 0 011-1h5l2 2h9a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1zM12 10v6M9 13h6",
  filePlus: "M6 3h8l4 4v14H6zM14 3v4h4M12 11v6M9 14h6",
  selectAll: "M4 4h16v16H4zM8 12l3 3 5-6",
  // hardware
  power: "M12 3v9M7 6.3a7.5 7.5 0 1010 0",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 017 0v3",
  moon: "M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z",
  restart: "M3 12a9 9 0 1 0 3-6.7M3 4v5h5",
  chip: "M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4",
  cpu: "M6 6h12v12H6zM9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
  memory: "M3 7h18v10H3zM7 7v10M11 7v10M15 7v10M3 20h2M19 20h2M5 17v3M19 17v3",
  network: "M12 3v6M5 21v-4h14v4M12 9a3 3 0 100 6 3 3 0 000-6zM12 15v2M5 17v-2M19 17v-2",
  fabric: "M5 6h14M5 18h14M8 6v12M16 6v12M3 12h18",
  thermo: "M10 14V5a2 2 0 014 0v9a4 4 0 11-4 0zM12 9v8",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  clock: "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2",
  box: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  jobs: "M4 6h10M4 12h16M4 18h7M18 4v4M16 6h4",
  warn: "M12 3l10 18H2zM12 10v5M12 18h.01",
  cloud: "M7 18a5 5 0 01-.9-9.9A6 6 0 0117.7 9 4.5 4.5 0 0117.5 18zM12 11v6M9.5 14.5L12 17l2.5-2.5",
  activity: "M3 12h4l3-8 4 16 3-8h4",
};

export function KindIcon({ kind, size = 18 }) {
  const map = { folder: "folder", weights: "weights", image: "image", video: "video", audio: "audio", archive: "archive", code: "code", text: "text", pdf: "pdf" };
  const d = ICONS[map[kind] || "file"];
  return <span className={`kind-icon kind-${kind || "file"}`}><Icon d={d} size={size} fill={kind === "folder" ? "currentColor" : undefined} strokeWidth={kind === "folder" ? 1.2 : 1.8} /></span>;
}

export function Spinner({ label }) {
  return <span className="spinner" role="status" aria-label={label || "…"} />;
}

export function Empty({ icon, title, children, action }) {
  return (
    <div className="empty">
      {icon && <Icon d={icon} size={26} />}
      {title && <b>{title}</b>}
      {children && <div className="help">{children}</div>}
      {action}
    </div>
  );
}

export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`block ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="help mt-1 block">{hint}</span>}
    </label>
  );
}

export function Switch({ checked, onChange, disabled, label }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={!!checked} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function Chip({ children, className = "" }) {
  return <span className={`chip ${className}`}>{children}</span>;
}

export function Section({ title, count, actions, children, id, sub }) {
  return (
    <section className="space-y-2.5" aria-labelledby={id} id={id ? `${id}-section` : undefined}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={id}>{title}</h2>
        {count !== undefined && count !== null && <span className="chip">{count}</span>}
        {sub && <span className="help">{sub}</span>}
        <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>
      </div>
      {children}
    </section>
  );
}

export function Busy({ busy, children, className = "btn", ...props }) {
  return (
    <button type="button" className={className} {...props} disabled={busy || props.disabled}>
      {busy && <span className="spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}

export function ErrorBox({ error, onRetry }) {
  const { t, errorText } = useApp();
  if (!error) return null;
  return (
    <div className="banner banner-danger" role="alert">
      {errorText(error)}
      {onRetry && <> <button type="button" className="btn-link" onClick={onRetry}>{t("retry")}</button></>}
      {error.hint && <span className="help block mt-1">{t("hint")}: {error.hint}</span>}
    </div>
  );
}

/** A relative date; the absolute date is shown beside it on wide rows by the caller when it matters. */
export function Rel({ ts }) {
  const { lang } = useApp();
  if (!ts) return <span className="help">—</span>;
  return <time dateTime={new Date(ts * 1000).toISOString()}>{rel(ts, lang)}</time>;
}

export function DateCell({ ts }) {
  const { lang } = useApp();
  if (!ts) return <span className="help">—</span>;
  return <time dateTime={new Date(ts * 1000).toISOString()}>{clock(ts, lang)}</time>;
}

/** A Windows-style usage bar. `value` 0..1. Turns amber past 75 % and red past 90 % when `alarm`. */
export function Bar({ value, alarm = true, className = "", thin }) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  const tone = alarm && v >= 0.9 ? "bar-bad" : alarm && v >= 0.75 ? "bar-warn" : "";
  return (
    <div className={`bar ${tone} ${thin ? "bar-thin" : ""} ${className}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)}>
      <span style={{ width: `${v * 100}%` }} />
    </div>
  );
}

export function CopyButton({ text, label }) {
  const { t, notify } = useApp();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      notify(t("copied"));
    } catch {
      notify(t("copy_failed"), "error");
    }
  };
  return (
    <button type="button" className="btn btn-sm btn-icon" onClick={copy} aria-label={label || t("copy")}>
      <Icon d={ICONS.copy} size={13} />
    </button>
  );
}

// ------------------------------------------------------------------ dialogs
export function Modal({ title, onClose, children, wide, footer }) {
  const ref = useRef(null);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", onKey);
    const first = ref.current?.querySelector("input:not([type=checkbox]), select, textarea");
    (first || ref.current?.querySelector("button"))?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} ref={ref} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 className="min-w-0 flex-1 trunc">{title}</h2>
          <button type="button" className="btn btn-sm btn-icon btn-ghost" onClick={onClose} aria-label="×"><Icon d={ICONS.close} size={14} /></button>
        </div>
        <div className="modal-body space-y-3">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children, actions }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="modal-backdrop" style={{ zIndex: 64 }} onMouseDown={onClose} aria-hidden="true" />
      <div className="drawer space-y-3" role="dialog" aria-modal="true" aria-label={title}>
        <div className="flex items-start gap-2">
          <h2 className="min-w-0 flex-1" style={{ overflowWrap: "anywhere" }}>{title}</h2>
          {actions}
          <button type="button" className="btn btn-sm btn-icon" onClick={onClose} aria-label="×"><Icon d={ICONS.close} size={14} /></button>
        </div>
        {children}
      </div>
    </>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div role="tablist" className="tabs">
      {tabs.map((tab) => (
        <button key={tab.id} type="button" role="tab" className="tab" aria-selected={tab.id === value} onClick={() => onChange(tab.id)}>{tab.label}</button>
      ))}
    </div>
  );
}

export function ConfirmDialog({ request, onClose }) {
  const { t } = useApp();
  const cancelRef = useRef(null);
  useEffect(() => {
    if (!request) return undefined;
    cancelRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request, onClose]);
  if (!request) return null;
  return (
    <div className="modal-backdrop" style={{ zIndex: 90 }} onClick={() => onClose(false)}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h2 id="confirm-title" className="flex-1">{request.title || t("confirm")}</h2></div>
        <div className="modal-body space-y-2">
          <p>{request.message}</p>
          {request.detail && <p className="help">{request.detail}</p>}
        </div>
        <div className="modal-foot">
          <button type="button" ref={cancelRef} className="btn" onClick={() => onClose(false)}>{t("cancel")}</button>
          <button type="button" className={`btn ${request.danger === false ? "btn-primary" : "btn-danger-solid"}`} onClick={() => onClose(true)}>{request.confirmLabel || t("delete")}</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ menus
/**
 * A popup menu (context menu or dropdown). `at` is {x, y} in viewport pixels; `items` are
 * {label, icon, shortcut, onClick, danger, disabled} or "-" for a separator.
 */
export function Menu({ at, items, onClose, label, anchorRef }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: at.x, top: at.y, ready: false });
  const [active, setActiveState] = useState(-1);
  const activeRef = useRef(-1);
  const setActive = (i) => { activeRef.current = i; setActiveState(i); };
  const real = items.filter(Boolean);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = at.x;
    let top = at.y;
    if (at.alignRight) left = at.x - r.width;
    if (left + r.width > vw - 6) left = Math.max(6, vw - r.width - 6);
    if (left < 6) left = 6;
    if (top + r.height > vh - 6) top = Math.max(6, (at.above ?? at.y) - r.height);
    setPos({ left, top, ready: true });
  }, [at]);
  useEffect(() => {
    const onDown = (e) => {
      if (anchorRef?.current && anchorRef.current.contains(e.target)) return;
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const focusable = () => real.map((it, i) => (it !== "-" && !it.disabled ? i : -1)).filter((i) => i >= 0);
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
      const idx = focusable();
      if (!idx.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        const at2 = idx.indexOf(activeRef.current);
        setActive(e.key === "ArrowDown" ? idx[(at2 + 1) % idx.length] : idx[(at2 - 1 + idx.length) % idx.length]);
      } else if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        const it = real[activeRef.current];
        if (it && it !== "-" && !it.disabled) { onClose(); it.onClick?.(); }
      }
    };
    const onScroll = () => onClose();
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("touchstart", onDown, true);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("touchstart", onDown, true);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [onClose, real, anchorRef]);
  return createPortal(
    <div ref={ref} className="menu" role="menu" aria-label={label} style={{ left: pos.left, top: pos.top, visibility: pos.ready ? "visible" : "hidden" }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); }} onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
      {real.map((it, i) => (it === "-" ? <div key={`s${i}`} className="menu-sep" role="separator" /> : (
        <button key={it.label} type="button" role="menuitem" className={`menu-item ${it.danger ? "menu-danger" : ""} ${active === i ? "menu-active" : ""}`} disabled={it.disabled}
          onMouseEnter={() => setActive(i)} onClick={() => { onClose(); it.onClick?.(); }}>
          <span className="menu-ico">{it.icon ? <Icon d={it.icon} size={15} /> : it.check !== undefined ? (it.check ? <Icon d={ICONS.check} size={15} /> : null) : null}</span>
          <span className="flex-1 text-left">{it.label}</span>
          {it.shortcut && <span className="menu-key">{it.shortcut}</span>}
        </button>
      )))}
    </div>,
    document.body,
  );
}

/** A button that opens a Menu under itself. */
export function MenuButton({ items, children, className = "btn", label, align = "left", disabled }) {
  const [at, setAt] = useState(null);
  const ref = useRef(null);
  const open = () => {
    const r = ref.current.getBoundingClientRect();
    setAt(align === "right" ? { x: r.right, y: r.bottom + 4, alignRight: true, above: r.top - 4 } : { x: r.left, y: r.bottom + 4, above: r.top - 4 });
  };
  return (
    <>
      <button type="button" ref={ref} className={className} aria-haspopup="menu" aria-expanded={!!at} aria-label={label} disabled={disabled}
        onClick={(e) => { e.stopPropagation(); if (at) setAt(null); else open(); }}>
        {children}
      </button>
      {at && <Menu at={at} items={typeof items === "function" ? items() : items} onClose={() => setAt(null)} label={label} anchorRef={ref} />}
    </>
  );
}

// Loads something on mount and when `deps` change. `reload()` runs it again without flashing the placeholder.
export function useLoad(fn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const run = useRef(fn);
  run.current = fn;
  const seq = useRef(0);
  const load = useMemo(() => async () => {
    const mine = ++seq.current;
    try {
      const data = await run.current();
      if (mine === seq.current) setState({ data, error: null, loading: false });
      return data;
    } catch (error) {
      if (mine === seq.current) setState((s) => ({ data: s.data, error, loading: false }));
      return undefined;
    }
  }, []);
  useEffect(() => {
    setState((s) => ({ ...s, loading: true }));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { ...state, reload: load, setData: (data) => setState((s) => ({ ...s, data })) };
}

// Busy flags keyed by name; a failing action shows the backend's error and hint in a toast.
export function useBusy() {
  const { toastError } = useApp();
  const [busy, setBusy] = useState({});
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const run = async (key, fn, { silent } = {}) => {
    setBusy((b) => ({ ...b, [key]: true }));
    try {
      return await fn();
    } catch (error) {
      if (silent) throw error;
      toastError(error);
      return undefined;
    } finally {
      if (mounted.current) setBusy((b) => ({ ...b, [key]: false }));
    }
  };
  return [busy, run];
}

/** Narrow viewport (phone) flag that follows resizes. */
export function useNarrow(px = 768) {
  const q = `(max-width: ${px - 1}px)`;
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setNarrow(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [q]);
  return narrow;
}

/** A value kept in localStorage (per-viewer convenience only). */
export function useStored(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : JSON.parse(raw);
    } catch {
      return initial;
    }
  });
  const set = (v) => {
    setValue((cur) => {
      const next = typeof v === "function" ? v(cur) : v;
      try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* not critical */ }
      return next;
    });
  };
  return [value, set];
}
