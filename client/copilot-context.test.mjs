// node --test client/copilot-context.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildContext, changedFields, collectEditors, installCopilot, onCopilotRefresh, parseRoute, rebaseForm, rebaseSettingsForm, settingsPatch, settingsToForm,
} from "./src/copilot-context.js";

const settings = () => ({
  nodes: [
    { id: "spark1", name: "Spark1", ssh: "Spark1", enabled: true, api_host: "", host: "", proxy_jump: "", color: "" },
    { id: "spark2", name: "Spark2", ssh: "Spark2", enabled: true, api_host: "", host: "", proxy_jump: "", color: "" },
  ],
  recipes_dir: "/r", models_dirs: ["~/models"], hf_cache: true, poll_s: 2, idle_poll_s: 15, history_min: 30, ssh_timeout_s: 8,
  show_system: false, trash_dir: "~/.t", remote_dir: "~/sparks", default_endpoint: "", xid_watch: true, xid_interval_s: 60, xid_notify: true,
  hf_token: true, token_file: "C:/secret/mcp-token",
});
const overview = () => ({
  nodes: [
    { id: "spark1", name: "Spark1", online: true, power_state: "on", gpu: { util: 12 }, memory: { percent: 30 }, enabled: true },
    { id: "spark2", name: "Spark2", online: false, power_state: "off", enabled: true },
  ],
  deployments: [
    { recipe: "qwen", title: "Qwen", state: "running", nodes: ["spark1"], head: "spark1", base_url: "http://h:8000/v1", health: { answering: true } },
    { recipe: "glm", title: "GLM", state: "stopped", nodes: ["spark1", "spark2"] },
  ],
});

test("changedFields and rebaseForm", () => {
  assert.deepEqual(changedFields({ a: 1, b: [1] }, { a: 1, b: [1, 2], c: "" }), { b: [1, 2], c: "" });
  assert.deepEqual(rebaseForm({ a: 1, b: 2 }, { a: 5, b: 6 }, { a: 1, b: 9 }), { a: 5, b: 9 });
});

test("parseRoute", () => {
  assert.deepEqual({ ...parseRoute("#/archivos/spark%201?path=%7E%2Fm&view=trash"), query: undefined }, { page: "archivos", param: "spark 1", query: undefined });
  assert.equal(parseRoute("#/archivos/s?path=%7E%2Fm").query.get("path"), "~/m");
  assert.deepEqual({ ...parseRoute(""), query: undefined }, { page: "", param: null, query: undefined });
});

test("settingsPatch sends only what changed, as per-Spark patches", () => {
  const base = settingsToForm(settings());
  assert.deepEqual(settingsPatch(base, structuredClone(base)), {});
  const form = structuredClone(base);
  form.poll_s = "2";                      // typed text that equals the loaded number is no change
  form.trash_dir = " ~/.t ";             // trimmed
  assert.deepEqual(settingsPatch(base, form), {});
  form.poll_s = "5";
  form.models_dirs = "~/models\n/data";
  form.nodes[1].name = "  Worker ";
  form.nodes[1].color = "#abcdef";
  assert.deepEqual(settingsPatch(base, form), { poll_s: 5, models_dirs: ["~/models", "/data"], nodes_patch: { spark2: { name: "Worker", color: "#abcdef" } } });
});

test("settingsPatch adds, renames and removes Sparks", () => {
  const base = settingsToForm(settings());
  const form = structuredClone(base);
  form.nodes[0].id = "head";
  form.nodes.splice(1, 1);
  form.nodes.push({ id: "spark9", name: "", ssh: "Spark9", enabled: true, api_host: "", host: "", proxy_jump: "", color: "" });
  const patch = settingsPatch(base, form);
  assert.deepEqual(patch.nodes_patch.spark1, { id: "head" });
  assert.deepEqual(patch.nodes_patch.spark9, { id: "spark9", name: "spark9", ssh: "Spark9", enabled: true, api_host: "", host: "", proxy_jump: "", color: "" });
  assert.deepEqual(patch.nodes_remove, ["spark2"]);
  assert.equal("nodes" in patch, false);
});

test("rebaseSettingsForm keeps typed fields and follows the rest", () => {
  const prev = settingsToForm(settings());
  const form = structuredClone(prev);
  form.trash_dir = "~/mine";                               // typed
  form.nodes[0].name = "Typed";                            // typed
  form.nodes.push({ id: "new", name: "New", ssh: "new", enabled: true });   // added by the person
  const server = settings();
  server.poll_s = 9;                                       // changed by the assistant
  server.nodes[0].name = "Agent name";                     // conflicting: the typed one stays
  server.nodes[0].host = "agent.local";                    // untouched field: follows
  server.nodes.push({ id: "spark3", name: "Spark3", ssh: "Spark3", enabled: true, api_host: "", host: "", proxy_jump: "", color: "" });
  server.nodes.splice(1, 1);                               // spark2 removed by the assistant
  const next = settingsToForm(server);
  const out = rebaseSettingsForm(prev, next, form);
  assert.equal(out.poll_s, 9);
  assert.equal(out.trash_dir, "~/mine");
  assert.deepEqual(out.nodes.map((n) => n.id), ["spark1", "new", "spark3"]);
  assert.equal(out.nodes[0].name, "Typed");
  assert.equal(out.nodes[0].host, "agent.local");
  assert.equal(out.nodes[0]._orig, "spark1");
  // and the patch built from the rebased form only carries what the person did
  const patch = settingsPatch(next, out);
  assert.deepEqual(Object.keys(patch).sort(), ["nodes_patch", "trash_dir"]);
  assert.deepEqual(Object.keys(patch.nodes_patch).sort(), ["new", "spark1"]);
  assert.deepEqual(patch.nodes_patch.spark1, { name: "Typed" });
});

