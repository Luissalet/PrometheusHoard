// Figures derived from a NodeView of /api/overview.

/** CX7 links of a node: {links:[{name, up, speed_mbps, rx_bps, tx_bps, v4}], rx, tx, up, total, speed}. */
export function fabricOf(node) {
  const src = node?.fabric || Object.fromEntries(Object.entries(node?.net || {}).filter(([, v]) => v.fabric));
  const links = Object.entries(src || {}).map(([name, v]) => ({ name, ...v }));
  const rx = links.reduce((s, l) => s + (l.rx_bps || 0), 0);
  const tx = links.reduce((s, l) => s + (l.tx_bps || 0), 0);
  const up = links.filter((l) => l.up).length;
  const speed = links.filter((l) => l.up).reduce((s, l) => s + (l.speed_mbps || 0), 0);
  const known = links.some((l) => l.rx_bps !== null && l.rx_bps !== undefined);
  return { links, rx: known ? rx : null, tx: known ? tx : null, up, total: links.length, speed };
}

/** The main disk: the one mounted at / (else the biggest). */
export function mainDisk(node) {
  const disks = node?.disks || [];
  return disks.find((d) => d.path === "/") || [...disks].sort((a, b) => (b.total || 0) - (a.total || 0))[0] || null;
}

export const memFrac = (node) => (node?.memory?.total ? (node.memory.used || 0) / node.memory.total : 0);

/** Columns of history samples as arrays for the charts. */
export function columns(samples) {
  const s = samples || [];
  return {
    cpu: s.map((x) => x.cpu),
    gpu: s.map((x) => x.gpu),
    mem: s.map((x) => x.mem),
    memUsed: s.map((x) => x.mem_used),
    power: s.map((x) => x.power),
    temp: s.map((x) => x.temp),
    fabricRx: s.map((x) => x.fabric_rx),
    fabricTx: s.map((x) => x.fabric_tx),
    lanRx: s.map((x) => x.lan_rx),
    lanTx: s.map((x) => x.lan_tx),
  };
}
