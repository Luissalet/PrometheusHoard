import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { splitList } from "../format.js";
import { Busy, Chip, ErrorBox, Field, Icon, ICONS, Section, Spinner, Switch, useBusy, useLoad } from "../components/ui.jsx";

const NUMS = ["poll_s", "idle_poll_s", "history_min", "ssh_timeout_s", "xid_interval_s"];

export default function Ajustes() {
  const { t, lang, setLang, notify, changed, confirm } = useApp();
  const settings = useLoad(() => api.call("settings_get"), []);
  const recipes = useLoad(() => api.call("recipes_list"), []);
  const [form, setForm] = useState(null);
  const [token, setToken] = useState("");
  const [busy, run] = useBusy();

  useEffect(() => {
    if (!settings.data) return;
    const s = settings.data;
    setForm({
      nodes: (s.nodes || []).map((n) => ({ ...n })),
      recipes_dir: s.recipes_dir || "",
      models_dirs: (s.models_dirs || []).join("\n"),
      hf_cache: !!s.hf_cache,
      poll_s: s.poll_s, idle_poll_s: s.idle_poll_s, history_min: s.history_min, ssh_timeout_s: s.ssh_timeout_s,
      show_system: !!s.show_system,
      trash_dir: s.trash_dir || "",
      remote_dir: s.remote_dir || "",
      default_endpoint: s.default_endpoint || "",
      xid_watch: s.xid_watch !== false, xid_interval_s: s.xid_interval_s ?? 60, xid_notify: s.xid_notify !== false,
    });
  }, [settings.data]);

  if (!form) {
    return (
      <div className="space-y-4">
        <h1>{t("nav_settings")}</h1>
        {settings.error ? <ErrorBox error={settings.error} onRetry={settings.reload} /> : <div className="help flex items-center gap-2"><Spinner />{t("loading")}</div>}
      </div>
    );
  }

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const setNode = (i, key, value) => setForm((f) => ({ ...f, nodes: f.nodes.map((n, j) => (j === i ? { ...n, [key]: value } : n)) }));
  const addNode = () => setForm((f) => {
    let k = f.nodes.length + 1;
    while (f.nodes.some((n) => n.id === `spark${k}`)) k += 1;
    return { ...f, nodes: [...f.nodes, { id: `spark${k}`, name: `Spark${k}`, ssh: `Spark${k}`, enabled: true, api_host: "", host: "", proxy_jump: "", color: "" }] };
  });
  const removeNode = async (i) => {
    const n = form.nodes[i];
    const ok = await confirm({ title: t("remove_spark_title", { name: n.name || n.id }), message: t("remove_spark_msg"), confirmLabel: t("remove") });
    if (ok) setForm((f) => ({ ...f, nodes: f.nodes.filter((_, j) => j !== i) }));
  };
  const ids = form.nodes.map((n) => n.id.trim());
  const dupes = ids.filter((id, i) => id && ids.indexOf(id) !== i);
  const badIds = form.nodes.filter((n) => !/^[a-z0-9][a-z0-9_-]*$/i.test(n.id.trim()));
  const invalid = dupes.length > 0 || badIds.length > 0 || NUMS.some((k) => form[k] === "" || Number.isNaN(Number(form[k])));

  const save = () => run("save", async () => {
    const body = {
      nodes: form.nodes.map((n) => ({ id: n.id.trim(), name: n.name.trim() || n.id.trim(), ssh: n.ssh.trim(), enabled: !!n.enabled, api_host: (n.api_host || "").trim(),
                                        host: (n.host || "").trim(), proxy_jump: (n.proxy_jump || "").trim(), color: n.color || "" })),
      recipes_dir: form.recipes_dir.trim(),
      models_dirs: splitList(form.models_dirs),
      hf_cache: form.hf_cache,
      show_system: form.show_system,
      trash_dir: form.trash_dir.trim(),
      remote_dir: form.remote_dir.trim(),
      default_endpoint: form.default_endpoint,
      xid_watch: form.xid_watch,
      xid_notify: form.xid_notify,
    };
    for (const k of NUMS) body[k] = Number(form[k]);
    await api.call("settings_set", body);
    notify(t("saved"));
    settings.reload();
    recipes.reload();
    changed();
  });
  const saveToken = (value) => run("token", async () => {
    const res = await api.call("hf_token_set", { hf_token: value });
    setToken("");
    notify(res.hf_token ? t("token_saved") : t("token_removed"));
    settings.reload();
  });

  const validRecipes = (recipes.data?.recipes || []).filter((r) => !r.invalid);
  return (
    <form className="space-y-8 settings" onSubmit={(e) => { e.preventDefault(); if (!invalid) save(); }}>
      <header className="page-head">
        <div className="min-w-0">
          <h1>{t("nav_settings")}</h1>
          <p className="help">{t("settings_help")}</p>
        </div>
        <Busy className="btn btn-primary" busy={busy.save} disabled={invalid} onClick={save}><Icon d={ICONS.save} size={15} />{t("save")}</Busy>
      </header>

      <Section title={t("sparks")} count={form.nodes.length} actions={<button type="button" className="btn btn-sm" onClick={addNode}><Icon d={ICONS.plus} size={14} />{t("add_spark")}</button>}>
        <p className="help">{t("sparks_help")}</p>
        <div className="space-y-2">
          {form.nodes.map((n, i) => (
            <div key={i} className="panel node-row">
              <div className="node-row-top">
                <input type="color" className="color-pick" value={n.color || "#76b900"} onChange={(e) => setNode(i, "color", e.target.value)} aria-label={t("color")} />
                <b className="trunc flex-1">{n.name || n.id || "—"}</b>
                <label className="inline-flex items-center gap-2 help"><Switch checked={n.enabled} onChange={(v) => setNode(i, "enabled", v)} label={t("enabled")} />{t("enabled")}</label>
                <button type="button" className="btn btn-sm btn-icon btn-danger" onClick={() => removeNode(i)} aria-label={t("remove_spark", { name: n.name || n.id })}><Icon d={ICONS.trash} size={14} /></button>
              </div>
              <div className="node-fields">
                <Field label={t("spark_id")} hint={dupes.includes(n.id.trim()) ? <span className="err-text">{t("id_dupe")}</span> : !/^[a-z0-9][a-z0-9_-]*$/i.test(n.id.trim()) ? <span className="err-text">{t("id_bad")}</span> : ""}>
                  <input className="field mono" value={n.id} onChange={(e) => setNode(i, "id", e.target.value)} />
                </Field>
                <Field label={t("name")}><input className="field" value={n.name} onChange={(e) => setNode(i, "name", e.target.value)} /></Field>
                <Field label={t("ssh_alias")}><input className="field mono" value={n.ssh} onChange={(e) => setNode(i, "ssh", e.target.value)} /></Field>
                <Field label={t("api_host")}><input className="field mono" value={n.api_host || ""} onChange={(e) => setNode(i, "api_host", e.target.value)} placeholder={t("api_host_ph")} /></Field>
                <Field label={t("ssh_host")}><input className="field mono" value={n.host || ""} onChange={(e) => setNode(i, "host", e.target.value)} placeholder={t("ssh_host_ph")} /></Field>
                <Field label={t("proxy_jump")}><input className="field mono" value={n.proxy_jump || ""} onChange={(e) => setNode(i, "proxy_jump", e.target.value)} placeholder={t("proxy_jump_ph")} /></Field>
              </div>
            </div>
          ))}
          {!form.nodes.length && <p className="panel help">{t("no_sparks_help")}</p>}
        </div>
        <p className="help">{t("api_host_help")}</p>
        <p className="help">{t("ssh_route_help")}</p>
      </Section>

      <Section title={t("folders")}>
        <div className="panel grid gap-4 md:grid-cols-2">
          <Field label={t("models_dirs")} hint={t("models_dirs_help")}>
            <textarea className="field" rows={3} value={form.models_dirs} onChange={(e) => set("models_dirs", e.target.value)} />
          </Field>
          <div className="space-y-4">
            <Field label={t("recipes_dir")} hint={t("recipes_dir_help")}>
              <input className="field mono" value={form.recipes_dir} onChange={(e) => set("recipes_dir", e.target.value)} />
            </Field>
            <label className="flex items-center gap-2"><Switch checked={form.hf_cache} onChange={(v) => set("hf_cache", v)} label={t("hf_cache")} /><span>{t("hf_cache")}</span></label>
          </div>
          <Field label={t("remote_dir")} hint={t("remote_dir_help")}>
            <input className="field mono" value={form.remote_dir} onChange={(e) => set("remote_dir", e.target.value)} />
          </Field>
          <Field label={t("trash_dir")} hint={t("trash_dir_help")}>
            <input className="field mono" value={form.trash_dir} onChange={(e) => set("trash_dir", e.target.value)} />
          </Field>
          <div className="md:col-span-2">
            <label className="flex items-start gap-2"><Switch checked={form.show_system} onChange={(v) => set("show_system", v)} label={t("show_system")} />
              <span><span className="block">{t("show_system")}</span><span className="help">{t("show_system_help")}</span></span></label>
          </div>
        </div>
      </Section>

      <Section title={t("refresh_section")}>
        <div className="panel grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label={t("poll_s")} hint={t("poll_s_help")}><input className="field num" type="number" min="1" step="0.5" value={form.poll_s} onChange={(e) => set("poll_s", e.target.value)} /></Field>
          <Field label={t("idle_poll_s")} hint={t("idle_poll_s_help")}><input className="field num" type="number" min="1" step="1" value={form.idle_poll_s} onChange={(e) => set("idle_poll_s", e.target.value)} /></Field>
          <Field label={t("history_min")} hint={t("history_min_help")}><input className="field num" type="number" min="1" max="240" step="1" value={form.history_min} onChange={(e) => set("history_min", e.target.value)} /></Field>
          <Field label={t("ssh_timeout_s")} hint={t("ssh_timeout_help")}><input className="field num" type="number" min="1" step="1" value={form.ssh_timeout_s} onChange={(e) => set("ssh_timeout_s", e.target.value)} /></Field>
        </div>
      </Section>

      <Section title={t("xid_settings")}>
        <div className="panel grid gap-4 md:grid-cols-2">
          <div className="md:col-span-2 space-y-3">
            <label className="flex items-start gap-2"><Switch checked={form.xid_watch} onChange={(v) => set("xid_watch", v)} label={t("xid_watch")} />
              <span><span className="block">{t("xid_watch")}</span><span className="help">{t("xid_watch_help")}</span></span></label>
            <label className="flex items-start gap-2"><Switch checked={form.xid_notify} disabled={!form.xid_watch} onChange={(v) => set("xid_notify", v)} label={t("xid_notify")} />
              <span><span className="block">{t("xid_notify")}</span><span className="help">{t("xid_notify_help")}</span></span></label>
          </div>
          <Field label={t("xid_interval_s")} hint={t("xid_interval_help")}>
            <input className="field num" type="number" min="15" max="3600" step="5" disabled={!form.xid_watch} value={form.xid_interval_s} onChange={(e) => set("xid_interval_s", e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title={t("integration")}>
        <div className="panel grid gap-4 md:grid-cols-2">
          <Field label={t("default_endpoint")} hint={t("default_endpoint_help")}>
            <select className="field" value={form.default_endpoint} onChange={(e) => set("default_endpoint", e.target.value)}>
              <option value="">{t("default_endpoint_none")}</option>
              {validRecipes.map((r) => <option key={r.name} value={r.name}>{r.title || r.name}</option>)}
              {form.default_endpoint && !validRecipes.some((r) => r.name === form.default_endpoint) && <option value={form.default_endpoint}>{form.default_endpoint}</option>}
            </select>
          </Field>
          <div className="space-y-2">
            <Field label={t("hf_token")} hint={t("hf_token_help")}>
              <div className="flex gap-2">
                <input className="field mono" type="password" autoComplete="new-password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={settings.data?.hf_token ? "••••••••" : "hf_…"} />
                <Busy className="btn" busy={busy.token} disabled={!token.trim()} onClick={() => saveToken(token.trim())}>{t("save_token")}</Busy>
              </div>
            </Field>
            <div className="flex items-center gap-2">
              {settings.data?.hf_token ? <Chip className="chip-ok"><Icon d={ICONS.check} size={11} />{t("token_is_set")}</Chip> : <Chip>{t("token_not_set")}</Chip>}
              {settings.data?.hf_token && <button type="button" className="btn btn-sm btn-danger" onClick={() => saveToken("")}>{t("remove_token")}</button>}
            </div>
          </div>
          {settings.data?.token_file && (
            <p className="help md:col-span-2">{t("mcp_token_file")}: <span className="mono">{settings.data.token_file}</span></p>
          )}
        </div>
      </Section>

      <Section title={t("language_section")}>
        <div className="panel">
          <Field label={t("language_label")} className="max-w-xs">
            <select className="field" value={lang} onChange={(e) => setLang(e.target.value)}>
              <option value="es">Español (España)</option>
              <option value="en">English</option>
            </select>
          </Field>
        </div>
      </Section>

      {invalid && <div className="banner banner-warn">{t("fix_before_save")}</div>}
      <div className="flex justify-end gap-2 sticky-save">
        <button type="button" className="btn" onClick={() => settings.reload().then(() => notify(t("reverted")))}>{t("revert")}</button>
        <Busy className="btn btn-primary" busy={busy.save} disabled={invalid} onClick={save}><Icon d={ICONS.save} size={15} />{t("save")}</Busy>
      </div>
      <button type="submit" hidden />
    </form>
  );
}
