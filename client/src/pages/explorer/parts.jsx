import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../context.js";
import { bytes, clock, dirname, ext, num } from "../../format.js";
import { Busy, Chip, ErrorBox, Field, Icon, ICONS, KindIcon, Modal, Spinner, useBusy, useLoad, useNarrow } from "../../components/ui.jsx";

const TEXT_EXT = new Set(["txt", "md", "json", "jsonl", "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "csv", "tsv", "env", "xml", "html", "htm", "css",
  "sh", "bash", "zsh", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "rs", "go", "c", "h", "cpp", "hpp", "java", "rb", "lua", "sql", "jinja", "j2", "svg", "service", "rst", "tex"]);
const PDF_EXT = new Set(["pdf"]);

/** What the preview pane can do with an entry: "text" | "image" | "video" | "audio" | "pdf" | "folder" | "none". */
export function previewKind(e) {
  if (!e) return "none";
  if (e.dir) return "folder";
  const x = ext(e.name);
  if (x === "svg") return "text";
  if (e.kind === "image") return "image";
  if (e.kind === "video") return "video";
  if (e.kind === "audio") return "audio";
  if (PDF_EXT.has(x)) return "pdf";
  if (e.kind === "text" || e.kind === "code" || TEXT_EXT.has(x)) return "text";
  if (e.kind === "file" && (e.size || 0) < 256 * 1024) return "text";   // small unknown files: try as text (the server says if it is binary)
  return "none";
}

const KIND_EXT = {
  weights: ["safetensors", "gguf", "bin", "pt", "pth"], image: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"], video: ["mp4", "mkv", "webm", "mov"],
  audio: ["wav", "mp3", "flac", "ogg"], archive: ["zip", "tar", "gz", "tgz", "xz", "zst", "7z"], code: ["py", "sh", "js", "ts", "tsx", "jsx", "rs", "go", "c", "cpp", "h", "java"],
  text: ["txt", "md", "json", "yaml", "yml", "toml", "log", "csv", "ini", "cfg"],
};
/** A kind guessed from a file name (for the trash, which lists names only). */
export function kindOfName(name) {
  const x = ext(name);
  if (!x) return "folder";
  for (const [k, list] of Object.entries(KIND_EXT)) if (list.includes(x)) return k;
  return "file";
}

export function typeLabel(e, t) {
  if (e.dir) return e.link ? t("kind_folder_link") : t("kind_folder");
  const x = ext(e.name);
  const base = t(`kind_${e.kind || "file"}`);
  return x ? `${base} (.${x})` : base;
}

function prettyJson(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return null;
  }
}

