// Patterns are literal lines for the native downloader, not a client-side file filter.
export function downloadRequest({ node, repo, name, revision, patterns }) {
  const include = patterns.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return { node, repo: repo.trim(), name: name.trim(), revision: revision.trim(), ...(include.length ? { include } : {}) };
}
