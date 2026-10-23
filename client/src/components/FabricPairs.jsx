import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { locale, linkSpeed, num } from "../format.js";
import { Busy, CopyButton, ErrorBox } from "./ui.jsx";
import { useContextProvider, useCopilotRefresh } from "./hooks.js";

function Direction({ from, to, address }) {
  const { t } = useApp();
  return <div className="fabric-direction">
    <span className="help">{t("fabric_direction", { from, to })}</span>
    {address == null ? <span className="help">{t("fabric_no_address")}</span> :
      <div className="fabric-address"><code>{address}</code><CopyButton text={address} label={t("fabric_copy", { from, to })} /></div>}
  </div>;
}

export function FabricSnapshot({ data, queriedAt, nodes }) {
  const { t, lang } = useApp();
  const name = id => nodes.find(node => node.id === id)?.name || id;
  const ids = [...new Set([...data.pairs.flatMap(pair => [pair.a, pair.b]), ...Object.keys(data.links)])];
  return <div className="fabric-snapshot">
    <p className="help">{t("fabric_queried", { at:new Date(queriedAt).toLocaleString(locale(lang)) })}</p>
    {data.pairs.length ? <ul className="fabric-pairs">{data.pairs.map(pair =>
      <li key={`${pair.a}/${pair.b}`}>
        <h3>{name(pair.a)} · {name(pair.b)}</h3>
        <p className="help mono">{pair.a} · {pair.b}</p>
        <div className="fabric-directions">
          <Direction from={name(pair.a)} to={name(pair.b)} address={pair.ip_b_from_a} />
          <Direction from={name(pair.b)} to={name(pair.a)} address={pair.ip_a_from_b} />
        </div>
      </li>)}</ul> : <p className="help">{t("fabric_no_pairs")}</p>}
    <details className="fabric-interfaces">
      <summary className="btn-link">{t("fabric_interfaces")}</summary>
      {ids.map(id => <section key={id} className="fabric-node">
        <h3><a href={`#/spark/${encodeURIComponent(id)}`}>{name(id)}</a></h3>
        <p className="help mono">{id}</p>
        {!Object.hasOwn(data.links,id) ? <p className="help">{t("fabric_no_reading")}</p> :
          !Object.keys(data.links[id]).length ? <p className="help">{t("fabric_no_interfaces")}</p> :
          <dl>{Object.entries(data.links[id]).map(([iface,link]) => <div key={iface}>
            <dt className="mono">{iface}</dt>
            <dd>
              <span>{link.up == null ? t("fabric_unknown") : t(link.up ? "link_up" : "link_down")}</span>
              <span>{t("fabric_reported_speed")}: {link.speed_mbps == null ? t("fabric_unknown") : link.speed_mbps === 0 ? `0 Mb/s` : linkSpeed(link.speed_mbps,lang)}</span>
              <span>MTU: {link.mtu == null ? t("fabric_unknown") : num(link.mtu,0,lang)}</span>
              <span className="mono">{link.v4?.length ? link.v4.join(" · ") : t("fabric_no_ipv4")}</span>
            </dd>
          </div>)}</dl>}
      </section>)}
    </details>
  </div>;
}

export default function FabricPairs({ nodes }) {
  const { t } = useApp();
  const [open,setOpen] = useState(false), [snapshot,setSnapshot] = useState(null);
  const [busy,setBusy] = useState(false), [error,setError] = useState(null);
  const request = useRef(0);
  const scope = JSON.stringify(nodes.map(node => [node.id,node.name]));
  const invalidate = () => { request.current++;setSnapshot(null);setBusy(false);setError(null); };
  useEffect(() => { invalidate();return () => { request.current++; }; },[scope]);
  useCopilotRefresh(invalidate);
  useContextProvider(() => open ? {kind:"fabric_status",dirty:false,fields:{preparing:busy},
    ...(snapshot ? {result:snapshot.data,queried_at:snapshot.queriedAt} : {})} : null);
  async function load() {
    const current=++request.current;
    setBusy(true);setError(null);setSnapshot(null);
    try {
      const data=await api.call("fabric_status",{});
      if(current===request.current)setSnapshot({data,queriedAt:Date.now()});
    } catch(cause) { if(current===request.current)setError(cause); }
    finally { if(current===request.current)setBusy(false); }
  }
  return <section className="fabric-panel" aria-labelledby="fabric-pairs-title">
    <div className="fabric-heading"><h2 id="fabric-pairs-title">{t("fabric_title")}</h2>
      <button type="button" className="btn btn-sm" aria-expanded={open} aria-controls="fabric-pairs-body" onClick={() => {if(open)invalidate();setOpen(!open);}}>{t(open ? "close" : "fabric_open")}</button>
    </div>
    {open && <div id="fabric-pairs-body" className="fabric-body">
      <p className="help">{t("fabric_cached")}</p>
      <Busy className="btn btn-sm" busy={busy} onClick={load}>{t(snapshot ? "fabric_refresh" : "fabric_query")}</Busy>
      <ErrorBox error={error} onRetry={load} />
      {!snapshot && !busy && !error && <p className="help">{t("fabric_before_query")}</p>}
      {snapshot && <FabricSnapshot data={snapshot.data} queriedAt={snapshot.queriedAt} nodes={nodes} />}
    </div>}
  </section>;
}