/** The right-hand preview: text with line numbers (editable), images, video, audio, PDF, or the facts of the item. */
export function Preview({ node, entry, entries, writable, onOpen, onSaved, onClose, standalone }) {
  const { t, lang, notify } = useApp();
  const kind = previewKind(entry);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [raw, setRaw] = useState(false);
  const [busy, run] = useBusy();
  const text = useLoad(() => (kind === "text" ? api.call("file_read", { node, path: entry.path, max_bytes: 400_000 }) : Promise.resolve(null)), [node, entry?.path, entry?.mtime, kind]);
  useEffect(() => { setEditing(false); setRaw(false); }, [entry?.path]);

  if (!entry) {
    const many = (entries || []).length;
    return (
      <div className="preview-empty">
        <Icon d={ICONS.eye} size={28} />
        {many > 1 ? (
          <>
            <b>{t("n_selected", { n: many })}</b>
            <span className="help">{bytes(entries.filter((e) => !e.dir).reduce((s, e) => s + (e.size || 0), 0), lang)}</span>
          </>
        ) : <span className="help">{t("preview_pick")}</span>}
      </div>
    );
  }

  const url = api.rawUrl(node, entry.path);
  const data = text.data;
  const isJson = ext(entry.name) === "json";
  const pretty = isJson && data?.text ? prettyJson(data.text) : null;
  const shown = data ? (pretty && !raw ? pretty : data.text) : "";
  const save = () => run("save", async () => {
    await api.call("file_write", { node, path: entry.path, content: draft, overwrite: true });
    notify(t("saved_file", { name: entry.name }));
    setEditing(false);
    text.reload();
    onSaved?.();
  });
  const canEdit = kind === "text" && writable && data && !data.binary && !data.truncated;

  return (
    <div className={`preview ${standalone ? "preview-standalone" : ""}`}>
      <div className="preview-head">
        <KindIcon kind={entry.kind} size={22} />
        <div className="min-w-0 flex-1">
          <div className="preview-name">{entry.name}</div>
          <div className="help">{typeLabel(entry, t)}{!entry.dir && ` · ${bytes(entry.size, lang)}`}</div>
        </div>
        {onClose && <button type="button" className="btn btn-sm btn-icon btn-ghost" onClick={onClose} aria-label={t("close_preview")}><Icon d={ICONS.close} size={14} /></button>}
      </div>
      <div className="preview-tools">
        {kind === "text" && !editing && canEdit && <button type="button" className="btn btn-sm" onClick={() => { setDraft(data.text); setEditing(true); }}><Icon d={ICONS.pencil} size={13} />{t("edit")}</button>}
        {editing && <Busy className="btn btn-sm btn-primary" busy={busy.save} onClick={save}><Icon d={ICONS.save} size={13} />{t("save")}</Busy>}
        {editing && <button type="button" className="btn btn-sm" onClick={() => setEditing(false)}>{t("cancel")}</button>}
        {pretty && !editing && <button type="button" className="btn btn-sm" aria-pressed={!raw} onClick={() => setRaw(!raw)}>{raw ? t("json_pretty") : t("json_raw")}</button>}
        {entry.dir && <button type="button" className="btn btn-sm" onClick={() => onOpen(entry)}><Icon d={ICONS.folder} size={13} />{t("open")}</button>}
        {!entry.dir && <a className="btn btn-sm" href={api.downloadUrl(node, entry.path)} download={entry.name}><Icon d={ICONS.download} size={13} />{t("download")}</a>}
        {(kind === "image" || kind === "pdf" || kind === "video" || kind === "audio") && <a className="btn btn-sm" href={url} target="_blank" rel="noreferrer"><Icon d={ICONS.external} size={13} />{t("open_tab")}</a>}
      </div>
      <div className="preview-body">
        {kind === "image" && <div className="preview-media"><img src={url} alt={entry.name} /></div>}
        {kind === "video" && <div className="preview-media"><video src={url} controls preload="metadata" /></div>}
        {kind === "audio" && <div className="preview-media p-3"><audio src={url} controls preload="metadata" className="w-full" /></div>}
        {kind === "pdf" && <iframe className="preview-pdf" src={url} title={entry.name} />}
        {kind === "text" && (text.loading && !data ? <div className="p-3 help flex items-center gap-2"><Spinner />{t("loading")}</div>
          : text.error ? <div className="p-2"><ErrorBox error={text.error} onRetry={text.reload} /></div>
            : data?.binary ? <NoPreview entry={entry} t={t} lang={lang} reason={t("binary_file")} />
              : editing ? <textarea className="editor" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} autoFocus
                onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); } e.stopPropagation(); }} />
                : <CodeView text={shown} truncated={data?.truncated} t={t} />)}
        {(kind === "none" || kind === "folder") && <NoPreview entry={entry} t={t} lang={lang} reason={kind === "folder" ? "" : t("no_preview")} />}
      </div>
    </div>
  );
}

