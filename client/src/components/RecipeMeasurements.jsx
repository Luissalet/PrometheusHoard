import React from "react";
import { useApp } from "../context.js";
import { measurementRows } from "../measurements.js";

const SUMMARY = ["prompt_tokens", "decode_tokens_s_estimate", "ttft_seconds", "needle_pass"];

export default function RecipeMeasurements({ measured }) {
  const { t, lang } = useApp();
  const summary = Object.fromEntries(SUMMARY.filter((key) => measured[key] !== undefined).map((key) => [key, measured[key]]));
  const facts = (values) => <dl className="facts">{measurementRows(values, t, lang).map((row) =>
    <div key={row.id}><dt>{row.label}</dt><dd className="num" style={{ overflowWrap: "anywhere" }}>{row.value}</dd></div>)}</dl>;
  return <div className="measured space-y-2">
    <span className="label">{t("measured")}</span>
    {Object.keys(summary).length > 0 && facts(summary)}
    <details>
      <summary className="btn-link">{t("measurement_details")}</summary>
      <div className="mt-2">{facts(measured)}</div>
    </details>
  </div>;
}
