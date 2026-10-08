import { bytes, num } from "./format.js";

export function measurementLabel(key, t) {
  const id = `measurement_${key}`;
  return t.has(id) ? t(id) : key.replaceAll("_", " ");
}

export function measurementValue(key, value, t, lang) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return t(value ? "measurement_yes" : "measurement_no");
  if (Array.isArray(value)) return value.map((v) => measurementValue(key, v, t, lang)).join(" · ") || "—";
  if (typeof value === "object") return JSON.stringify(value);
  if (key === "reasoning_effort" && t.has(`measurement_reasoning_${value}`)) return t(`measurement_reasoning_${value}`);
  if (typeof value !== "number") return String(value);
  if (!Number.isFinite(value)) return "—";
  if (key.endsWith("_bytes")) return bytes(value, lang);
  if (key.endsWith("_seconds")) return `${num(value, 1, lang)} s`;
  if (key.endsWith("_tokens_s") || key.endsWith("_tokens_s_estimate")) return `${num(value, 1, lang)} ${t("measurement_tokens_s")}`;
  if (key === "needle_depths") return `${num(value * 100, 0, lang)} %`;
  return num(value, Number.isInteger(value) ? 0 : 1, lang);
}

/** Keep every nested measurement readable, including unknown future fields. */
export function measurementRows(values, t, lang, parents = []) {
  return Object.entries(values || {}).flatMap(([key, value]) => {
    const path = [...parents, key];
    if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
      return measurementRows(value, t, lang, path);
    }
    return [{ id: path.join("/"), label: path.map((k) => measurementLabel(k, t)).join(" · "),
      value: measurementValue(key, value, t, lang) }];
  });
}
