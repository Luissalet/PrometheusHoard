import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { bytes, ctx, elapsed, num, pct, rel } from "../format.js";
import { Bar, Busy, Chip, CopyButton, Empty, ErrorBox, Field, Icon, ICONS, Modal, Section, Spinner, Tabs, useBusy, useLoad } from "../components/ui.jsx";
import { usePoll } from "../components/hooks.js";

const ACTIVE = new Set(["running", "starting", "stopping", "failed", "unknown"]);
const DEP_TONE = { running: "chip-ok", starting: "chip-amber", stopping: "chip-amber", failed: "chip-danger", unknown: "chip-amber", stopped: "" };
const JOB_TONE = { running: "chip-accent", done: "chip-ok", failed: "chip-danger", cancelled: "", lost: "chip-amber" };

export default function Modelos({ query }) {
  const { t, ov, version } = useApp();
  const deps = useLoad(() => api.call("deployments"), [version]);
  const recipes = useLoad(() => api.call("recipes_list"), [version]);
  const jobs = useLoad(() => api.call("jobs_list", { limit: 50 }), [version]);
  const [logsOf, setLogsOf] = useState(null);
  const deployments = deps.data?.deployments || [];
  const moving = deployments.some((d) => d.state === "starting" || d.state === "stopping");
  const jobsActive = (jobs.data?.jobs || []).some((j) => j.state === "running");
  usePoll(() => deps.reload(), moving ? 2000 : 8000);
  usePoll(() => jobs.reload(), jobsActive ? 2000 : 8000);

  useEffect(() => {
    const s = query.get("s");
    if (s) setTimeout(() => document.getElementById(`${s}-section`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
  }, [query]);

  const loaded = deployments.filter((d) => ACTIVE.has(d.state));
  const detected = ov?.detected || [];
  const nodes = ov?.nodes || [];
  return (
    <div className="space-y-8">
      <header className="page-head">
        <div className="min-w-0">
          <h1>{t("nav_models")}</h1>
          <p className="help">{t("models_help")}</p>
        </div>
        <nav className="jump" aria-label={t("on_this_page")}>
          <a href="#/modelos?s=loaded">{t("sec_loaded")}</a>
          <a href="#/modelos?s=recipes">{t("sec_recipes")}</a>
          <a href="#/modelos?s=disk">{t("sec_disk")}</a>
          <a href="#/modelos?s=jobs">{t("sec_jobs")}</a>
        </nav>
      </header>

      <Section id="loaded" title={t("sec_loaded")} count={loaded.length + detected.length || undefined}>
        {deps.error && !deps.data && <ErrorBox error={deps.error} onRetry={deps.reload} />}
        {deps.loading && !deps.data ? <Loading /> : loaded.length || detected.length ? (
          <div className="card-grid">
            {loaded.map((d) => <LoadedCard key={d.recipe} d={d} nodes={nodes} onLogs={() => setLogsOf(d)} reload={deps.reload} />)}
            {detected.map((x) => <DetectedCard key={x.recipe} x={x} nodes={nodes} />)}
          </div>
        ) : <Empty icon={ICONS.models} title={t("nothing_loaded")}>{t("nothing_loaded_help")}</Empty>}
      </Section>

      <Section id="recipes" title={t("sec_recipes")} count={(recipes.data?.recipes || []).length || undefined}
        sub={recipes.data?.folder ? <span className="inline-flex items-center gap-1.5">{t("recipes_folder")}: <span className="mono">{recipes.data.folder}</span> <CopyButton text={recipes.data.folder} /></span> : null}
        actions={<button type="button" className="btn btn-sm" onClick={recipes.reload}><Icon d={ICONS.refresh} size={14} />{t("refresh")}</button>}>
        {recipes.error && <ErrorBox error={recipes.error} onRetry={recipes.reload} />}
        {recipes.loading && !recipes.data ? <Loading /> : (recipes.data?.recipes || []).length ? (
          <div className="card-grid">
            {recipes.data.recipes.map((r) => <RecipeCard key={r.name} r={r} dep={deployments.find((d) => d.recipe === r.name)} nodes={nodes} reload={deps.reload} onLogs={(d) => setLogsOf(d)} />)}
          </div>
        ) : <Empty icon={ICONS.recipes} title={t("no_recipes")}>{t("no_recipes_help", { folder: recipes.data?.folder || "" })}</Empty>}
      </Section>

      <DiskModels nodes={nodes} onJob={jobs.reload} />

      <Jobs jobs={jobs} />

      {logsOf && <LogsModal d={logsOf} nodes={nodes} onClose={() => setLogsOf(null)} />}
    </div>
  );
}

function Loading() {
  const { t } = useApp();
  return <div className="help flex items-center gap-2"><Spinner />{t("loading")}</div>;
}

function NodeChips({ ids, head, nodes }) {
  const { t } = useApp();
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => {
        const n = nodes.find((x) => x.id === id);
        return (
          <Chip key={id} className={n && !n.online ? "chip-danger" : ""}>
            <Icon d={ICONS.computer} size={11} />{n?.name || id}{head ? <span className="role">{t(id === head ? "role_head" : "role_worker")}</span> : null}
          </Chip>
        );
      })}
    </span>
  );
}

