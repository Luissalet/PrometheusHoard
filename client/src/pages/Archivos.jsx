import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { bytes, clock, dirname, joinPath, num } from "../format.js";
import { Bar, ErrorBox, Icon, ICONS, KindIcon, Menu, MenuButton, Spinner, useLoad, useNarrow, useStored } from "../components/ui.jsx";
import { Preview, Properties, TransferDialog, TrashView, typeLabel } from "./explorer/parts.jsx";

// The clipboard outlives page changes (cut here, paste after a look at another page).
let CLIP = null;

const SORTERS = {
  name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }),
  mtime: (a, b) => (a.mtime || 0) - (b.mtime || 0),
  kind: (a, b) => (a.kind || "").localeCompare(b.kind || "") || a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }),
  size: (a, b) => (a.size || 0) - (b.size || 0),
};

function locHash(node, path, view) {
  if (view === "trash") return `#/archivos/${encodeURIComponent(node)}?view=trash`;
  return `#/archivos/${encodeURIComponent(node)}${path && path !== "~" ? `?path=${encodeURIComponent(path)}` : ""}`;
}

function uniqueName(base, extName, taken) {
  const names = new Set(taken.map((e) => e.name.toLowerCase()));
  const make = (i) => (i === 1 ? `${base}${extName}` : `${base} (${i})${extName}`);
  let i = 1;
  while (names.has(make(i).toLowerCase())) i += 1;
  return make(i);
}