function NoPreview({ entry, t, lang, reason }) {
  return (
    <div className="preview-facts">
      <div className="preview-bigicon"><KindIcon kind={entry.kind} size={56} /></div>
      {reason && <p className="help text-center">{reason}</p>}
      <dl className="props">
        <div className="prop"><dt>{t("col_type")}</dt><dd>{typeLabel(entry, t)}</dd></div>
        {!entry.dir && <div className="prop"><dt>{t("col_size")}</dt><dd>{bytes(entry.size, lang)} <span className="help">({num(entry.size, 0, lang)} B)</span></dd></div>}
        <div className="prop"><dt>{t("col_modified")}</dt><dd>{clock(entry.mtime, lang)}</dd></div>
        <div className="prop"><dt>{t("location")}</dt><dd className="mono">{dirname(entry.path)}</dd></div>
        {entry.mode && <div className="prop"><dt>{t("permissions")}</dt><dd className="mono">{entry.mode}</dd></div>}
      </dl>
    </div>
  );
}

function CodeView({ text, truncated, t }) {
  const lines = useMemo(() => (text || "").split("\n"), [text]);
  if (!text) return <p className="p-3 help">{t("empty_file")}</p>;
  return (
    <div className="code">
      <pre className="code-gutter" aria-hidden="true">{lines.map((_, i) => `${i + 1}\n`).join("")}</pre>
      <pre className="code-text">{text}</pre>
      {truncated && <div className="code-trunc">{t("truncated")}</div>}
    </div>
  );
}

/** Properties of one item (folder size included) or a summary of several. */
export function Properties({ node, nodeName, entries, onClose }) {
  const { t, lang } = useApp();
  const one = entries.length === 1 ? entries[0] : null;
  const info = useLoad(() => (one ? api.call("file_info", { node, path: one.path }) : Promise.resolve(null)), [node, one?.path]);
  const d = info.data || one;
  return (
    <Modal title={one ? t("props_of", { name: one.name }) : t("props_many", { n: entries.length })} onClose={onClose}
      footer={<button type="button" className="btn btn-primary" onClick={onClose}>{t("ok")}</button>}>
      {one ? (
        <>
          <div className="flex items-center gap-3">
            <KindIcon kind={one.kind} size={36} />
            <div className="min-w-0"><div className="font-semibold" style={{ overflowWrap: "anywhere" }}>{one.name}</div><div className="help">{typeLabel(one, t)}</div></div>
          </div>
          <dl className="props">
            <div className="prop"><dt>{t("location")}</dt><dd className="mono">{nodeName} · {dirname(one.path)}</dd></div>
            <div className="prop"><dt>{t("col_size")}</dt><dd>
              {one.dir ? (info.loading ? <span className="inline-flex items-center gap-2"><Spinner />{t("calculating")}</span> : d?.bytes != null ? <>{bytes(d.bytes, lang)} <span className="help">({num(d.bytes, 0, lang)} B)</span></> : "—")
                : <>{bytes(one.size, lang)} <span className="help">({num(one.size, 0, lang)} B)</span></>}
            </dd></div>
            <div className="prop"><dt>{t("col_modified")}</dt><dd>{clock(d?.mtime, lang)}</dd></div>
            <div className="prop"><dt>{t("permissions")}</dt><dd className="mono">{d?.mode || "—"}</dd></div>
            {d?.link && <div className="prop"><dt>{t("link")}</dt><dd>{t("yes")}</dd></div>}
            <div className="prop"><dt>{t("full_path")}</dt><dd className="mono">{one.path}</dd></div>
          </dl>
          {info.error && <ErrorBox error={info.error} />}
        </>
      ) : (
        <dl className="props">
          <div className="prop"><dt>{t("contents")}</dt><dd>{t("n_files_folders", { files: entries.filter((e) => !e.dir).length, folders: entries.filter((e) => e.dir).length })}</dd></div>
          <div className="prop"><dt>{t("size_files")}</dt><dd>{bytes(entries.filter((e) => !e.dir).reduce((s, e) => s + (e.size || 0), 0), lang)}</dd></div>
          <div className="prop"><dt>{t("location")}</dt><dd className="mono">{nodeName} · {dirname(entries[0]?.path)}</dd></div>
        </dl>
      )}
    </Modal>
  );
}