test("collectEditors gathers and orders the answers of open forms", () => {
  const target = new EventTarget();
  target.addEventListener("prometheus:context-request", (e) => e.detail.provide({ kind: "file-editor", object_id: "a", dirty: false }));
  target.addEventListener("prometheus:context-request", (e) => e.detail.provide({ kind: "settings-form", object_id: "settings", dirty: true, fields: { poll_s: 5 } }));
  target.addEventListener("prometheus:context-request", (e) => e.detail.provide(null));
  assert.deepEqual(collectEditors(target).map((e) => e.kind), ["settings-form", "file-editor"]);
  assert.deepEqual(collectEditors({}), []);
});

const deps = (over = {}) => ({ getOverview: async () => overview(), getSettings: async () => settings(), ...over });

test("cluster view lists the Sparks and the loaded deployments", async () => {
  const c = await buildContext({ hash: "#/", ...deps() });
  assert.equal(c.view.page, "cluster");
  assert.deepEqual(c.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["spark:spark1", "spark:spark2", "deployment:qwen"]);
  assert.equal(c.active_objects[0].gpu_util, 12);
  assert.equal(c.active_objects[2].answering, true);
});

test("spark view returns that Spark and its loaded deployments; unknown ids give none", async () => {
  const c = await buildContext({ hash: "#/spark/spark1", ...deps() });
  assert.equal(c.view.object_id, "spark1");
  assert.deepEqual(c.active_objects.map((o) => o.object_id), ["spark1", "qwen"]);
  const none = await buildContext({ hash: "#/spark/ghost", ...deps() });
  assert.deepEqual(none.active_objects, []);
  assert.equal(none.view.object_id, undefined);
});

test("files, models, serving views", async () => {
  const files = await buildContext({ hash: "#/archivos/spark2?path=%7E%2Fmodels&view=trash", ...deps() });
  assert.deepEqual(files.active_objects.map((o) => o.object_id), ["spark2"]);
  assert.deepEqual({ path: files.view.path, listing: files.view.listing }, { path: "~/models", listing: "trash" });
  const models = await buildContext({ hash: "#/modelos?s=disk", ...deps() });
  assert.deepEqual(models.active_objects.map((o) => o.object_id), ["qwen"]);
  assert.equal(models.view.section, "disk");
  const serving = await buildContext({ hash: "#/sirviendo", ...deps() });
  assert.equal(serving.view.page, "serving");
  assert.deepEqual(serving.active_objects.map((o) => o.kind), ["deployment"]);
});

test("settings view returns the settings and Sparks without secrets", async () => {
  const c = await buildContext({ hash: "#/ajustes", ...deps() });
  assert.equal(c.view.page, "settings");
  assert.deepEqual(c.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["settings:settings", "spark:spark1", "spark:spark2"]);
  const text = JSON.stringify(c);
  assert.equal(/token|secret/i.test(text), false);
});

test("fetch errors and bad data never throw and give no objects", async () => {
  const boom = async () => { throw new Error("down"); };
  for (const hash of ["#/", "#/spark/spark1", "#/archivos/spark1", "#/modelos", "#/sirviendo", "#/ajustes", "#/nope"]) {
    const c = await buildContext({ hash, getOverview: boom, getSettings: boom });
    assert.deepEqual(c.active_objects, []);
  }
  const odd = await buildContext({ hash: "#/", getOverview: async () => ({ nodes: "x", deployments: null }), getSettings: async () => null });
  assert.deepEqual(odd.active_objects, []);
});

test("editors reach the view; the client being renamed becomes an object", async () => {
  const editors = [
    { kind: "client-name-form", object_id: "192.0.2.4", title: "Torre", dirty: true, fields: { name: "Sala" } },
    { kind: "model-download-new", object_id: null, dirty: false, fields: {} },
  ];
  const c = await buildContext({ hash: "#/sirviendo", editors, ...deps() });
  assert.equal(c.view.editor.kind, "client-name-form");
  assert.equal(c.view.editors.length, 2);
  assert.ok(c.active_objects.some((o) => o.kind === "client" && o.object_id === "192.0.2.4"));
  assert.equal(c.active_objects.some((o) => o.object_id === null), false);
});

test("turn end: the event and the direct call refresh once, installCopilot is idempotent", async () => {
  const target = new EventTarget();
  installCopilot(target);
  const first = target.hoardCopilot;
  installCopilot(target);
  assert.equal(target.hoardCopilot, first);
  assert.equal(typeof first.context, "function");
  let calls = 0;
  const off = onCopilotRefresh(() => { calls += 1; });
  target.dispatchEvent(new Event("hoard:copilot-turn-end"));
  first.onTurnEnd();
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(calls, 1);
  off();
  first.onTurnEnd();
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(calls, 1);
});
