// Thin fetch wrapper: JSON in/out. `{ error, code, hint, conflicts }` bodies become exceptions that keep those fields.
async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    const error = new Error(cause && cause.message ? cause.message : "Network error");
    error.code = "network";
    throw error;
  }
  return parse(response.status, response.ok, await response.text());
}

function parse(status, ok, text) {
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!ok) {
    const detail = data && (data.error || data.detail);
    const error = new Error((typeof detail === "string" && detail) || `Error ${status}`);
    error.code = (data && data.code) || (status === 0 ? "network" : "");
    error.hint = (data && data.hint) || "";
    error.conflicts = (data && data.conflicts) || [];
    error.status = status;
    throw error;
  }
  return data;
}

// Every write and most reads go through the same tool handlers the assistant uses.
const call = (name, args) => request("POST", "/api/ui/call", { body: { name, arguments: args || {} } });

const fileUrl = (kind, node, path) => `/api/files/${kind}?node=${encodeURIComponent(node)}&path=${encodeURIComponent(path)}`;

/** Upload files with XHR so the page can show progress. `onProgress(loaded, total)`. Resolves to {uploaded}. */
function upload({ node, folder, files, overwrite = false, onProgress }) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("node", node);
    form.append("folder", folder);
    form.append("overwrite", overwrite ? "true" : "false");
    for (const f of files) form.append("files", f, f.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/upload");
    xhr.upload.onprogress = (e) => { if (onProgress) onProgress(e.loaded, e.lengthComputable ? e.total : 0); };
    xhr.onload = () => {
      try { resolve(parse(xhr.status, xhr.status >= 200 && xhr.status < 300, xhr.responseText)); } catch (e) { reject(e); }
    };
    xhr.onerror = () => { const e = new Error("Network error"); e.code = "network"; reject(e); };
    xhr.send(form);
  });
}

export const api = {
  health: () => request("GET", "/api/health"),
  overview: (detail = false) => request("GET", "/api/overview", { params: { detail: detail ? 1 : 0 } }),
  history: (node, minutes = 10) => request("GET", `/api/nodes/${encodeURIComponent(node)}/history`, { params: { minutes } }),
  serving: () => request("GET", "/api/serving"),
  endpoints: () => request("GET", "/api/endpoints"),
  downloadUrl: (node, path) => fileUrl("download", node, path),
  rawUrl: (node, path) => fileUrl("raw", node, path),
  upload,
  call,
};