/** "Send to another Spark…": a files_transfer job to the chosen Sparks. */
export function TransferDialog({ node, entries, nodes, onClose, onDone }) {
  const { t, notify } = useApp();
  const others = nodes.filter((n) => n.id !== node);
  const [to, setTo] = useState(() => others.filter((n) => n.online).map((n) => n.id).slice(0, 1));
  const [dest, setDest] = useState("");
  const [busy, run] = useBusy();
  const src = nodes.find((n) => n.id === node);
  const send = () => run("send", async () => {
    const res = await api.call("files_transfer", { src_node: node, paths: entries.map((e) => e.path), dst_nodes: to, dest: dest.trim() });
    notify(t("transfer_started", { n: (res.jobs || []).length }), "ok", { label: t("see_jobs"), href: "#/modelos?s=jobs" });
    onDone?.();
    onClose();
  });
  return (
    <Modal title={t("send_to_title", { n: entries.length })} onClose={onClose}
      footer={<><button type="button" className="btn" onClick={onClose}>{t("cancel")}</button>
        <Busy className="btn btn-primary" busy={busy.send} disabled={!to.length} onClick={send}><Icon d={ICONS.send} size={14} />{t("send")}</Busy></>}>
      <p className="help">{t("send_to_help", { src: src?.name || node })}</p>
      <div className="pick-list">
        {entries.slice(0, 6).map((e) => <div key={e.path} className="flex items-center gap-2 min-w-0"><KindIcon kind={e.kind} size={15} /><span className="trunc">{e.name}</span></div>)}
        {entries.length > 6 && <div className="help">{t("and_n_more", { n: entries.length - 6 })}</div>}
      </div>
      <fieldset className="space-y-1.5">
        <legend className="label">{t("dest_sparks")}</legend>
        {others.length ? others.map((n) => (
          <label key={n.id} className={`check-row ${!n.online ? "opacity-50" : ""}`}>
            <input type="checkbox" checked={to.includes(n.id)} disabled={!n.online} onChange={(e) => setTo(e.target.checked ? [...to, n.id] : to.filter((x) => x !== n.id))} />
            <Icon d={ICONS.computer} size={15} />
            <span>{n.name}</span>
            {!n.online && <Chip>{t("ps_unreachable")}</Chip>}
          </label>
        )) : <p className="help">{t("no_other_sparks")}</p>}
      </fieldset>
      <Field label={t("dest_folder")} hint={t("dest_folder_help")}>
        <input className="field mono" value={dest} onChange={(e) => setDest(e.target.value)} placeholder={dirname(entries[0]?.path || "")} />
      </Field>
    </Modal>
  );
}

