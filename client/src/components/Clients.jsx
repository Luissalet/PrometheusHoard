import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { compact } from "../format.js";
import { Busy, Chip, Icon, ICONS, Rel } from "./ui.jsx";

const API_KEYS = { chat: "cl_api_chat", completions: "cl_api_completions", responses: "cl_api_responses", messages: "cl_api_messages", embeddings: "cl_api_embeddings", other: "cl_api_other" };
const SHOWN = 5;

/** Who is using a model server, from its access log (the «Clientes» section of an endpoint card). */
export default function Clients({ clients: c, onChanged }) {
  const { t, lang } = useApp();
  const [all, setAll] = useState(false);
  if (!c) return null;
  const list = c.clients || [];
  const rows = all ? list : list.slice(0, SHOWN);
  const failed = c.ok === false;
  const reading = c.ok === null || c.ok === undefined;

  return (
    <section className="cl" aria-label={t("cl_title")}>
      <div className="cl-head">
        <h3>{t("cl_title")}</h3>
        {list.length > 0 && <span className="chip">{list.length}</span>}
        <span className="help cl-window">{t("cl_window", { h: c.window_h || 24 })}</span>
      </div>

      {failed && (
        <div className={`banner ${list.length ? "banner-warn" : "banner-danger"}`} role={list.length ? "status" : "alert"}>{errorText(t, c.error)}</div>
      )}
      {reading && !list.length && <p className="help">{t("cl_reading")}</p>}
      {!reading && !failed && !list.length && <p className="help">{t("cl_none")}</p>}

      {list.length > 0 && (
        <ul className="cl-list">
          {rows.map((x) => <ClientRow key={x.ip} x={x} pc={x.this_pc ? c.this_pc : null} onChanged={onChanged} />)}
        </ul>
      )}
      {list.length > SHOWN && (
        <button type="button" className="btn-link cl-more" onClick={() => setAll(!all)}>{all ? t("cl_show_less") : t("cl_show_all", { n: list.length })}</button>
      )}
      {(c.local_probes > 0 || c.container) && (
        <p className="help cl-foot">
          {c.local_probes > 0 && <span>{t("cl_probes", { n: compact(c.local_probes, lang) })}</span>}
          {c.local_probes > 0 && c.container && " · "}
          {c.container && <span title={c.source === "sudo" ? t("cl_via_sudo") : ""}>{t("cl_container", { name: String(c.container).slice(0, 24) })}</span>}
          {c.truncated && ` · ${t("cl_truncated")}`}
        </p>
      )}
    </section>
  );
}

function errorText(t, code) {
  if (code === "docker_denied") return t("cl_err_denied");
  if (code === "no_container") return t("cl_err_container");
  return t("cl_err_other", { error: code || "?" });
}

function ClientRow({ x, pc, onChanged }) {
  const { t, lang, nodeName, toastError, notify } = useApp();
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const label = x.name || (x.role === "this_pc" ? t("cl_this_pc") : x.role === "spark" ? nodeName(x.spark) : x.ip);
  const isAddress = label === x.ip;
  const hour = x.activity === "polling" ? x.requests_1h : x.inference_1h;

  return (
    <li className={`cl-row ${x.activity === "polling" ? "is-quiet" : ""}`}>
      <div className="cl-main">
        {editing ? (
          <Rename x={x} initial={x.name} onDone={async (saved) => { setEditing(false); if (saved) { notify(t("cl_saved")); await onChanged(); } }} onError={toastError} />
        ) : (
          <div className="cl-name">
            <b className="trunc" title={label}>{label}</b>
            {!isAddress && <span className="help mono trunc">{x.ip}</span>}
            <button type="button" className="btn btn-ghost btn-icon btn-sm" aria-label={t("cl_rename")} title={t("cl_rename")} onClick={() => setEditing(true)}>
              <Icon d={ICONS.pencil} size={14} />
            </button>
            {x.role === "this_pc" && pc && (
              <button type="button" className="btn-link cl-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
                <Icon d={ICONS.chevron} size={12} className={open ? "cl-chev is-open" : "cl-chev"} /> {open ? t("cl_pc_hide") : t("cl_pc_show", { n: pc.connections })}
              </button>
            )}
          </div>
        )}
        <div className="cl-badges">
          {x.kinds.map((k) => <Chip key={k} className={k === "messages" || k === "responses" ? "chip-accent" : ""}><span title={`${x.by_kind[k]}`}>{t(API_KEYS[k])}</span></Chip>)}
          {x.only_polling && <span title={t("cl_only_polls_tip")}><Chip className="chip-amber">{t("cl_only_polls")}</Chip></span>}
          {x.errors > 0 && <span title={t("cl_errors_tip", { h: x.errors_1h })}><Chip className="chip-danger">{t("cl_errors_n", { n: x.errors })}</Chip></span>}
        </div>
      </div>
      <div className="cl-side">
        <span className="num cl-rate">{t(x.activity === "polling" ? "cl_polls_h" : "cl_req_h", { n: compact(hour, lang) })}</span>
        <span className="help cl-seen">{t("cl_seen")} <Rel ts={x.last} /></span>
      </div>
      {open && pc && <Programs pc={pc} />}
    </li>
  );
}

function Rename({ x, initial, onDone, onError }) {
  const { t } = useApp();
  const [text, setText] = useState(initial || "");
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);
  const save = async () => {
    setBusy(true);
    try {
      await api.call("client_name_set", { ip: x.ip, name: text.trim() });
      onDone(true);
    } catch (e) {
      setBusy(false);
      onError(e);
    }
  };
  return (
    <form className="cl-rename" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <input ref={ref} className="field" value={text} maxLength={60} placeholder={t("cl_name_ph")} aria-label={t("cl_name_field", { ip: x.ip })}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") onDone(false); }} />
      <Busy busy={busy} className="btn btn-primary btn-sm" type="submit">{t("save")}</Busy>
      <button type="button" className="btn btn-sm" onClick={() => onDone(false)}>{t("cancel")}</button>
    </form>
  );
}

function Programs({ pc }) {
  const { t } = useApp();
  const rows = pc.processes || [];
  return (
    <div className="cl-pc">
      <div className="cl-pc-title">{t("cl_pc_now")}</div>
      {pc.error === "access_denied" && <p className="help">{t("cl_pc_denied")}</p>}
      {pc.error === "unavailable" && <p className="help">{t("cl_pc_unavailable")}</p>}
      {pc.error && !["access_denied", "unavailable"].includes(pc.error) && <p className="help">{t("cl_err_other", { error: pc.error })}</p>}
      {!pc.error && !rows.length && <p className="help">{t("cl_pc_none")}</p>}
      {rows.length > 0 && (
        <ul className="cl-procs">
          {rows.map((p) => (
            <li key={p.pid ?? "none"}>
              <b className="trunc">{p.label || t("cl_pc_unknown")}</b>
              <span className="help mono trunc" title={p.cmd}>{p.exe || (p.pid ? `pid ${p.pid}` : "")}{p.pid && p.exe ? ` · ${p.pid}` : ""}</span>
              <span className="num help cl-conn">{t("cl_conn_n", { n: p.connections })}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
