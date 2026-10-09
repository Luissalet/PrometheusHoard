import React from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { clock } from "../format.js";
import { Chip, Icon, ICONS, Rel, useLoad } from "./ui.jsx";
import { usePoll } from "./hooks.js";

const SHOWN = 8;

/** GPU driver errors the watcher found in the kernel logs of the Sparks (Xid, full-chip reset, GSP). It only reports; it never acts. */
export default function XidPanel() {
  const { t, lang, nodeName } = useApp();
  const load = useLoad(() => api.call("xid_events", { hours: 24, limit: 50 }), []);
  usePoll(() => load.reload(), 30000, true);
  const d = load.data;
  if (!d) return null;
  const unreadable = Object.entries(d.nodes || {}).filter(([, v]) => !v.ok).map(([id]) => nodeName(id));
  const watched = Object.keys(d.nodes || {}).length;
  const events = d.events || [];

  if (!d.enabled && !events.length) {
    return <section className="xid xid-quiet" aria-label={t("xid_title")}><span className="dot" /><span className="help">{t("xid_off")}</span></section>;
  }
  if (!events.length) {
    return (
      <section className="xid xid-quiet" aria-label={t("xid_title")}>
        <span className="dot" style={{ background: unreadable.length ? "var(--warn)" : "var(--ok)" }} />
        <span className="help">
          <b>{t("xid_title")}</b> · {t("xid_none", { n: watched })}
          {unreadable.length > 0 && ` · ${t("xid_unreadable", { names: unreadable.join(", ") })}`}
        </span>
      </section>
    );
  }
  return (
    <section className="xid banner banner-warn" role="status" aria-label={t("xid_title")}>
      <div className="xid-head">
        <Icon d={ICONS.warning || ICONS.activity} size={16} />
        <b>{t("xid_title")}</b>
        <Chip className="chip-danger">{t("xid_n", { n: d.total })}</Chip>
        {!d.enabled && <Chip>{t("xid_off_short")}</Chip>}
        <span className="help xid-note">{t("xid_only_warns")}</span>
      </div>
      <ul className="xid-list">
        {events.slice(0, SHOWN).map((e) => (
          <li key={e.id}>
            <div className="xid-row">
              <Chip className="chip-accent">{nodeName(e.node)}</Chip>
              <Chip className="chip-danger">{e.kind === "xid" ? t("xid_kind_xid", { code: e.xid }) : t(`xid_kind_${e.kind}`)}</Chip>
              {e.meaning && <span>{e.meaning}</span>}
              <span className="help xid-when" title={clock(e.t, lang)}><Rel ts={e.t} /></span>
              {e.notified === true && <Chip className="chip-ok">{t("xid_notified")}</Chip>}
            </div>
            <code className="xid-line" title={e.line}>{e.line}</code>
          </li>
        ))}
      </ul>
      {events.length > SHOWN && <p className="help">{t("xid_more", { n: events.length - SHOWN })}</p>}
      {unreadable.length > 0 && <p className="help">{t("xid_unreadable", { names: unreadable.join(", ") })}</p>}
    </section>
  );
}