export default function Archivos({ param, query }) {
  const { t, lang, ov, notify, toastError, confirm } = useApp();
  const narrow = useNarrow();
  const nodes = ov?.nodes || [];
  const fallback = nodes.find((n) => n.online)?.id || nodes[0]?.id;
  const node = param || fallback;
  const view = query.get("view") === "trash" ? "trash" : "files";
  const path = query.get("path") || "~";
  const nodeInfo = nodes.find((n) => n.id === node);
  const nodeName = nodeInfo?.name || node;

  // Without a Spark in the address, open the first one (replace, so Back does not bounce here).
  useEffect(() => {
    if (!param && fallback) window.location.replace(locHash(fallback, "~"));
  }, [param, fallback]);

  const [hidden, setHidden] = useStored("prom.hidden", false);
  const [mode, setMode] = useStored("prom.view", "details");
  const [showPane, setShowPane] = useStored("prom.preview", true);
  const [sort, setSort] = useStored("prom.sort", { key: "name", dir: 1 });
  const [sel, setSel] = useState(new Set());
  const [anchor, setAnchor] = useState(-1);
  const [focusIdx, setFocusIdx] = useState(-1);
  const [menu, setMenu] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [renameText, setRenameText] = useState("");
  const [editAddr, setEditAddr] = useState(false);
  const [addrText, setAddrText] = useState("");
  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState(null);         // {pattern, base, hits, truncated, loading, error}
  const [clip, setClipState] = useState(CLIP);
  const [dialog, setDialog] = useState(null);         // {kind: "props"|"send", entries}
  const [mobilePreview, setMobilePreview] = useState(null);
  const [upload, setUpload] = useState(null);         // {count, loaded, total}
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef(null);
  const listRef = useRef(null);
  const hist = useRef({ stack: [], index: -1 });
  const [, bump] = useState(0);
  const setClip = (c) => { CLIP = c; setClipState(c); };

  const settings = useLoad(() => api.call("settings_get"), []);
  const listing = useLoad(() => (node && view === "files" ? api.call("files_list", { node, path, hidden }) : Promise.resolve(null)), [node, path, hidden, view]);
  const data = listing.data && listing.data.node === node && !listing.error ? listing.data : null;
  const cwd = data?.path || "";
  const writable = !!data?.writable;

  // Back/forward history of this explorer (also follows the browser's own buttons).
  const here = `${node}|${view}|${path}`;
  useEffect(() => {
    const h = hist.current;
    if (h.stack[h.index] === here) return;
    if (h.stack[h.index - 1] === here) h.index -= 1;
    else if (h.stack[h.index + 1] === here) h.index += 1;
    else { h.stack = [...h.stack.slice(0, h.index + 1), here]; h.index = h.stack.length - 1; }
    bump((x) => x + 1);
  }, [here]);
  useEffect(() => { setSel(new Set()); setAnchor(-1); setFocusIdx(-1); setRenaming(null); setEditAddr(false); setSearch(null); setSearchText(""); }, [here]);

  const homeRef = useRef("");
  homeRef.current = data?.home || homeRef.current;
  const go = useCallback((n, p, v) => { window.location.hash = locHash(n, p && homeRef.current && p.replace(/\/+$/, "") === homeRef.current ? "~" : p, v); }, []);
  const goHist = (delta) => {
    const h = hist.current;
    const target = h.stack[h.index + delta];
    if (!target) return;
    const [n, v, p] = target.split("|");
    go(n, p, v);
  };
  const canBack = hist.current.index > 0;
  const canForward = hist.current.index < hist.current.stack.length - 1;
  const goUp = () => { if (view === "trash") go(node, "~"); else if (data?.parent && cwd !== data.home) go(node, data.parent); else if (data?.parent && settings.data?.show_system) go(node, data.parent); };
  const canUp = view === "trash" || (data?.parent && (cwd !== data?.home || settings.data?.show_system));

  // Entries as shown: search hits or the folder, sorted (folders first).
  const entries = useMemo(() => {
    const src = search ? (search.hits || []) : (data?.entries || []);
    const fn = SORTERS[sort.key] || SORTERS.name;
    const list = [...src].sort((a, b) => {
      if (a.dir !== b.dir) return a.dir ? -1 : 1;
      return fn(a, b) * sort.dir;
    });
    return list;
  }, [search, data, sort]);
  const selected = entries.filter((e) => sel.has(e.path));
  const single = selected.length === 1 ? selected[0] : null;

  const reload = () => { listing.reload(); if (search) runSearch(search.pattern); };

  // ------------------------------------------------------------------ selection
  const clickRow = (e, entry, idx) => {
    if (renaming) return;
    if (narrow && !e.ctrlKey && !e.shiftKey) {
      if (sel.size) { toggle(entry.path); setAnchor(idx); } else open(entry);
      return;
    }
    if (e.shiftKey && anchor >= 0) {
      const [a, b] = [Math.min(anchor, idx), Math.max(anchor, idx)];
      const range = entries.slice(a, b + 1).map((x) => x.path);
      setSel(new Set(e.ctrlKey || e.metaKey ? [...sel, ...range] : range));
    } else if (e.ctrlKey || e.metaKey) {
      toggle(entry.path);
      setAnchor(idx);
    } else {
      setSel(new Set([entry.path]));
      setAnchor(idx);
    }
    setFocusIdx(idx);
  };
  const toggle = (p) => setSel((cur) => { const next = new Set(cur); if (next.has(p)) next.delete(p); else next.add(p); return next; });
  const selectAll = () => setSel(new Set(entries.map((e) => e.path)));

  // ------------------------------------------------------------------ actions
  const open = (entry) => {
    if (!entry) return;
    if (entry.dir) { go(node, entry.path); return; }
    setSel(new Set([entry.path]));
    if (narrow) setMobilePreview(entry);
    else setShowPane(true);
  };
  const openLocation = (entry) => go(node, dirname(entry.path));

  const doCopy = (cut, list = selected) => {
    if (!list.length) return;
    setClip({ mode: cut ? "cut" : "copy", node, paths: list.map((e) => e.path), names: list.map((e) => e.name) });
    notify(t(cut ? "clip_cut" : "clip_copied", { n: list.length }));
  };
  const doPaste = async (destFolder = cwd) => {
    if (!clip || !destFolder) return;
    try {
      if (clip.node === node) {
        if (clip.mode === "cut") {
          await api.call("files_move", { node, paths: clip.paths, dest: destFolder });
          setClip(null);
          notify(t("moved_n", { n: clip.paths.length }));
        } else {
          await api.call("files_copy", { node, paths: clip.paths, dest: destFolder });
          notify(t("copied_n", { n: clip.paths.length }));
        }
      } else {
        const res = await api.call("files_transfer", { src_node: clip.node, paths: clip.paths, dst_nodes: [node], dest: destFolder });
        if (clip.mode === "cut") setClip(null);
        notify(t("transfer_started", { n: (res.jobs || []).length }), "ok", { label: t("see_jobs"), href: "#/modelos?s=jobs" });
      }
      reload();
    } catch (e) {
      toastError(e);
    }
  };
  const doDelete = async (permanent, list = selected) => {
    if (!list.length || !writable) return;
    if (permanent) {
      const ok = await confirm({
        title: list.length === 1 ? t("delete_forever_one", { name: list[0].name }) : t("delete_forever_many", { n: list.length }),
        message: t("delete_forever_msg"),
        confirmLabel: t("delete_forever"),
      });
      if (!ok) return;
    }
    try {
      await api.call("files_delete", { node, paths: list.map((e) => e.path), permanent, confirm: permanent });
      notify(permanent ? t("deleted_n", { n: list.length }) : t("trashed_n", { n: list.length }), "ok",
        permanent ? undefined : { label: t("see_trash"), href: locHash(node, "", "trash") });
      setSel(new Set());
      reload();
    } catch (e) {
      toastError(e);
    }
  };
  const startRename = (entry = single) => {
    if (!entry || !writable) return;
    setRenaming(entry.path);
    setRenameText(entry.name);
  };
  const commitRename = async () => {
    const entry = entries.find((e) => e.path === renaming);
    const name = renameText.trim();
    setRenaming(null);
    if (!entry || !name || name === entry.name) return;
    try {
      const res = await api.call("file_rename", { node, path: entry.path, new_name: name });
      await listing.reload();
      setSel(new Set([res.path]));
    } catch (e) {
      toastError(e);
    }
  };
  const newFolder = async () => {
    if (!writable) return;
    const name = uniqueName(t("new_folder_name"), "", data?.entries || []);
    try {
      const res = await api.call("folder_create", { node, path: joinPath(cwd, name) });
      await listing.reload();
      setSel(new Set([res.path]));
      setRenaming(res.path);
      setRenameText(name);
    } catch (e) {
      toastError(e);
    }
  };
  const newTextFile = async () => {
    if (!writable) return;
    const name = uniqueName(t("new_text_name"), ".txt", data?.entries || []);
    try {
      const res = await api.call("file_write", { node, path: joinPath(cwd, name), content: "", overwrite: false });
      await listing.reload();
      setSel(new Set([res.path]));
      setRenaming(res.path);
      setRenameText(name);
    } catch (e) {
      toastError(e);
    }
  };
  const doUpload = async (files) => {
    const list = [...(files || [])];
    if (!list.length || !writable) return;
    const total = list.reduce((s, f) => s + f.size, 0);
    setUpload({ count: list.length, loaded: 0, total });
    try {
      const res = await api.upload({ node, folder: cwd, files: list, onProgress: (loaded, tot) => setUpload({ count: list.length, loaded, total: tot || total }) });
      const up = res.uploaded || [];
      const renamed = up.filter((u, i) => list[i] && !u.path.endsWith(`/${list[i].name}`));
      notify(renamed.length ? t("uploaded_renamed", { n: up.length, names: renamed.map((u) => u.path.split("/").pop()).join(", ") }) : t("uploaded_n", { n: up.length }));
      await listing.reload();
      setSel(new Set(up.map((u) => u.path)));
    } catch (e) {
      toastError(e);
    } finally {
      setUpload(null);
    }
  };
  const runSearch = async (pattern) => {
    const p = (pattern || "").trim();
    if (!p) { setSearch(null); return; }
    setSearch({ pattern: p, base: cwd, hits: [], loading: true });
    setSel(new Set());
    try {
      const res = await api.call("files_search", { node, path: cwd || path, pattern: p, hidden });
      setSearch({ pattern: p, base: res.path, hits: res.hits || [], truncated: res.truncated, loading: false });
    } catch (e) {
      setSearch({ pattern: p, base: cwd, hits: [], loading: false, error: e });
    }
  };
  const copyPath = async (entry) => {
    try { await navigator.clipboard.writeText(entry.path); notify(t("path_copied")); } catch { notify(entry.path); }
  };

  // ------------------------------------------------------------------ menus
  const itemMenu = (entry) => {
    const list = sel.has(entry.path) ? selected : [entry];
    const one = list.length === 1 ? list[0] : null;
    const pasteInto = one && one.dir && clip;
    return [
      { label: t("open"), icon: one?.dir ? ICONS.folder : ICONS.eye, onClick: () => open(one), disabled: !one },
      search && { label: t("open_location"), icon: ICONS.folder, onClick: () => openLocation(one), disabled: !one },
      { label: t("download"), icon: ICONS.download, disabled: !one || one.dir, onClick: () => { const a = document.createElement("a"); a.href = api.downloadUrl(node, one.path); a.download = one.name; a.click(); } },
      "-",
      { label: t("cut"), icon: ICONS.cut, shortcut: "Ctrl+X", onClick: () => doCopy(true, list), disabled: !writable },
      { label: t("copy"), icon: ICONS.copy, shortcut: "Ctrl+C", onClick: () => doCopy(false, list) },
      { label: pasteInto ? t("paste_into", { name: one.name }) : t("paste"), icon: ICONS.paste, shortcut: "Ctrl+V", disabled: !clip || !writable, onClick: () => doPaste(pasteInto ? one.path : cwd) },
      { label: t("copy_path"), icon: ICONS.link, onClick: () => copyPath(one || list[0]) },
      "-",
      { label: t("rename"), icon: ICONS.pencil, shortcut: "F2", disabled: !one || !writable, onClick: () => startRename(one) },
      { label: t("delete"), icon: ICONS.trash, shortcut: t("key_del"), disabled: !writable, onClick: () => doDelete(false, list) },
      { label: t("delete_forever"), icon: ICONS.trash, shortcut: t("key_shift_del"), danger: true, disabled: !writable, onClick: () => doDelete(true, list) },
      "-",
      { label: t("send_to"), icon: ICONS.send, disabled: nodes.length < 2, onClick: () => setDialog({ kind: "send", entries: list }) },
      { label: t("properties"), icon: ICONS.info, onClick: () => setDialog({ kind: "props", entries: list }) },
    ];
  };
  const blankMenu = () => [
    { label: t("paste"), icon: ICONS.paste, shortcut: "Ctrl+V", disabled: !clip || !writable, onClick: () => doPaste(cwd) },
    "-",
    { label: t("new_folder"), icon: ICONS.folderPlus, disabled: !writable, onClick: newFolder },
    { label: t("new_text"), icon: ICONS.filePlus, disabled: !writable, onClick: newTextFile },
    { label: t("upload"), icon: ICONS.upload, disabled: !writable, onClick: () => fileInput.current?.click() },
    "-",
    { label: t("select_all"), icon: ICONS.selectAll, shortcut: "Ctrl+A", onClick: selectAll },
    { label: t("refresh"), icon: ICONS.refresh, onClick: reload },
    { label: t("properties"), icon: ICONS.info, disabled: !data, onClick: () => setDialog({ kind: "props", entries: [{ name: cwd.split("/").pop() || "/", path: cwd, dir: true, kind: "folder", mtime: null }] }) },
  ];
  const onRowContext = (e, entry, idx) => {
    e.preventDefault();
    e.stopPropagation();
    if (!sel.has(entry.path)) { setSel(new Set([entry.path])); setAnchor(idx); }
    setMenu({ at: { x: e.clientX, y: e.clientY }, items: itemMenu(entry) });
  };
  const onMore = (e, entry, idx) => {
    e.stopPropagation();
    const r = e.currentTarget.getBoundingClientRect();
    if (!sel.has(entry.path) && !narrow) { setSel(new Set([entry.path])); setAnchor(idx); }
    setMenu({ at: { x: r.right, y: r.bottom + 2, alignRight: true, above: r.top - 2 }, items: itemMenu(entry) });
  };

  // ------------------------------------------------------------------ keyboard
  const onKeyDown = (e) => {
    if (renaming || editAddr || view !== "files") return;
    const tag = e.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key;
    if (ctrl && k.toLowerCase() === "a") { e.preventDefault(); selectAll(); }
    else if (ctrl && k.toLowerCase() === "c") { e.preventDefault(); doCopy(false); }
    else if (ctrl && k.toLowerCase() === "x") { e.preventDefault(); if (writable) doCopy(true); }
    else if (ctrl && k.toLowerCase() === "v") { e.preventDefault(); if (writable) doPaste(); }
    else if (k === "F2") { e.preventDefault(); startRename(); }
    else if (k === "Delete") { e.preventDefault(); doDelete(e.shiftKey); }
    else if (k === "Backspace") { e.preventDefault(); goUp(); }
    else if (e.altKey && k === "ArrowLeft") { e.preventDefault(); goHist(-1); }
    else if (e.altKey && k === "ArrowRight") { e.preventDefault(); goHist(1); }
    else if (e.altKey && k === "ArrowUp") { e.preventDefault(); goUp(); }
    else if (k === "Enter") { if (single) { e.preventDefault(); open(single); } }
    else if (k === "Escape") { setSel(new Set()); }
    else if (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End") {
      if (!entries.length) return;
      e.preventDefault();
      const step = 1;
      let idx = focusIdx < 0 ? (k === "ArrowUp" ? entries.length : -1) : focusIdx;
      if (k === "ArrowDown") idx = Math.min(entries.length - 1, idx + step);
      else if (k === "ArrowUp") idx = Math.max(0, idx - step);
      else if (k === "Home") idx = 0;
      else idx = entries.length - 1;
      setFocusIdx(idx);
      if (e.shiftKey && anchor >= 0) {
        const [a, b] = [Math.min(anchor, idx), Math.max(anchor, idx)];
        setSel(new Set(entries.slice(a, b + 1).map((x) => x.path)));
      } else {
        setSel(new Set([entries[idx].path]));
        setAnchor(idx);
      }
      listRef.current?.querySelector(`[data-idx="${idx}"]`)?.scrollIntoView({ block: "nearest" });
    }
  };

  // ------------------------------------------------------------------ drag and drop upload
  const onDragOver = (e) => {
    if (!writable || view !== "files") return;
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setDragging(true);
  };
  const onDragLeave = (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); };
  const onDrop = (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    e.preventDefault();
    setDragging(false);
    doUpload(e.dataTransfer.files);
  };

  // ------------------------------------------------------------------ quick access and drives
  const s = settings.data;
  const quick = [
    { label: t("qa_home"), icon: ICONS.home, path: "~" },
    { label: t("qa_models"), icon: ICONS.models, path: (s?.models_dirs || ["~/models"])[0] || "~/models" },
    { label: t("qa_recipes"), icon: ICONS.recipes, path: `${(s?.remote_dir || "~/sparks").replace(/\/+$/, "")}/recipes` },
    { label: t("qa_trash"), icon: ICONS.trash, view: "trash" },
  ];
  const samePath = (p) => {
    if (!data) return false;
    const abs = p.replace(/^~/, data.home);
    return view === "files" && !search && abs === cwd;
  };

  const navPane = (
    <nav className="xp-nav" aria-label={t("nav_pane")}>
      <div className="xp-nav-title">{t("quick_access")}</div>
      {quick.map((q) => (
        <button key={q.label} type="button" className="xp-nav-item" aria-current={(q.view === "trash" ? view === "trash" : samePath(q.path)) ? "true" : undefined}
          onClick={() => { go(node, q.path || "~", q.view); }}>
          <Icon d={q.icon} size={16} />
          <span className="trunc">{q.label}</span>
        </button>
      ))}
      <div className="xp-nav-title">{t("this_cluster")}</div>
      {nodes.map((n) => {
        const disk = (n.disks || []).find((d) => d.path === "/") || (n.disks || [])[0];
        return (
          <button key={n.id} type="button" className={`xp-drive ${!n.online ? "is-offline" : ""}`} aria-current={n.id === node ? "true" : undefined}
            onClick={() => { go(n.id, "~"); }}>
            <span className="xp-drive-ico"><Icon d={ICONS.drive} size={22} /></span>
            <span className="min-w-0 flex-1">
              <span className="block trunc font-semibold">{n.name}{n.hostname ? <span className="help font-normal"> ({n.hostname})</span> : null}</span>
              {n.online && disk ? (
                <>
                  <Bar value={disk.total ? 1 - disk.free / disk.total : 0} thin />
                  <span className="block help trunc">{t("free_of", { free: bytes(disk.free, lang), total: bytes(disk.total, lang) })}</span>
                </>
              ) : <span className="block help">{t("ps_unreachable")}</span>}
            </span>
          </button>
        );
      })}
    </nav>
  );

  // ------------------------------------------------------------------ render
  if (!node) {
    return <div className="p-6 help flex items-center gap-2">{ov ? t("no_sparks") : <><Spinner />{t("loading")}</>}</div>;
  }

  const crumbs = data?.crumbs || [];
  const selSize = selected.filter((e) => !e.dir).reduce((acc, e) => acc + (e.size || 0), 0);
  const jobsRunning = (ov?.jobs || []).length;
  const previewEntry = single;
  const cutPaths = clip?.mode === "cut" && clip.node === node ? new Set(clip.paths) : null;
  const sortBy = (key) => setSort((cur) => ({ key, dir: cur.key === key ? -cur.dir : 1 }));
  const sortMark = (key) => (sort.key === key ? <Icon d={sort.dir > 0 ? ICONS.chevronDown : ICONS.arrowUp} size={11} /> : null);

  const header = (
    <div className="xp-cols" role="row">
      {narrow && <span />}
      {[["name", t("col_name")], ["mtime", t("col_modified")], ["kind", t("col_type")], ["size", t("col_size")]].map(([key, label]) => (
        <button key={key} type="button" role="columnheader" className={`xp-col xp-c-${key === "mtime" ? "date" : key === "kind" ? "type" : key} ${key === "size" ? "r" : ""}`} aria-sort={sort.key === key ? (sort.dir > 0 ? "ascending" : "descending") : "none"} onClick={() => sortBy(key)}>
          {label} {sortMark(key)}
        </button>
      ))}
      <span />
    </div>
  );

  const rows = entries.map((e, idx) => {
    const isSel = sel.has(e.path);
    const isRen = renaming === e.path;
    const nameEl = isRen ? (
      <input className="field xp-rename" value={renameText} autoFocus onChange={(ev) => setRenameText(ev.target.value)}
        onFocus={(ev) => { const dot = e.dir ? -1 : ev.target.value.lastIndexOf("."); ev.target.setSelectionRange(0, dot > 0 ? dot : ev.target.value.length); }}
        onKeyDown={(ev) => { ev.stopPropagation(); if (ev.key === "Enter") commitRename(); if (ev.key === "Escape") setRenaming(null); }}
        onBlur={commitRename} onClick={(ev) => ev.stopPropagation()} onDoubleClick={(ev) => ev.stopPropagation()} />
    ) : <span className="xp-name-text">{e.name}</span>;
    const common = {
      key: e.path,
      "data-idx": idx,
      role: "row",
      "aria-selected": isSel,
      className: `${mode === "icons" && !narrow ? "xp-tile" : "xp-row"} ${isSel ? "is-selected" : ""} ${idx === focusIdx ? "is-focus" : ""} ${cutPaths?.has(e.path) ? "is-cut" : ""} ${e.hidden ? "is-hidden" : ""}`,
      onClick: (ev) => clickRow(ev, e, idx),
      onDoubleClick: () => { if (!narrow) open(e); },
      onContextMenu: (ev) => onRowContext(ev, e, idx),
    };
    if (mode === "icons" && !narrow) {
      const thumb = e.kind === "image" && (e.size || 0) < 8 * 1024 * 1024 && !e.name.toLowerCase().endsWith(".svg");
      return (
        <div {...common}>
          <span className="xp-tile-ico">{thumb ? <img src={api.rawUrl(node, e.path)} alt="" loading="lazy" /> : <KindIcon kind={e.kind} size={44} />}</span>
          <span className="xp-tile-name">{nameEl}</span>
        </div>
      );
    }
    return (
      <div {...common}>
        {narrow && (
          <span className="xp-check" onClick={(ev) => { ev.stopPropagation(); toggle(e.path); setAnchor(idx); }}>
            <input type="checkbox" checked={isSel} readOnly aria-label={e.name} tabIndex={-1} />
          </span>
        )}
        <span className="xp-name"><KindIcon kind={e.kind} size={18} />{e.link && <span className="xp-link" aria-hidden="true">↗</span>}{nameEl}
          {search && <span className="xp-where help mono">{data?.home && dirname(e.path).startsWith(data.home) ? `~${dirname(e.path).slice(data.home.length)}` : dirname(e.path)}</span>}
          {narrow && <span className="xp-sub help">{clock(e.mtime, lang)}{!e.dir ? ` · ${bytes(e.size, lang)}` : ""}</span>}
        </span>
        {!narrow && <span className="xp-cell xp-c-date help">{clock(e.mtime, lang)}</span>}
        {!narrow && <span className="xp-cell xp-c-type help trunc">{typeLabel(e, t)}</span>}
        {!narrow && <span className="xp-cell r num help">{e.dir ? "" : bytes(e.size, lang)}</span>}
        <button type="button" className="xp-more btn-icon btn-ghost" aria-label={t("more_actions", { name: e.name })} onClick={(ev) => onMore(ev, e, idx)}>
          <Icon d={ICONS.more} size={16} strokeWidth={3} />
        </button>
      </div>
    );
  });

  const addressBar = (
    <div className={`xp-address ${editAddr ? "is-editing" : ""}`} onClick={() => { if (!editAddr && view === "files") { setAddrText(cwd || path); setEditAddr(true); } }}>
      {editAddr ? (
        <input className="xp-address-input mono" autoFocus value={addrText} aria-label={t("address")}
          onChange={(e) => setAddrText(e.target.value)} onBlur={() => setEditAddr(false)} onFocus={(e) => e.target.select()}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter") { setEditAddr(false); const p = addrText.trim(); if (p) go(node, p); }
            if (e.key === "Escape") setEditAddr(false);
          }} />
      ) : (
        <>
          <Icon d={ICONS.drive} size={15} className="shrink-0" />
          <button type="button" className="xp-crumb" onClick={(e) => { e.stopPropagation(); go(node, "~"); }}>{nodeName}</button>
          {view === "trash" ? (
            <><Icon d={ICONS.chevron} size={12} className="xp-sep" /><span className="xp-crumb is-last">{t("qa_trash")}</span></>
          ) : crumbs.map((c, i) => (
            <React.Fragment key={c.path}>
              <Icon d={ICONS.chevron} size={12} className="xp-sep" />
              <button type="button" className={`xp-crumb ${i === crumbs.length - 1 && !search ? "is-last" : ""}`} onClick={(e) => { e.stopPropagation(); go(node, c.path); }}>
                {c.name === "~" ? t("qa_home") : c.name}
              </button>
            </React.Fragment>
          ))}
          {view === "files" && !data && path !== "~" && <><Icon d={ICONS.chevron} size={12} className="xp-sep" /><span className="xp-crumb is-last mono">{path}</span></>}
          {search && <><Icon d={ICONS.chevron} size={12} className="xp-sep" /><span className="xp-crumb is-last">{t("search_results")}</span></>}
          <span className="flex-1" />
        </>
      )}
    </div>
  );

  const viewItems = [
    { label: t("view_details"), icon: ICONS.list, check: mode === "details", onClick: () => setMode("details") },
    { label: t("view_icons"), icon: ICONS.grid, check: mode === "icons", onClick: () => setMode("icons") },
    "-",
    { label: t("show_hidden"), icon: hidden ? ICONS.check : ICONS.eyeOff, onClick: () => setHidden(!hidden) },
    { label: t("preview_pane"), icon: showPane ? ICONS.check : ICONS.pane, onClick: () => setShowPane(!showPane) },
  ];
  const sortItems = [["name", t("col_name")], ["mtime", t("col_modified")], ["kind", t("col_type")], ["size", t("col_size")]].map(([key, label]) => ({
    label, icon: sort.key === key ? (sort.dir > 0 ? ICONS.chevronDown : ICONS.arrowUp) : undefined, onClick: () => sortBy(key),
  }));

  return (
    <div className={`xp ${showPane && !narrow && view === "files" ? "with-pane" : ""}`} onKeyDown={onKeyDown}>
      {/* --- top bar: history, address, search */}
      <div className="xp-top">
        <div className="xp-hist">
          <button type="button" className="btn-icon btn-ghost" onClick={() => goHist(-1)} disabled={!canBack} aria-label={t("back")}><Icon d={ICONS.arrowLeft} size={16} /></button>
          <button type="button" className="btn-icon btn-ghost" onClick={() => goHist(1)} disabled={!canForward} aria-label={t("forward")}><Icon d={ICONS.arrowRight} size={16} /></button>
          <button type="button" className="btn-icon btn-ghost" onClick={goUp} disabled={!canUp} aria-label={t("up")}><Icon d={ICONS.arrowUp} size={16} /></button>
          <button type="button" className="btn-icon btn-ghost" onClick={reload} aria-label={t("refresh")}><Icon d={ICONS.refresh} size={15} /></button>
        </div>
        {addressBar}
        {view === "files" && (
          <form className="xp-search" role="search" onSubmit={(e) => { e.preventDefault(); runSearch(searchText); }}>
            <Icon d={ICONS.search} size={14} />
            <input value={searchText} onChange={(e) => { setSearchText(e.target.value); if (!e.target.value) setSearch(null); }} placeholder={t("search_in", { name: crumbs.length ? (crumbs[crumbs.length - 1].name === "~" ? t("qa_home") : crumbs[crumbs.length - 1].name) : nodeName })}
              aria-label={t("search")} onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Escape") { setSearchText(""); setSearch(null); } }} />
            {searchText && <button type="button" className="btn-icon btn-ghost" aria-label={t("clear_search")} onClick={() => { setSearchText(""); setSearch(null); }}><Icon d={ICONS.close} size={13} /></button>}
          </form>
        )}
      </div>

      {/* --- command bar */}
      {view === "files" && (
        <div className="xp-cmd" role="toolbar" aria-label={t("commands")}>
          {narrow && (
            <MenuButton className="btn btn-sm" label={t("this_cluster")} items={[
              ...nodes.map((n) => ({ label: n.name, icon: ICONS.drive, check: n.id === node, disabled: !n.online, onClick: () => go(n.id, "~") })),
              "-",
              ...quick.map((q) => ({ label: q.label, icon: q.icon, onClick: () => go(node, q.path || "~", q.view) })),
            ]}>
              <Icon d={ICONS.drive} size={15} /><span>{nodeName}</span><Icon d={ICONS.chevronDown} size={12} />
            </MenuButton>
          )}
          <MenuButton className="btn btn-sm btn-primary" label={t("new")} disabled={!writable} items={[
            { label: t("new_folder"), icon: ICONS.folderPlus, onClick: newFolder },
            { label: t("new_text"), icon: ICONS.filePlus, onClick: newTextFile },
          ]}>
            <Icon d={ICONS.plus} size={15} /><span>{t("new")}</span><Icon d={ICONS.chevronDown} size={12} />
          </MenuButton>
          <button type="button" className="btn btn-sm" disabled={!writable || !!upload} onClick={() => fileInput.current?.click()}><Icon d={ICONS.upload} size={15} /><span className="hide-sm">{t("upload")}</span></button>
          <span className="xp-div" />
          <button type="button" className="btn btn-sm btn-icon" disabled={!selected.length || !writable} onClick={() => doCopy(true)} aria-label={t("cut")}><Icon d={ICONS.cut} size={15} /></button>
          <button type="button" className="btn btn-sm btn-icon" disabled={!selected.length} onClick={() => doCopy(false)} aria-label={t("copy")}><Icon d={ICONS.copy} size={15} /></button>
          <button type="button" className="btn btn-sm btn-icon" disabled={!clip || !writable} onClick={() => doPaste()} aria-label={t("paste")}><Icon d={ICONS.paste} size={15} /></button>
          <button type="button" className="btn btn-sm btn-icon" disabled={!single || !writable} onClick={() => startRename()} aria-label={t("rename")}><Icon d={ICONS.pencil} size={15} /></button>
          <button type="button" className="btn btn-sm btn-icon" disabled={!selected.length || !writable} onClick={() => doDelete(false)} aria-label={t("delete")}><Icon d={ICONS.trash} size={15} /></button>
          <span className="xp-div" />
          <button type="button" className="btn btn-sm" disabled={!selected.length || nodes.length < 2} onClick={() => setDialog({ kind: "send", entries: selected })}><Icon d={ICONS.send} size={15} /><span className="hide-sm">{t("send_to_short")}</span></button>
          <a className={`btn btn-sm ${!single || single.dir ? "is-disabled" : ""}`} aria-disabled={!single || single.dir} href={single && !single.dir ? api.downloadUrl(node, single.path) : undefined} download={single?.name}
            onClick={(e) => { if (!single || single.dir) e.preventDefault(); }}><Icon d={ICONS.download} size={15} /><span className="hide-sm">{t("download")}</span></a>
          <button type="button" className="btn btn-sm btn-icon" disabled={!selected.length} onClick={() => setDialog({ kind: "props", entries: selected })} aria-label={t("properties")}><Icon d={ICONS.info} size={15} /></button>
          <span className="flex-1" />
          {narrow && <MenuButton className="btn btn-sm" label={t("sort_by")} align="right" items={sortItems}><Icon d={ICONS.list} size={15} /><span>{t("sort")}</span></MenuButton>}
          <MenuButton className="btn btn-sm" label={t("view")} align="right" items={viewItems}>
            <Icon d={mode === "icons" ? ICONS.grid : ICONS.list} size={15} /><span className="hide-sm">{t("view")}</span><Icon d={ICONS.chevronDown} size={12} />
          </MenuButton>
          {!narrow && <button type="button" className="btn btn-sm btn-icon" aria-pressed={showPane} onClick={() => setShowPane(!showPane)} aria-label={t("preview_pane")}><Icon d={ICONS.pane} size={15} /></button>}
        </div>
      )}
      <input ref={fileInput} type="file" multiple hidden onChange={(e) => { doUpload(e.target.files); e.target.value = ""; }} />

      <div className="xp-body">
        {!narrow && navPane}
        {view === "trash" ? (
          <div className="xp-main"><TrashView node={node} nodeName={nodeName} /></div>
        ) : (
          <div className="xp-main" onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
            {data && !writable && (
              <div className="xp-banner"><Icon d={ICONS.lock} size={14} />{t("read_only", { home: data.home })}</div>
            )}
            {nodeInfo && !nodeInfo.online && <div className="xp-banner xp-banner-bad"><Icon d={ICONS.warn} size={14} />{t("node_offline_files", { name: nodeName })}</div>}
            <div className={`xp-list ${mode === "icons" && !narrow ? "is-icons" : "is-details"}`} ref={listRef} tabIndex={0} role="grid" aria-label={t("files_of", { name: nodeName })} aria-multiselectable="true"
              onClick={(e) => { if (e.target === e.currentTarget || e.target.classList.contains("xp-rows")) setSel(new Set()); }}
              onContextMenu={(e) => { if (e.target.closest(".xp-row, .xp-tile")) return; e.preventDefault(); setSel(new Set()); setMenu({ at: { x: e.clientX, y: e.clientY }, items: blankMenu() }); }}>
              {(mode === "details" || narrow) && entries.length > 0 && !narrow && header}
              {listing.error ? (
                <div className="p-4 space-y-3">
                  <ErrorBox error={listing.error} onRetry={listing.reload} />
                  <button type="button" className="btn" onClick={() => go(node, "~")}><Icon d={ICONS.home} size={15} />{t("go_home")}</button>
                </div>
              ) : (listing.loading && !data) || search?.loading ? (
                <div className="p-4 help flex items-center gap-2"><Spinner />{search?.loading ? t("searching") : t("loading")}</div>
              ) : search?.error ? (
                <div className="p-4"><ErrorBox error={search.error} /></div>
              ) : !entries.length ? (
                <div className="empty">
                  <Icon d={search ? ICONS.search : ICONS.folder} size={30} />
                  <b>{search ? t("no_results", { q: search.pattern }) : t("folder_empty")}</b>
                  {!search && writable && <span className="help">{t("folder_empty_help")}</span>}
                </div>
              ) : <div className="xp-rows">{rows}</div>}
              {search?.truncated && <p className="help p-2">{t("search_truncated")}</p>}
              {data?.truncated && !search && <p className="help p-2">{t("list_truncated", { n: num(data.count, 0, lang) })}</p>}
              {dragging && <div className="xp-drop"><Icon d={ICONS.upload} size={30} /><b>{t("drop_here", { folder: crumbs.length ? crumbs[crumbs.length - 1].name : cwd })}</b></div>}
            </div>
          </div>
        )}
        {showPane && !narrow && view === "files" && (
          <aside className="xp-pane" aria-label={t("preview_pane")}>
            <Preview node={node} entry={previewEntry} entries={selected} writable={writable} onOpen={open} onSaved={listing.reload} />
          </aside>
        )}
      </div>

      {view === "files" && (
        <div className="statusbar">
          <span>{search ? t("n_results", { n: entries.length }) : t("n_items", { n: entries.length })}</span>
          {selected.length > 0 && <span>{t("n_selected", { n: selected.length })}{selSize ? ` · ${bytes(selSize, lang)}` : ""}</span>}
          {clip && <span className="hide-sm">{t(clip.mode === "cut" ? "clip_state_cut" : "clip_state_copy", { n: clip.paths.length })}</span>}
          <span className="flex-1" />
          {upload && (
            <span className="xp-upload">
              <Spinner />
              {t("uploading", { n: upload.count })}
              <Bar value={upload.total ? upload.loaded / upload.total : 0} alarm={false} thin />
              <span className="num">{upload.total ? `${Math.round((upload.loaded / upload.total) * 100)} %` : ""}</span>
            </span>
          )}
          {jobsRunning > 0 && <a className="btn-link" href="#/modelos?s=jobs">{t("jobs_n", { n: jobsRunning })}</a>}
          {!writable && data && <span className="hide-sm inline-flex items-center gap-1"><Icon d={ICONS.lock} size={12} />{t("read_only_short")}</span>}
        </div>
      )}

      {menu && <Menu at={menu.at} items={menu.items} onClose={() => setMenu(null)} />}
      {dialog?.kind === "props" && <Properties node={node} nodeName={nodeName} entries={dialog.entries} onClose={() => setDialog(null)} />}
      {dialog?.kind === "send" && <TransferDialog node={node} entries={dialog.entries} nodes={nodes} onClose={() => setDialog(null)} />}
      {mobilePreview && narrow && (
        <div className="xp-sheet" role="dialog" aria-modal="true" aria-label={mobilePreview.name}>
          <Preview node={node} entry={mobilePreview} entries={[mobilePreview]} writable={writable} onOpen={open} onSaved={listing.reload} onClose={() => { setMobilePreview(null); setSel(new Set()); }} standalone />
        </div>
      )}
    </div>
  );
}