function useDeploy(reload) {
  const { t, notify, toastError, confirm, changed } = useApp();
  const [busy, run] = useBusy();
  const start = (r, title) => run(`start-${r}`, async () => {
    try {
      await api.call("deploy_start", { recipe: r });
      notify(t("loading_model", { title }));
    } catch (e) {
      if (e.code !== "conflict") throw e;
      const names = (e.conflicts || []).join(", ");
      const ok = await confirm({
        title: names ? t("conflict_title", { names }) : t("conflict_mem_title"),
        message: names ? t("conflict_msg", { names, title }) : e.message,
        detail: names ? "" : e.hint || "",
        confirmLabel: names ? t("conflict_go", { names }) : t("try_anyway"),
        danger: true,
      });
      if (!ok) return;
      await api.call("deploy_start", { recipe: r, stop_conflicts: true });
      notify(t("loading_model", { title }));
    }
    reload();
    changed();
  }, { silent: true }).catch(toastError);
  const stop = async (d) => {
    const ok = await confirm({ title: t("unload_title", { title: d.title }), message: t("unload_msg"), confirmLabel: t("unload"), danger: true });
    if (!ok) return;
    const p = run(`stop-${d.recipe}`, async () => {
      await api.call("deploy_stop", { recipe: d.recipe });
      notify(t("unloaded", { title: d.title }));
      reload();
      changed();
    });
    setTimeout(reload, 600);
    await p;
  };
  return { busy, start, stop };
}

function LoadedCard({ d, nodes, onLogs, reload }) {
  const { t, lang } = useApp();
  const { busy, stop } = useDeploy(reload);
  return (
    <article className="panel card space-y-2.5">
      <div className="flex items-start gap-2">
        <Icon d={ICONS.models} size={20} />
        <div className="min-w-0 flex-1">
          <h3 style={{ overflowWrap: "anywhere" }}>{d.title}</h3>
          <div className="help mono trunc">{d.recipe}</div>
        </div>
        <Chip className={DEP_TONE[d.state]}>{(d.state === "starting" || d.state === "stopping") && <span className="spinner spinner-xs" />}{t(`dep_${d.state}`)}</Chip>
      </div>
      {d.state === "unknown" && <div className="banner banner-warn">{t("dep_unknown_help")}</div>}
      {(d.step || d.message) && d.state !== "running" && d.state !== "unknown" && (
        <div className={`banner ${d.state === "failed" ? "banner-danger" : "banner-info"}`}>
          {d.step && <b>{t("step")}: {d.step}. </b>}{d.message}
        </div>
      )}
      <NodeChips ids={d.nodes} head={d.head} nodes={nodes} />
      {d.base_url && (
        <div className="url-box">
          <span className="mono trunc flex-1">{d.base_url}</span>
          <CopyButton text={d.base_url} label={t("copy_url")} />
        </div>
      )}
      <dl className="facts">
        <div><dt>{t("served_name")}</dt><dd className="mono">{(d.served || []).join(", ") || d.served_model_name || "—"}</dd></div>
        <div><dt>{t("context")}</dt><dd>{ctx(d.max_model_len, lang)}</dd></div>
        <div><dt>{t("engine")}</dt><dd>{d.engine || "—"}</dd></div>
        <div><dt>{d.ready_at || !d.started ? t("since_label") : t("started_label")}</dt><dd>{d.ready_at ? rel(d.ready_at, lang) : d.started ? rel(d.started, lang) : d.external ? t("external") : "—"}</dd></div>
      </dl>
      {d.external && <p className="help">{t("external_help")}</p>}
      <div className="flex flex-wrap gap-2 pt-1">
        <Busy className="btn btn-sm btn-danger" busy={busy[`stop-${d.recipe}`]} disabled={d.state === "stopping"} onClick={() => stop(d)}><Icon d={ICONS.stop} size={13} />{t("unload")}</Busy>
        <button type="button" className="btn btn-sm" onClick={onLogs}><Icon d={ICONS.terminal} size={14} />{t("view_logs")}</button>
      </div>
    </article>
  );
}

