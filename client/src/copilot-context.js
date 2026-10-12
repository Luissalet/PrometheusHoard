// What the shared assistant launcher asks this app for: the real objects open in the current view and the unsaved form fields,
// plus a way to refresh the open view when the assistant finishes a turn. Nothing here renders anything.
import { api } from "./api.js";
import { splitList } from "./format.js";

const PAGE_NAMES = { "": "cluster", spark: "spark", sirviendo: "serving", archivos: "files", modelos: "models", ajustes: "settings" };
const EDITOR_ORDER = { "settings-form": 0, "model-download-new": 1, "client-name-form": 2, "file-editor": 3 };
const MAX_SPARKS = 8;
const MAX_DEPLOYMENTS = 8;
const REFRESH_DEBOUNCE_MS = 250;

// ---- form helpers ---------------------------------------------------------------------------------------------------------
// Fields of `draft` whose value differs from `initial` (compared as JSON, so arrays and "" clears count).
export function changedFields(initial, draft) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

// A refreshed baseline arrives while the user is editing: fields the user did not touch follow the new baseline, touched ones stay.
export function rebaseForm(previousBase, nextBase, form) {
  const out = { ...nextBase };
  for (const key of Object.keys(form)) {
    if (JSON.stringify(form[key]) !== JSON.stringify(previousBase?.[key])) out[key] = form[key];
  }
  return out;
}

const SCALARS = ["recipes_dir", "models_dirs", "hf_cache", "show_system", "trash_dir", "remote_dir", "default_endpoint", "xid_watch", "xid_notify",
  "poll_s", "idle_poll_s", "history_min", "ssh_timeout_s", "xid_interval_s"];
const NUMBERS = ["poll_s", "idle_poll_s", "history_min", "ssh_timeout_s", "xid_interval_s"];
const NODE_KEYS = ["id", "name", "ssh", "enabled", "api_host", "host", "proxy_jump", "color"];

// The settings page form from what settings_get returned; every Spark row remembers the id it was loaded with (`_orig`).
export function settingsToForm(s) {
  return {
    nodes: (s.nodes || []).map((n) => ({ ...n, _orig: n.id })),
    recipes_dir: s.recipes_dir || "",
    models_dirs: (s.models_dirs || []).join("\n"),
    hf_cache: !!s.hf_cache,
    poll_s: s.poll_s, idle_poll_s: s.idle_poll_s, history_min: s.history_min, ssh_timeout_s: s.ssh_timeout_s,
    show_system: !!s.show_system,
    trash_dir: s.trash_dir || "",
    remote_dir: s.remote_dir || "",
    default_endpoint: s.default_endpoint || "",
    xid_watch: s.xid_watch !== false, xid_interval_s: s.xid_interval_s ?? 60, xid_notify: s.xid_notify !== false,
  };
}

// What would be sent: trimmed text, numbers as numbers, folder lists split. Spark rows keep their `_orig`.
export function normalizeSettingsForm(form) {
  const out = {
    nodes: (form.nodes || []).map((n) => ({
      _orig: n._orig,
      id: String(n.id ?? "").trim(), name: String(n.name ?? "").trim() || String(n.id ?? "").trim(), ssh: String(n.ssh ?? "").trim(), enabled: !!n.enabled,
      api_host: String(n.api_host || "").trim(), host: String(n.host || "").trim(), proxy_jump: String(n.proxy_jump || "").trim(), color: n.color || "",
    })),
    recipes_dir: String(form.recipes_dir ?? "").trim(),
    models_dirs: splitList(form.models_dirs),
    hf_cache: !!form.hf_cache,
    show_system: !!form.show_system,
    trash_dir: String(form.trash_dir ?? "").trim(),
    remote_dir: String(form.remote_dir ?? "").trim(),
    default_endpoint: form.default_endpoint || "",
    xid_watch: !!form.xid_watch,
    xid_notify: !!form.xid_notify,
  };
  for (const k of NUMBERS) out[k] = form[k] === "" || form[k] === null || form[k] === undefined ? form[k] : Number(form[k]);
  return out;
}

const withoutOrig = (node) => Object.fromEntries(NODE_KEYS.map((k) => [k, node[k]]));

