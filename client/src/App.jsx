import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { initialLang, makeT, saveLang } from "./i18n.js";
import { AppContext } from "./context.js";
import { createSerialPoll } from "./polling.js";
import { ConfirmDialog, Icon, ICONS } from "./components/ui.jsx";
import Equipo from "./pages/Equipo.jsx";
import Spark from "./pages/Spark.jsx";
import Sirviendo from "./pages/Sirviendo.jsx";
import Archivos from "./pages/Archivos.jsx";
import Modelos from "./pages/Modelos.jsx";
import Ajustes from "./pages/Ajustes.jsx";

export { useApp } from "./context.js";

const PAGES = [
  { path: "", key: "nav_cluster", icon: ICONS.computer, component: Equipo, fast: true },
  { path: "spark", key: "nav_cluster", icon: ICONS.computer, component: Spark, hidden: true, parent: "", fast: true },
  { path: "sirviendo", key: "nav_serving", icon: ICONS.activity, component: Sirviendo, fast: true },
  { path: "archivos", key: "nav_files", icon: ICONS.folder, component: Archivos },
  { path: "modelos", key: "nav_models", icon: ICONS.models, component: Modelos, badge: "work" },
  { path: "ajustes", key: "nav_settings", icon: ICONS.settings, component: Ajustes },
];

function readHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const q = raw.indexOf("?");
  const path = q >= 0 ? raw.slice(0, q) : raw;
  const query = q >= 0 ? raw.slice(q + 1) : "";
  const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { page: parts[0] || "", param: parts[1] || null, query: new URLSearchParams(query), hash: window.location.hash };
}