function DetectedCard({ x, nodes }) {
  const { t, lang } = useApp();
  return (
    <article className="panel card space-y-2.5" data-testid={`detected-${x.recipe}`}>
      <div className="flex items-start gap-2">
        <Icon d={ICONS.models} size={20} />
        <div className="min-w-0 flex-1">
          <h3 style={{ overflowWrap: "anywhere" }}>{x.title}</h3>
          <div className="help mono trunc">{x.engine} · pid {x.pid}</div>
        </div>
        <Chip className={x.up ? DEP_TONE.running : DEP_TONE.starting}>{x.up ? t("dep_running") : t("dep_starting")}</Chip>
      </div>
      <p className="help">{x.up ? t("detected_help") : t("detected_down")}</p>
      <NodeChips ids={[x.node]} head={x.node} nodes={nodes} />
      <div className="url-box">
        <span className="mono trunc flex-1">{x.base_url}</span>
        <CopyButton text={x.base_url} label={t("copy_url")} />
      </div>
      <dl className="facts">
        <div><dt>{t("served_name")}</dt><dd className="mono">{(x.models || []).join(", ") || "—"}</dd></div>
        <div><dt>{t("context")}</dt><dd>{ctx(x.max_model_len, lang)}</dd></div>
        <div><dt>{t("engine")}</dt><dd>{x.engine || "—"}</dd></div>
        <div><dt>{t("detected_label")}</dt><dd>{x.node}:{x.port}</dd></div>
      </dl>
    </article>
  );
}

function RecipeCard({ r, dep, nodes, reload, onLogs }) {
  const { t, lang } = useApp();
  const { busy, start, stop } = useDeploy(reload);
  if (r.invalid) {
    return (
      <article className="panel card space-y-2 card-invalid">
        <div className="flex items-center gap-2"><Icon d={ICONS.warn} size={18} /><h3 className="flex-1 mono">{r.name}</h3><Chip className="chip-danger">{t("invalid")}</Chip></div>
        <p className="mono help" style={{ overflowWrap: "anywhere" }}>{r.invalid}</p>
      </article>
    );
  }
  const state = dep?.state || "stopped";
  const live = state !== "stopped";
  const offline = r.nodes.filter((id) => !nodes.find((n) => n.id === id)?.online);
  const measured = r.measured && Object.keys(r.measured).length ? r.measured : null;
  return (
    <article className={`panel card space-y-2.5 ${state === "running" ? "card-live" : ""}`}>
      <div className="flex items-start gap-2">
        <Icon d={ICONS.recipes} size={20} />
        <div className="min-w-0 flex-1">
          <h3 style={{ overflowWrap: "anywhere" }}>{r.title || r.name}</h3>
          <div className="help mono trunc">{r.name}</div>
        </div>
        {live && <Chip className={DEP_TONE[state]}>{(state === "starting" || state === "stopping") && <span className="spinner spinner-xs" />}{t(`dep_${state}`)}</Chip>}
      </div>
      {r.description && <p>{r.description}</p>}
      <NodeChips ids={r.nodes} head={r.nodes.length > 1 ? r.head : null} nodes={nodes} />
      <dl className="facts">
        <div><dt>{t("model")}</dt><dd className="mono">{r.model || "—"}</dd></div>
        <div><dt>{t("context")}</dt><dd>{ctx(r.max_model_len, lang)}</dd></div>
        <div><dt>{t("memory_per_spark")}</dt><dd>{r.memory_gb ? `${num(r.memory_gb, 0, lang)} GB` : "—"}</dd></div>
        <div><dt>{t("engine")}</dt><dd>{r.engine || "—"} · {t("port")} {r.port}</dd></div>
      </dl>
      {(r.tags || []).length > 0 && <div className="flex flex-wrap gap-1">{r.tags.map((x) => <Chip key={x}>{x}</Chip>)}</div>}
      {r.notes && <p className="help" style={{ whiteSpace: "pre-wrap" }}>{r.notes}</p>}
      {measured && (
        <div className="measured">
          <span className="label">{t("measured")}</span>
          <dl className="facts">{Object.entries(measured).map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="num">{typeof v === "number" ? num(v, Number.isInteger(v) ? 0 : 1, lang) : String(v)}</dd></div>)}</dl>
        </div>
      )}
      {state === "starting" && dep && (
        <div className="banner banner-info flex items-center gap-2"><span className="spinner" />{dep.step ? `${t("step")}: ${dep.step}. ` : ""}{dep.message || t("starting_wait")}</div>
      )}
      {state === "failed" && dep?.message && <div className="banner banner-danger">{dep.message}</div>}
      {offline.length > 0 && !live && <p className="help"><Icon d={ICONS.warn} size={12} /> {t("recipe_offline", { names: offline.map((id) => nodes.find((n) => n.id === id)?.name || id).join(", ") })}</p>}
      <div className="flex flex-wrap gap-2 pt-1">
        {state === "running" || state === "starting" || state === "unknown" ? (
          <Busy className="btn btn-sm btn-danger" busy={busy[`stop-${r.name}`]} onClick={() => stop(dep)}><Icon d={ICONS.stop} size={13} />{t("unload")}</Busy>
        ) : (
          <Busy className="btn btn-sm btn-primary" busy={busy[`start-${r.name}`]} disabled={state === "stopping"} onClick={() => start(r.name, r.title || r.name)}><Icon d={ICONS.play} size={13} />{state === "failed" ? t("retry_load") : t("load")}</Busy>
        )}
        {live && <button type="button" className="btn btn-sm" onClick={() => onLogs(dep)}><Icon d={ICONS.terminal} size={14} />{t("view_logs")}</button>}
      </div>
    </article>
  );
}