// The settings_set arguments for what changed from the loaded settings: changed fields only, and Sparks as a per-Spark patch
// (nodes_patch / nodes_remove), so a Spark the assistant edited meanwhile is not overwritten by the whole list.
export function settingsPatch(baseForm, form) {
  const before = normalizeSettingsForm(baseForm);
  const after = normalizeSettingsForm(form);
  const out = changedFields(Object.fromEntries(SCALARS.map((k) => [k, before[k]])), Object.fromEntries(SCALARS.map((k) => [k, after[k]])));
  const baseIds = new Set(before.nodes.map((n) => n._orig));
  const keptIds = new Set(after.nodes.map((n) => n._orig).filter(Boolean));
  const patch = {};
  for (const row of after.nodes) {
    if (row._orig && baseIds.has(row._orig)) {
      const diff = changedFields(withoutOrig(before.nodes.find((n) => n._orig === row._orig)), withoutOrig(row));
      if (Object.keys(diff).length) patch[row._orig] = diff;
    } else {
      patch[row.id] = withoutOrig(row);
    }
  }
  const removed = [...baseIds].filter((id) => !keptIds.has(id));
  if (Object.keys(patch).length) out.nodes_patch = patch;
  if (removed.length) out.nodes_remove = removed;
  return out;
}

// A refreshed baseline arrives while the settings form is open: untouched fields and Sparks follow it, typed ones stay,
// Sparks the person added stay, Sparks the assistant added appear.
export function rebaseSettingsForm(previousBase, nextBase, form) {
  const { nodes: formNodes, ...formRest } = form;
  const { nodes: prevNodes, ...prevRest } = previousBase;
  const { nodes: nextNodes, ...nextRest } = nextBase;
  const out = rebaseForm(prevRest, nextRest, formRest);
  const nodes = [];
  const used = new Set();
  for (const row of formNodes || []) {
    if (!row._orig) { nodes.push(row); continue; }
    const next = (nextNodes || []).find((n) => n._orig === row._orig);
    if (!next) continue;                                      // removed meanwhile
    used.add(row._orig);
    const prev = (prevNodes || []).find((n) => n._orig === row._orig);
    nodes.push({ ...rebaseForm(prev, next, row), _orig: row._orig });
  }
  const known = new Set((prevNodes || []).map((n) => n._orig));
  for (const n of nextNodes || []) if (!used.has(n._orig) && !known.has(n._orig)) nodes.push(n);   // added meanwhile
  return { ...out, nodes };
}

