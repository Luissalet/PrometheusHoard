import React from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Icon, ICONS, MenuButton } from "./ui.jsx";

const ASLEEP = new Set(["off", "sleeping"]);

/** Power state of a node as {label, tone, busy}: what the status chip shows. */
export function powerState(node, t) {
  const ps = node?.power_state || "unknown";
  if (ps === "restarting") return { key: ps, label: t("ps_restarting"), tone: "chip-amber", busy: true };
  if (ps === "shutting_down") return { key: ps, label: t("ps_shutting_down"), tone: "chip-amber", busy: true };
  if (ps === "waking") return { key: ps, label: t("ps_waking"), tone: "chip-amber", busy: true };
  if (ps === "sleeping") return { key: ps, label: t("ps_sleeping"), tone: "chip" };
  if (ps === "off") return { key: ps, label: t("ps_off"), tone: "chip" };
  if (node?.online) return { key: "on", label: t("ps_on"), tone: "chip-ok" };
  if (!node?.enabled) return { key: "disabled", label: t("ps_disabled"), tone: "chip" };
  return { key: "unknown", label: t("ps_unreachable"), tone: "chip-danger" };
}

/**
 * The power button of one Spark (`node` is a NodeView) or of the whole cluster (`nodes` given, node omitted).
 * Like the Windows power menu: Lock, Sleep, Shut down, Restart; Wake when it is off. Sleep, shut down and restart ask first.
 */
export function PowerButton({ node, nodes, compact, className }) {
  const { t, notify, toastError, confirm, refreshOv } = useApp();
  const all = !node;
  const list = all ? (nodes || []) : [node];
  const target = all ? "all" : node.id;
  const name = all ? t("all_sparks") : node.name;
  const anyOn = list.some((n) => n.online);
  const anyOff = list.some((n) => !n.online && n.enabled !== false);
  const loaded = list.flatMap((n) => (n.deployments || []).map((d) => d.title));
  const uniqueLoaded = [...new Set(loaded)];

  const act = async (action) => {
    if (action !== "wake" && action !== "lock") {
      const ok = await confirm({
        title: t(`power_confirm_${action}`, { name }),
        message: uniqueLoaded.length ? t("power_unloads", { models: uniqueLoaded.join(", ") }) : t("power_nothing_loaded"),
        detail: t(`power_detail_${action}`),
        confirmLabel: t(`power_${action}`),
      });
      if (!ok) return;
    }
    try {
      const res = await api.call("power", { node: target, action, confirm: true });
      const failed = (res.results || []).filter((r) => !r.ok);
      if (failed.length) failed.forEach((r) => notify(`${r.node}: ${r.message || r.error || t("failed")}`, "error"));
      else notify(t(`power_done_${action}`, { name }));
    } catch (e) {
      toastError(e);
    }
    refreshOv();
  };

  const items = [];
  if (anyOn) {
    items.push({ label: t("power_lock"), icon: ICONS.lock, onClick: () => act("lock") });
    items.push({ label: t("power_sleep"), icon: ICONS.moon, onClick: () => act("sleep") });
    items.push({ label: t("power_shutdown"), icon: ICONS.power, onClick: () => act("shutdown"), danger: true });
    items.push({ label: t("power_restart"), icon: ICONS.restart, onClick: () => act("restart") });
  }
  if (anyOff || list.some((n) => ASLEEP.has(n.power_state))) {
    if (items.length) items.push("-");
    items.push({ label: all ? t("power_wake_all") : t("power_wake"), icon: ICONS.power, onClick: () => act("wake") });
  }
  if (!items.length) items.push({ label: t("power_nothing"), disabled: true });

  return (
    <MenuButton items={items} align="right" label={t("power_of", { name })} className={className || `btn ${compact ? "btn-sm btn-icon" : ""} btn-power`}>
      <Icon d={ICONS.power} size={compact ? 15 : 16} />
      {!compact && <span>{all ? t("power_all") : t("power")}</span>}
    </MenuButton>
  );
}