function LogsModal({ d, nodes, onClose }) {
  const { t } = useApp();
  const [node, setNode] = useState(d.head || d.nodes[0]);
  const logs = useLoad(() => api.call("deploy_logs", { recipe: d.recipe, node, lines: 400 }), [d.recipe, node]);
  const L = logs.data;
  return (
    <Modal title={t("logs_of", { title: d.title })} onClose={onClose} wide
      footer={<><button type="button" className="btn" onClick={logs.reload}><Icon d={ICONS.refresh} size={14} />{t("refresh")}</button><button type="button" className="btn btn-primary" onClick={onClose}>{t("close")}</button></>}>
      <Tabs tabs={d.nodes.map((id) => ({ id, label: `${nodes.find((n) => n.id === id)?.name || id}${id === d.head ? ` · ${t("role_head")}` : ""}` }))} value={node} onChange={setNode} />
      {logs.error && <ErrorBox error={logs.error} onRetry={logs.reload} />}
      {logs.loading && !L ? <Loading /> : L && (
        <div className="space-y-3">
          {L.container !== undefined && <div><span className="label">{t("log_container")}</span><pre className="pre">{L.container || t("log_empty")}</pre></div>}
          {(L.jobs || []).map((j) => (
            <div key={j.id || j.title}>
              <span className="label">{j.title} · <Chip className={JOB_TONE[j.state]}>{t(`job_${j.state}`)}</Chip></span>
              <pre className="pre">{j.log || t("log_empty")}</pre>
            </div>
          ))}
          {L.script !== undefined && <div><span className="label">{t("log_script")}</span><pre className="pre">{L.script || t("log_empty")}</pre></div>}
          {L.container === undefined && !(L.jobs || []).length && L.script === undefined && <p className="help">{t("no_logs")}</p>}
        </div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ models on disk
function DiskModels({ nodes, onJob }) {
  const { t, lang, notify, confirm, toastError, version } = useApp();
  const inv = useLoad(() => api.call("models_list", {}), [version]);
  const [busy, run] = useBusy();
  const [copyOf, setCopyOf] = useState(null);
  const [hf, setHf] = useState(false);
  const byNode = inv.data?.nodes || {};
  const errors = inv.data?.errors || {};
  const ids = [...new Set([...nodes.map((n) => n.id), ...Object.keys(byNode)])];
  const total = Object.values(byNode).reduce((s, l) => s + l.length, 0);
  const refresh = () => run("refresh", async () => { inv.setData(await api.call("models_list", { refresh: true })); });
  const del = async (node, m) => {
    const name = nodes.find((n) => n.id === node)?.name || node;
    const ok = await confirm({ title: t("delete_model_title", { name: m.name }), message: t("delete_model_msg", { size: bytes(m.bytes, lang), spark: name }), confirmLabel: t("delete") });
    if (!ok) return;
    try {
      await api.call("model_delete", { node, model: m.path, confirm: true });
      notify(t("model_deleted", { name: m.name }));
      inv.setData(await api.call("models_list", { refresh: true }));
    } catch (e) {
      toastError(e);
    }
  };
  const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
  const status = (m) => (
    <>
      {m.downloading && <Chip className="chip-accent"><span className="spinner spinner-xs" />{t("downloading")}</Chip>}
      {m.incomplete && !m.downloading && <Chip className="chip-amber">{t("incomplete")}</Chip>}
      {!m.downloading && !m.incomplete && <Chip className="chip-ok">{t("complete")}</Chip>}
    </>
  );
  const actions = (node, m) => (
    <span className="flex flex-wrap justify-end gap-1">
      <button type="button" className="btn btn-sm" disabled={nodes.length < 2} onClick={() => setCopyOf({ node, m })}><Icon d={ICONS.send} size={13} /><span>{t("copy_to")}</span></button>
      <a className="btn btn-sm btn-icon" href={`#/archivos/${encodeURIComponent(node)}?path=${encodeURIComponent(m.path)}`} aria-label={t("open_folder")}><Icon d={ICONS.folder} size={14} /></a>
      <button type="button" className="btn btn-sm btn-icon btn-danger" onClick={() => del(node, m)} aria-label={t("delete_model", { name: m.name })}><Icon d={ICONS.trash} size={14} /></button>
    </span>
  );
  return (
    <Section id="disk" title={t("sec_disk")} count={total || undefined}
      actions={<>
        <Busy className="btn btn-sm" busy={busy.refresh} onClick={refresh}><Icon d={ICONS.refresh} size={14} />{t("rescan")}</Busy>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => setHf(true)}><Icon d={ICONS.cloud} size={14} />{t("hf_download")}</button>
      </>}>
      {inv.error && <ErrorBox error={inv.error} onRetry={inv.reload} />}
      {inv.loading && !inv.data ? <Loading /> : (
        <div className="space-y-4">
          {ids.map((id) => {
            const list = byNode[id] || [];
            const size = list.reduce((s, m) => s + (m.bytes || 0), 0);
            return (
              <div key={id} className="panel p-0 overflow-hidden">
                <div className="group-head">
                  <Icon d={ICONS.drive} size={16} />
                  <b>{nodeName(id)}</b>
                  <span className="help">{t("n_models", { n: list.length })}{list.length ? ` · ${bytes(size, lang)}` : ""}</span>
                  <a className="ml-auto btn btn-sm btn-ghost" href={`#/archivos/${encodeURIComponent(id)}?path=${encodeURIComponent("~/models")}`}><Icon d={ICONS.folder} size={13} /><span className="hide-sm">{t("open_models_folder")}</span></a>
                </div>
                {errors[id] && <div className="p-2"><div className="banner banner-danger">{typeof errors[id] === "string" ? errors[id] : JSON.stringify(errors[id])}</div></div>}
                {!list.length ? <p className="help px-3 py-3">{errors[id] ? "" : t("no_models_here")}</p> : (
                  <>
                    <div className="hidden md:block scroll-x">
                      <table className="tbl tbl-fixed">
                        <colgroup><col style={{ width: "27%" }} /><col style={{ width: "9%" }} /><col style={{ width: "22%" }} /><col style={{ width: "13%" }} /><col style={{ width: "7%" }} /><col style={{ width: "10%" }} /><col style={{ width: "12rem" }} /></colgroup>
                        <thead><tr><th>{t("col_name")}</th><th className="r">{t("col_size")}</th><th>{t("architecture")}</th><th>{t("quant")}</th><th className="r">{t("context")}</th><th>{t("status")}</th><th /></tr></thead>
                        <tbody>
                          {list.map((m) => (
                            <tr key={m.path}>
                              <td><div className="font-semibold" style={{ overflowWrap: "anywhere" }}>{m.name}</div><div className="help mono">{m.repo || m.path}</div></td>
                              <td className="r num">{bytes(m.bytes, lang)}<div className="help">{t("n_files", { n: m.files ?? "—" })}</div></td>
                              <td><span className="mono">{(m.architectures || []).join(", ") || m.model_type || "—"}</span>{(m.gguf || []).length > 0 && <Chip className="ml-1">GGUF</Chip>}</td>
                              <td>{m.quant || "—"}</td>
                              <td className="r num">{ctx(m.max_context, lang)}</td>
                              <td>{status(m)}</td>
                              <td className="r">{actions(id, m)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="md:hidden divide-list">
                      {list.map((m) => (
                        <div key={m.path} className="p-3 space-y-1.5">
                          <div className="flex items-start gap-2"><Icon d={ICONS.weights} size={16} /><div className="min-w-0 flex-1"><div className="font-semibold" style={{ overflowWrap: "anywhere" }}>{m.name}</div><div className="help mono" style={{ overflowWrap: "anywhere" }}>{m.repo || m.path}</div></div>{status(m)}</div>
                          <div className="help">{[bytes(m.bytes, lang), (m.architectures || [])[0] || m.model_type, m.quant, m.max_context ? `${t("context")} ${ctx(m.max_context, lang)}` : null].filter(Boolean).join(" · ")}</div>
                          {actions(id, m)}
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
      {copyOf && <CopyModelModal {...copyOf} nodes={nodes} onClose={() => setCopyOf(null)} onDone={onJob} />}
      {hf && <HfModal nodes={nodes} onClose={() => setHf(false)} onDone={onJob} />}
    </Section>
  );
}

function CopyModelModal({ node, m, nodes, onClose, onDone }) {
  const { t, lang, notify } = useApp();
  const others = nodes.filter((n) => n.id !== node);
  const [to, setTo] = useState(others.filter((n) => n.online).map((n) => n.id));
  const [busy, run] = useBusy();
  const go = () => run("go", async () => {
    const res = await api.call("model_copy", { node, model: m.path, to });
    notify(t("copy_started", { n: (res.jobs || []).length }), "ok", { label: t("see_jobs"), href: "#/modelos?s=jobs" });
    onDone?.();
    onClose();
  });
  return (
    <Modal title={t("copy_model_title", { name: m.name })} onClose={onClose}
      footer={<><button type="button" className="btn" onClick={onClose}>{t("cancel")}</button><Busy className="btn btn-primary" busy={busy.go} disabled={!to.length} onClick={go}><Icon d={ICONS.send} size={14} />{t("copy")}</Busy></>}>
      <p className="help">{t("copy_model_help", { size: bytes(m.bytes, lang), src: nodes.find((n) => n.id === node)?.name || node })}</p>
      <fieldset className="space-y-1.5">
        <legend className="label">{t("dest_sparks")}</legend>
        {others.map((n) => (
          <label key={n.id} className={`check-row ${!n.online ? "opacity-50" : ""}`}>
            <input type="checkbox" checked={to.includes(n.id)} disabled={!n.online} onChange={(e) => setTo(e.target.checked ? [...to, n.id] : to.filter((x) => x !== n.id))} />
            <Icon d={ICONS.computer} size={15} /><span>{n.name}</span>{!n.online && <Chip>{t("ps_unreachable")}</Chip>}
          </label>
        ))}
      </fieldset>
    </Modal>
  );
}

function HfModal({ nodes, onClose, onDone }) {
  const { t, notify } = useApp();
  const settings = useLoad(() => api.call("settings_get"), []);
  const [repo, setRepo] = useState("");
  const [node, setNode] = useState(nodes.find((n) => n.online)?.id || nodes[0]?.id || "");
  const [name, setName] = useState("");
  const [revision, setRevision] = useState("");
  const [busy, run] = useBusy();
  const valid = /^[\w.-]+\/[\w.-]+$/.test(repo.trim());
  const go = () => run("go", async () => {
    await api.call("model_download", { node, repo: repo.trim(), name: name.trim(), revision: revision.trim() });
    notify(t("download_started", { repo: repo.trim() }), "ok", { label: t("see_jobs"), href: "#/modelos?s=jobs" });
    onDone?.();
    onClose();
  });
  return (
    <Modal title={t("hf_download")} onClose={onClose}
      footer={<><button type="button" className="btn" onClick={onClose}>{t("cancel")}</button><Busy className="btn btn-primary" busy={busy.go} disabled={!valid || !node} onClick={go}><Icon d={ICONS.download} size={14} />{t("download")}</Busy></>}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid && node) go(); }}>
        <Field label={t("hf_repo")} hint={t("hf_repo_help")}>
          <input className="field mono" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="org/modelo" autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("dest_spark")}>
          <select className="field" value={node} onChange={(e) => setNode(e.target.value)}>
            {nodes.map((n) => <option key={n.id} value={n.id} disabled={!n.online}>{n.name}{!n.online ? ` (${t("ps_unreachable")})` : ""}</option>)}
          </select>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("folder_name_opt")} hint={t("folder_name_help")}>
            <input className="field mono" value={name} onChange={(e) => setName(e.target.value)} placeholder={repo.includes("/") ? repo.split("/")[1] : ""} />
          </Field>
          <Field label={t("revision_opt")}>
            <input className="field mono" value={revision} onChange={(e) => setRevision(e.target.value)} placeholder="main" />
          </Field>
        </div>
        <p className="help">{settings.data ? (settings.data.hf_token ? t("hf_token_set") : <>{t("hf_token_missing")} <a className="btn-link" href="#/ajustes">{t("nav_settings")}</a></>) : ""}</p>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------ jobs
function Jobs({ jobs }) {
  const { t, lang, toastError, notify } = useApp();
  const [busy, run] = useBusy();
  const [filter, setFilter] = useState("all");
  const [more, setMore] = useState(false);
  const list = (jobs.data?.jobs || []).filter((j) => filter === "all" || (filter === "active" ? j.state === "running" : j.state !== "running"));
  const cancel = (j) => run(j.id, async () => {
    await api.call("job_cancel", { job: j.id });
    notify(t("job_cancelled_msg", { title: j.title }));
    jobs.reload();
  }).catch(toastError);
  return (
    <Section id="jobs" title={t("sec_jobs")} count={(jobs.data?.jobs || []).length || undefined}
      actions={<div className="seg" role="group" aria-label={t("filter")}>
        {["all", "active", "finished"].map((f) => <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{t(`jobs_${f}`)}</button>)}
      </div>}>
      {jobs.error && <ErrorBox error={jobs.error} onRetry={jobs.reload} />}
      {jobs.loading && !jobs.data ? <Loading /> : !list.length ? <Empty icon={ICONS.jobs} title={t("no_jobs")}>{t("no_jobs_help")}</Empty> : (
        <div className="panel p-0 divide-list">
          {(more ? list : list.slice(0, 10)).map((j) => {
            const indeterminate = j.state === "running" && (j.progress === null || j.progress === undefined);
            return (
              <div key={j.id} className="job">
                <div className="flex flex-wrap items-center gap-2">
                  <Icon d={j.kind === "download" ? ICONS.cloud : j.kind === "copy" ? ICONS.copy : ICONS.recipes} size={17} />
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold" style={{ overflowWrap: "anywhere" }}>{j.title}</div>
                    <div className="help">{t(`jobkind_${j.kind}`)} · {j.node} · {rel(j.created, lang)} · {elapsed(j.elapsed_s)}</div>
                  </div>
                  <Chip className={JOB_TONE[j.state]}>{j.state === "running" && <span className="spinner spinner-xs" />}{t(`job_${j.state}`)}</Chip>
                  {j.state === "running" && <Busy className="btn btn-sm" busy={busy[j.id]} onClick={() => cancel(j)}><Icon d={ICONS.stop} size={12} />{t("cancel")}</Busy>}
                </div>
                {j.state === "running" && (
                  <div className="flex items-center gap-3">
                    <Bar value={j.progress ?? 0} alarm={false} className={indeterminate ? "bar-indeterminate flex-1" : "flex-1"} />
                    <span className="help num shrink-0">
                      {j.progress !== null && j.progress !== undefined ? pct(j.progress * 100, lang) : ""}
                      {j.watched_bytes != null ? ` · ${bytes(j.watched_bytes, lang)}${j.expected_bytes ? ` / ${bytes(j.expected_bytes, lang)}` : ""}` : ""}
                    </span>
                  </div>
                )}
                {j.log && (
                  <details>
                    <summary className="help">{t("log_tail")}</summary>
                    <pre className="pre mt-1">{j.log}</pre>
                  </details>
                )}
              </div>
            );
          })}
          {list.length > 10 && (
            <div className="p-2 text-center"><button type="button" className="btn btn-sm" onClick={() => setMore(!more)}>{more ? t("show_less") : t("show_more", { n: list.length - 10 })}</button></div>
          )}
        </div>
      )}
    </Section>
  );
}