// ---- route and editors ----------------------------------------------------------------------------------------------------
export function parseRoute(hash) {
  const [path, query = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { page: parts[0] || "", param: parts[1] || null, query: new URLSearchParams(query) };
}

// Every open form answers this event with a snapshot of what the user has typed: { kind, object_id, dirty, fields }.
export function collectEditors(target = globalThis.window) {
  const found = [];
  if (!target?.dispatchEvent) return found;
  target.dispatchEvent(new CustomEvent("prometheus:context-request", { detail: { provide: (snapshot) => { if (snapshot) found.push(snapshot); } } }));
  return found.sort((a, b) => (EDITOR_ORDER[a.kind] ?? 9) - (EDITOR_ORDER[b.kind] ?? 9));
}

// ---- objects --------------------------------------------------------------------------------------------------------------
const pick = (source, keys) => Object.fromEntries(keys.filter((k) => source?.[k] !== undefined && source[k] !== null && source[k] !== "").map((k) => [k, source[k]]));
const sparkObject = (n) => ({
  kind: "spark", object_id: n.id, title: n.name || n.id,
  ...pick(n, ["online", "power_state", "enabled", "hostname", "error", "ssh", "api_host", "host", "proxy_jump"]),
  ...(n.gpu?.util !== undefined ? { gpu_util: n.gpu.util } : {}),
  ...(n.memory?.percent !== undefined ? { memory_percent: n.memory.percent } : {}),
});
const deploymentObject = (d) => ({
  kind: "deployment", object_id: d.recipe, title: d.title || d.recipe,
  ...pick(d, ["state", "nodes", "head", "base_url", "served_model_name", "engine", "step", "message"]),
  ...(d.health ? { answering: !!d.health.answering } : {}),
});
const settingsObject = (s) => ({ kind: "settings", object_id: "settings", title: "Settings", ...pick(s, ["recipes_dir", "models_dirs", "default_endpoint", "hf_cache", "poll_s", "idle_poll_s", "xid_watch"]) });
const isLive = (d) => d && d.recipe && d.state && d.state !== "stopped";

async function safe(fn) {
  try { return await fn(); } catch { return null; }
}

// Pure core (dependencies injected so it can be tested): never throws; unknown ids yield no object.
export async function buildContext({ hash, editors = [], getOverview, getSettings }) {
  const route = parseRoute(hash);
  const page = PAGE_NAMES[route.page] ?? (route.page || "cluster");
  const active = [];
  const seen = new Set();
  const add = (object) => { const key = `${object.kind}:${object.object_id}`; if (object.object_id && !seen.has(key)) { seen.add(key); active.push(object); } };
  const view = { page };

  const overview = ["", "spark", "sirviendo", "archivos", "modelos"].includes(route.page) ? await safe(() => getOverview()) : null;
  const nodes = Array.isArray(overview?.nodes) ? overview.nodes : [];
  const live = (Array.isArray(overview?.deployments) ? overview.deployments : []).filter(isLive);

  if (route.page === "") {
    nodes.slice(0, MAX_SPARKS).forEach((n) => n?.id && add(sparkObject(n)));
    live.slice(0, MAX_DEPLOYMENTS).forEach((d) => add(deploymentObject(d)));
  } else if (route.page === "spark") {
    const node = nodes.find((n) => n?.id === route.param);
    if (node) {
      view.object_id = node.id;
      add(sparkObject(node));
      live.filter((d) => (d.nodes || []).includes(node.id)).slice(0, MAX_DEPLOYMENTS).forEach((d) => add(deploymentObject(d)));
    }
  } else if (route.page === "sirviendo") {
    live.slice(0, MAX_DEPLOYMENTS).forEach((d) => add(deploymentObject(d)));
  } else if (route.page === "archivos") {
    const node = nodes.find((n) => n?.id === route.param);
    if (node) {
      view.object_id = node.id;
      add(sparkObject(node));
      view.path = route.query.get("path") || "~";
      if (route.query.get("view") === "trash") view.listing = "trash";
    }
  } else if (route.page === "modelos") {
    live.slice(0, MAX_DEPLOYMENTS).forEach((d) => add(deploymentObject(d)));
    if (route.query.get("s")) view.section = route.query.get("s");
  } else if (route.page === "ajustes") {
    const settings = await safe(() => getSettings());
    if (settings && Array.isArray(settings.nodes)) {
      add(settingsObject(settings));
      settings.nodes.slice(0, MAX_SPARKS).forEach((n) => n?.id && add(sparkObject(n)));
    }
  }

  for (const e of editors) {
    if (e.kind === "client-name-form" && e.object_id && !seen.has(`client:${e.object_id}`)) add({ kind: "client", object_id: e.object_id, title: e.title || e.object_id });
  }
  const editor = editors.find((e) => e.dirty) || editors[0];
  if (editor) view.editor = editor;
  if (editors.length > 1) view.editors = editors;
  return { active_objects: active, view };
}

export function context() {
  return buildContext({
    hash: globalThis.window?.location?.hash,
    editors: collectEditors(),
    getOverview: () => api.overview(false),
    getSettings: () => api.call("settings_get"),
  });
}

// ---- refresh when the assistant finishes a turn -------------------------------------------------------------------------
const refreshHandlers = new Set();
let timer = null;

function runRefresh() {
  for (const fn of [...refreshHandlers]) {
    try { Promise.resolve(fn()).catch(() => {}); } catch { /* a refresh must never break the page */ }
  }
}

export function scheduleRefresh() {
  clearTimeout(timer);
  timer = setTimeout(runRefresh, REFRESH_DEBOUNCE_MS);
}

// Pages and the shell register what "reload what is on screen" means for them (it keeps the forms the user is editing untouched).
export function onCopilotRefresh(fn) {
  refreshHandlers.add(fn);
  return () => refreshHandlers.delete(fn);
}

export const onTurnEnd = () => scheduleRefresh();

// Once at start: the object the launcher looks for, and the window event it dispatches on turn end (the debounce merges the two).
export function installCopilot(target = globalThis.window) {
  if (!target || target.hoardCopilot) return;
  target.hoardCopilot = { context, onTurnEnd };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
}