function useHashRoute() {
  const [route, setRoute] = useState(readHash);
  useEffect(() => {
    const onChange = () => {
      const next = readHash();
      setRoute((cur) => {
        if (cur.page !== next.page) window.scrollTo(0, 0);
        return next;
      });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function Toasts({ toasts, onClose }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => <ToastItem key={toast.id} toast={toast} onClose={onClose} />)}
    </div>
  );
}

function ToastItem({ toast, onClose }) {
  useEffect(() => {
    const timer = setTimeout(() => onClose(toast.id), toast.kind === "error" ? 10000 : 4500);
    return () => clearTimeout(timer);
  }, [toast, onClose]);
  return (
    <div className={`toast ${toast.kind === "error" ? "toast-error" : "toast-ok"}`} role={toast.kind === "error" ? "alert" : "status"}>
      <Icon d={toast.kind === "error" ? ICONS.warn : ICONS.check} size={15} />
      <div className="min-w-0 flex-1">
        <div>{toast.message}</div>
        {toast.hint && <div className="help">{toast.hint}</div>}
        {toast.action && <a className="btn-link" href={toast.action.href} onClick={() => onClose(toast.id)}>{toast.action.label}</a>}
      </div>
      <button type="button" className="btn-icon btn-ghost toast-x" onClick={() => onClose(toast.id)} aria-label="×"><Icon d={ICONS.close} size={13} /></button>
    </div>
  );
}

function ClusterLight({ ov, t }) {
  if (!ov) return null;
  const c = ov.cluster || {};
  const color = !c.total ? "var(--muted)" : c.online === c.total ? "var(--ok)" : c.online ? "var(--warn)" : "var(--danger)";
  return (
    <div className="flex items-center gap-2 text-[12.5px]" aria-live="polite">
      <span className="dot" style={{ background: color }} />
      <span>{t("sparks_online_of", { n: c.online ?? 0, total: c.total ?? 0 })}</span>
    </div>
  );
}

export default function App() {
  const route = useHashRoute();
  const [lang, setLang] = useState(initialLang);
  const t = useMemo(() => makeT(lang), [lang]);
  const [health, setHealth] = useState(null);
  const [ov, setOv] = useState(null);
  const [ovError, setOvError] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [confirmReq, setConfirmReq] = useState(null);
  const [version, setVersion] = useState(0);
  const confirmResolve = useRef(null);

  useEffect(() => { document.documentElement.lang = lang; }, [lang]);

  const refreshOv = useCallback(async () => {
    try {
      setOv(await api.overview(false));
      setOvError(null);
    } catch (e) {
      setOvError(e);
    }
  }, []);
  const changed = useCallback(() => { setVersion((v) => v + 1); refreshOv(); }, [refreshOv]);

  const page = PAGES.find((p) => p.path === route.page) || PAGES[0];
  // Poll every 2 s on the cluster pages, every 3 s while something is starting or copying, else every 10 s; only while the tab is visible.
  const busy = !!(ov && ((ov.jobs || []).length || (ov.deployments || []).some((d) => d.state === "starting" || d.state === "stopping")
    || (ov.nodes || []).some((n) => !["on", "off", "unknown"].includes(n.power_state))));
  const every = page.fast ? 2000 : busy ? 3000 : 10000;
  useEffect(() => {
    const poll = createSerialPoll(refreshOv, {
      intervalMs: every,
      isHidden: () => document.hidden,
    });
    const onVisible = () => poll.wake();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      poll.stop();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshOv, every]);
  useEffect(() => { api.health().then(setHealth).catch(() => {}); }, []);

  const closeToast = useCallback((id) => setToasts((list) => list.filter((x) => x.id !== id)), []);
  const pushToast = useCallback((toast) => setToasts((list) => [...list.slice(-2), { ...toast, id: Math.random().toString(36).slice(2) }]), []);
  const notify = useCallback((message, kind = "ok", action) => pushToast({ message, kind, action }), [pushToast]);
  const errorText = useCallback((error) => {
    if (!error) return "";
    const label = error.code && t.has(`err_${error.code}`) ? t(`err_${error.code}`) : "";
    const base = error.message || String(error);
    return label && label !== base ? `${label}: ${base}` : base;
  }, [t]);
  const toastError = useCallback((error) => pushToast({ message: errorText(error), hint: error?.hint || "", kind: "error" }), [pushToast, errorText]);
  const confirm = useCallback((request) => new Promise((resolve) => {
    confirmResolve.current = resolve;
    setConfirmReq(request);
  }), []);
  const closeConfirm = useCallback((answer) => {
    setConfirmReq(null);
    if (confirmResolve.current) confirmResolve.current(answer);
    confirmResolve.current = null;
  }, []);

  const changeLang = useCallback((next) => { saveLang(next); setLang(next); }, []);
  const nodeName = useCallback((id) => (ov?.nodes || []).find((n) => n.id === id)?.name || id, [ov]);

  const value = useMemo(() => ({
    t, lang, setLang: changeLang, health, ov, ovError, refreshOv, changed, version, notify, toastError, errorText, confirm, route, nodeName,
  }), [t, lang, changeLang, health, ov, ovError, refreshOv, changed, version, notify, toastError, errorText, confirm, route, nodeName]);

  const Component = page.component;
  const current = page.hidden ? page.parent : page.path;
  const badges = {
    work: (ov?.jobs || []).length + (ov?.deployments || []).filter((d) => d.state === "starting" || d.state === "stopping").length,
  };
  const demo = health?.demo || ov?.demo;

  return (
    <AppContext.Provider value={value}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 skip-link focus:p-2">{t("skip")}</a>
      <div className="min-h-dvh md:grid md:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-30 border-b md:flex md:h-dvh md:flex-col md:self-start md:border-b-0 md:border-r">
          <div className="brand">
            <img src="/icon-192.png" alt="" width="32" height="32" />
            <div className="min-w-0">Prometheus's Hoard<small>{t("subtitle")}</small></div>
            <div className="ml-auto flex items-center gap-2 md:hidden">
              {demo && <span className="chip chip-amber">{t("demo_short")}</span>}
              <button type="button" className="btn btn-sm" onClick={() => changeLang(lang === "es" ? "en" : "es")} aria-label={t("language_label")}>{lang === "es" ? "EN" : "ES"}</button>
            </div>
          </div>
          <nav aria-label={t("sections")} className="mainnav">
            {PAGES.filter((p) => !p.hidden).map((p) => {
              const n = p.badge ? badges[p.badge] : 0;
              return (
                <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0" aria-current={p.path === current ? "page" : undefined}>
                  <Icon d={p.icon} />
                  <span>{t(p.key)}</span>
                  {n > 0 && <span className="nav-badge" aria-label={t("badge_n", { n })}>{n > 99 ? "99+" : n}</span>}
                </a>
              );
            })}
          </nav>
          <div className="hidden flex-1 md:block" />
          <div className="hidden space-y-3 border-t px-4 py-3 md:block" style={{ borderColor: "var(--line)" }}>
            <ClusterLight ov={ov} t={t} />
            {demo && <span className="chip chip-amber">{t("demo_on")}</span>}
            <div className="flex items-center gap-2">
              <button type="button" className="btn btn-sm" onClick={() => changeLang(lang === "es" ? "en" : "es")}>{t("language")}</button>
              {health?.version && <span className="help">v{health.version}</span>}
            </div>
          </div>
        </aside>
        <main id="main" className={`min-w-0 ${route.page === "archivos" ? "main-explorer" : "px-4 py-4 md:px-7 md:py-6"}`}>
          {ovError && !ov && (
            <div className="banner banner-danger mb-4" role="alert">
              {t("unreachable")}: {ovError.message}. <button type="button" className="btn-link" onClick={refreshOv}>{t("retry")}</button>
            </div>
          )}
          {ovError && ov && route.page !== "archivos" && (
            <div className="banner banner-warn mb-4" role="status">
              {t("stale")}: {ovError.message}. <button type="button" className="btn-link" onClick={refreshOv}>{t("retry")}</button>
            </div>
          )}
          <Component key={page.path === "spark" ? `spark/${route.param}` : page.path} param={route.param} query={route.query} />
        </main>
      </div>
      <Toasts toasts={toasts} onClose={closeToast} />
      <ConfirmDialog request={confirmReq} onClose={closeConfirm} />
    </AppContext.Provider>
  );
}
