// Numbers, sizes, dates and small helpers. es-ES by default, en-GB when English is chosen.
export const locale = (lang) => (lang === "en" ? "en-GB" : "es-ES");

export function num(value, digits = 0, lang = "es") {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
  // es-ES leaves 4-digit numbers without a group separator by default; "always" keeps 1.234 readable in tables.
  return Number(value).toLocaleString(locale(lang), { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: "always" });
}

export function pct(value, lang = "es", digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${num(value, digits, lang)} %`;
}

/** Bytes as "512 KB", "3,4 MB", "118 GB", "3,7 TB" (powers of 1024, names as Windows shows them). */
export function bytes(value, lang = "es", { digits } = {}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const v = Number(value);
  if (v < 1024) return `${num(v, 0, lang)} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let x = v / 1024;
  let i = 0;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1; }
  const d = digits ?? (x >= 100 ? 0 : 1);
  return `${num(x, d, lang)} ${units[i]}`;
}

/** Bytes as GB with one decimal ("118,4 GB"), for memory bars. */
export function gib(value, lang = "es", digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${num(Number(value) / 1024 ** 3, digits, lang)} GB`;
}

/** Bits per second as "12,3 Mb/s" / "1,2 Gb/s". */
export function rate(bps, lang = "es") {
  if (bps === null || bps === undefined || Number.isNaN(Number(bps))) return "—";
  const v = Number(bps) * 8;
  if (v < 1000) return `${num(v, 0, lang)} b/s`;
  const units = ["Kb/s", "Mb/s", "Gb/s", "Tb/s"];
  let x = v / 1000;
  let i = 0;
  while (x >= 1000 && i < units.length - 1) { x /= 1000; i += 1; }
  return `${num(x, x >= 100 ? 0 : 1, lang)} ${units[i]}`;
}

/** A link speed in Mb/s as "200 Gb/s" / "10 Gb/s" / "1 Gb/s". */
export function linkSpeed(mbps, lang = "es") {
  if (!mbps) return "—";
  if (mbps >= 1000) return `${num(mbps / 1000, mbps % 1000 ? 1 : 0, lang)} Gb/s`;
  return `${num(mbps, 0, lang)} Mb/s`;
}

export function clock(ts, lang) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(locale(lang), { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function shortTime(ts, lang) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString(locale(lang), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// "hace 5 min" / "dentro de 2 h"
export function rel(ts, lang, nowMs = Date.now()) {
  if (!ts) return "—";
  const diff = ts * 1000 - nowMs;
  const abs = Math.abs(diff);
  const formatter = new Intl.RelativeTimeFormat(locale(lang), { numeric: "auto", style: "short" });
  if (abs < 45_000) return formatter.format(0, "second");
  const units = [["year", 31_536_000_000], ["month", 2_592_000_000], ["day", 86_400_000], ["hour", 3_600_000], ["minute", 60_000]];
  for (const [unit, size] of units) {
    if (abs >= size || unit === "minute") return formatter.format(Math.round(diff / size), unit);
  }
  return "—";
}

/** A wait of ``seconds`` as "45 s", "5 min 12 s" or "1 h 02 min". */
export function elapsed(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "—";
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (total < 3600) return `${minutes} min ${String(total % 60).padStart(2, "0")} s`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** Uptime as "3 d 4 h", "5 h 12 min", "8 min". */
export function uptime(seconds, lang = "es") {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const day = lang === "en" ? "d" : "d";
  if (d) return `${d} ${day} ${h} h`;
  if (h) return `${h} h ${String(m).padStart(2, "0")} min`;
  return `${m} min`;
}

/** Context length as "1M", "256K", "32.768". */
export function ctx(n, lang = "es") {
  if (!n) return "—";
  if (n >= 1024 * 1024 && n % (1024 * 1024) === 0) return `${n / (1024 * 1024)}M`;
  if (n >= 1000 * 1000 && n % (1000 * 1000) === 0) return `${n / 1000000}M`;
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}K`;
  return num(n, 0, lang);
}

export const splitList = (text) => (text || "").split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);

export const basename = (p) => (p || "").replace(/\/+$/, "").split("/").pop() || "/";
export const dirname = (p) => {
  const clean = (p || "").replace(/\/+$/, "");
  const i = clean.lastIndexOf("/");
  return i <= 0 ? "/" : clean.slice(0, i);
};
export const joinPath = (a, b) => `${(a || "").replace(/\/+$/, "")}/${b}`;
export const ext = (name) => {
  const i = (name || "").lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
};