/** The trash of one Spark: restore, delete for good, empty. */
export function TrashView({ node, nodeName }) {
  const { t, lang, notify, confirm } = useApp();
  const narrow = useNarrow();
  const list = useLoad(() => api.call("trash_list", { node }), [node]);
  const [sel, setSel] = useState(new Set());
  const [busy, run] = useBusy();
  const items = list.data?.items || [];
  const total = items.reduce((s, i) => s + (i.bytes || 0), 0);
  const ids = [...sel];
  useEffect(() => { setSel(new Set()); }, [node]);
  const toggle = (id, multi) => {
    const next = new Set(multi ? sel : []);
    if (multi && next.has(id)) next.delete(id); else next.add(id);
    setSel(next);
  };
  const restore = (which) => run("restore", async () => {
    const res = await api.call("trash_restore", { node, ids: which });
    notify(t("restored_n", { n: (res.restored || which).length }));
    setSel(new Set());
    list.reload();
  });
  const purge = async (which) => {
    const ok = await confirm({ title: which ? t("purge_some_title", { n: which.length }) : t("empty_trash_title", { name: nodeName }), message: t("purge_msg"), confirmLabel: which ? t("delete_forever") : t("empty_trash") });
    if (!ok) return;
    await run("purge", async () => {
      const res = await api.call("trash_empty", { node, confirm: true, ...(which ? { ids: which } : {}) });
      notify(t("purged_n", { n: res.emptied ?? 0 }));
      setSel(new Set());
      list.reload();
    });
  };
  return (
    <div className="trash">
      <div className="trash-bar">
        <Busy className="btn btn-sm" busy={busy.restore} disabled={!ids.length} onClick={() => restore(ids)}><Icon d={ICONS.restore} size={14} />{t("restore")}</Busy>
        <Busy className="btn btn-sm btn-danger" busy={busy.purge} disabled={!ids.length} onClick={() => purge(ids)}><Icon d={ICONS.trash} size={14} />{t("delete_forever")}</Busy>
        <span className="flex-1" />
        <button type="button" className="btn btn-sm" onClick={list.reload}><Icon d={ICONS.refresh} size={14} /><span className="hide-sm">{t("refresh")}</span></button>
        <Busy className="btn btn-sm btn-danger" busy={busy.purge} disabled={!items.length} onClick={() => purge(null)}><Icon d={ICONS.trash} size={14} />{t("empty_trash")}</Busy>
      </div>
      {list.error && <div className="p-3"><ErrorBox error={list.error} onRetry={list.reload} /></div>}
      {list.loading && !list.data ? <div className="p-4 help flex items-center gap-2"><Spinner />{t("loading")}</div>
        : !items.length ? <div className="empty"><Icon d={ICONS.trash} size={30} /><b>{t("trash_empty")}</b><span className="help">{t("trash_empty_help")}</span></div>
          : narrow ? (
            <div className="scroll-x">
              {items.map((i) => (
                <div key={i.id} className={`trash-row ${sel.has(i.id) ? "is-selected" : ""}`} onClick={() => toggle(i.id, true)}>
                  <span className="flex justify-center"><input type="checkbox" checked={sel.has(i.id)} readOnly aria-label={i.names.join(", ")} tabIndex={-1} /></span>
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 min-w-0"><KindIcon kind={i.names.length === 1 ? kindOfName(i.names[0]) : "folder"} size={16} /><span style={{ overflowWrap: "anywhere" }}>{i.names.join(", ") || i.id}</span></span>
                    <span className="block help mono" style={{ overflowWrap: "anywhere" }}>{dirname(i.origin)}</span>
                    <span className="block help">{clock(i.deleted, lang)} · {bytes(i.bytes, lang)}</span>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="scroll-x">
              <table className="tbl trash-tbl">
                <thead><tr><th style={{ width: 28 }}><input type="checkbox" aria-label={t("select_all")} checked={sel.size === items.length} onChange={(e) => setSel(new Set(e.target.checked ? items.map((i) => i.id) : []))} /></th>
                  <th>{t("col_name")}</th><th>{t("original_location")}</th><th>{t("deleted_on")}</th><th className="r">{t("col_size")}</th></tr></thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id} className={sel.has(i.id) ? "is-selected" : ""} onClick={(e) => toggle(i.id, e.ctrlKey || e.metaKey)}>
                      <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={i.names.join(", ")} checked={sel.has(i.id)} onChange={() => toggle(i.id, true)} /></td>
                      <td><span className="inline-flex items-center gap-2 min-w-0"><KindIcon kind={i.names.length === 1 ? kindOfName(i.names[0]) : "folder"} size={16} /><span style={{ overflowWrap: "anywhere" }}>{i.names.join(", ") || i.id}</span></span></td>
                      <td className="mono help">{dirname(i.origin)}</td>
                      <td>{clock(i.deleted, lang)}</td>
                      <td className="r num">{bytes(i.bytes, lang)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      <div className="statusbar">
        <span>{t("n_items", { n: items.length })}</span>
        {sel.size > 0 && <span>{t("n_selected", { n: sel.size })}</span>}
        <span>{bytes(total, lang)}</span>
        {list.data?.trash && <span className="mono help ml-auto trunc">{list.data.trash}</span>}
      </div>
    </div>
  );
}
