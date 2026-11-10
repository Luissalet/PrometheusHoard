"""Long history of every Spark: GPU, CPU, memory, temperature and power, one row per Spark and minute in ``<data>/history.sqlite3``.

The poller hands each probe sample to :meth:`MetricsStore.add`; samples are averaged per minute in memory (maxima kept for temperature,
power and GPU use) and written when the minute changes. Rows older than ``history_days`` are dropped once an hour. Queries group the
minutes into buckets so a range of an hour or of a month comes back as a few hundred points per Spark, with ``None`` where there were no
samples (the Spark was off, unreachable or the app was closed), so charts show the gap instead of a line across it. Energy is the
integral of the GPU board power over the minutes that have data: a lower bound of what the Spark draws from the wall."""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

FIELDS = ("gpu", "cpu", "mem", "mem_used", "temp", "power")
MAX_FIELDS = ("gpu", "temp", "power")
MAX_POINTS = 360
RANGES_H = (1, 6, 24, 24 * 7, 24 * 30)

SCHEMA = """CREATE TABLE IF NOT EXISTS minutes (
  node TEXT NOT NULL, t INTEGER NOT NULL, n INTEGER NOT NULL,
  gpu REAL, gpu_max REAL, cpu REAL, mem REAL, mem_used REAL, temp REAL, temp_max REAL, power REAL, power_max REAL,
  PRIMARY KEY (node, t))"""


class _Acc:
    __slots__ = ("t", "n", "sum", "cnt", "max")

    def __init__(self, t: int):
        self.t = t
        self.n = 0
        self.sum = {f: 0.0 for f in FIELDS}
        self.cnt = {f: 0 for f in FIELDS}
        self.max: dict[str, Optional[float]] = {f: None for f in MAX_FIELDS}

    def add(self, sample: dict[str, Any]) -> None:
        self.n += 1
        for f in FIELDS:
            v = sample.get(f)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if math.isnan(v):
                continue
            self.sum[f] += v
            self.cnt[f] += 1
            if f in self.max and (self.max[f] is None or v > self.max[f]):
                self.max[f] = v

    def row(self, node: str) -> tuple:
        avg = {f: (self.sum[f] / self.cnt[f]) if self.cnt[f] else None for f in FIELDS}
        return (node, self.t, self.n, avg["gpu"], self.max["gpu"], avg["cpu"], avg["mem"], avg["mem_used"], avg["temp"], self.max["temp"],
                avg["power"], self.max["power"])


class MetricsStore:
    def __init__(self, path: Path, *, days: Callable[[], int] = lambda: 90, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.days = days
        self.clock = clock
        self._lock = threading.RLock()
        self._acc: dict[str, _Acc] = {}
        self._pruned = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(SCHEMA)
            self._db.commit()

    # ------------------------------------------------------------------ writing
    def add(self, node_id: str, sample: dict[str, Any]) -> None:
        minute = int(float(sample["t"]) // 60 * 60)
        with self._lock:
            acc = self._acc.get(node_id)
            if acc is not None and acc.t != minute:
                self._write([acc.row(node_id)])
                acc = None
            if acc is None:
                acc = self._acc[node_id] = _Acc(minute)
            acc.add(sample)
        if self.clock() - self._pruned > 3600:
            self.prune()

    def _write(self, rows: list[tuple]) -> None:
        if not rows:
            return
        self._db.executemany("INSERT OR REPLACE INTO minutes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self._db.commit()

    def flush(self) -> None:
        """Write the minute in progress too (it is written again, complete, when the minute ends)."""
        with self._lock:
            self._write([acc.row(node) for node, acc in self._acc.items() if acc.n])

    def prune(self) -> int:
        self._pruned = self.clock()
        cutoff = int(self.clock() - 86400 * max(1, int(self.days())))
        with self._lock:
            cur = self._db.execute("DELETE FROM minutes WHERE t < ?", (cutoff,))
            self._db.commit()
            return cur.rowcount or 0

    def close(self) -> None:
        with self._lock:
            try:
                self.flush()
                self._db.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ reading
    @staticmethod
    def step_for(span_s: float) -> int:
        return int(max(60, math.ceil(span_s / MAX_POINTS / 60) * 60))

    def query(self, nodes: Iterable[str], hours: float, *, end: Optional[float] = None) -> dict[str, Any]:
        """Bucketed series per Spark for the last ``hours``: ``{start, end, step, t: [...], nodes: {id: {gpu: [...], ...}}, stats: {id: {...}}}``."""
        end = float(end if end is not None else self.clock())
        span = max(60.0, float(hours) * 3600)
        step = self.step_for(span)
        start = int((end - span) // step * step)
        last = int(end // step * step)
        buckets = list(range(start, last + 1, step))
        index = {b: i for i, b in enumerate(buckets)}
        ids = list(nodes)
        self.flush()
        out_nodes: dict[str, dict[str, list[Optional[float]]]] = {}
        keys = ("gpu", "gpu_max", "cpu", "mem", "mem_used", "temp", "temp_max", "power", "power_max")
        for node in ids:
            out_nodes[node] = {k: [None] * len(buckets) for k in keys}
        if ids:
            marks = ",".join("?" * len(ids))
            sql = (f"SELECT node, (t / ?) * ? AS b, AVG(gpu), MAX(gpu_max), AVG(cpu), AVG(mem), AVG(mem_used), AVG(temp), MAX(temp_max), "
                   f"AVG(power), MAX(power_max) FROM minutes WHERE node IN ({marks}) AND t >= ? AND t <= ? GROUP BY node, b")
            with self._lock:
                rows = self._db.execute(sql, (step, step, *ids, start, end)).fetchall()
            for row in rows:
                node, b = row[0], int(row[1])
                i = index.get(b)
                if i is None:
                    continue
                for k, v in zip(keys, row[2:]):
                    out_nodes[node][k][i] = None if v is None else round(float(v), 2)
        return {"start": start, "end": end, "step": step, "hours": hours, "t": buckets, "nodes": out_nodes, "stats": self.stats(ids, end - span, end)}

    def stats(self, nodes: Iterable[str], start: float, end: float) -> dict[str, dict[str, Any]]:
        """Per Spark over the span: average and peak GPU use, peak temperature, average and peak power, energy (kWh) and how many minutes
        had data out of the minutes in the span."""
        ids = list(nodes)
        out: dict[str, dict[str, Any]] = {}
        if not ids:
            return out
        marks = ",".join("?" * len(ids))
        sql = (f"SELECT node, COUNT(*), AVG(gpu), MAX(gpu_max), MAX(temp_max), AVG(temp), AVG(power), MAX(power_max), SUM(power), AVG(mem), MAX(mem) "
               f"FROM minutes WHERE node IN ({marks}) AND t >= ? AND t <= ? GROUP BY node")
        with self._lock:
            rows = self._db.execute(sql, (*ids, int(start), end)).fetchall()
        total = max(1, int(round((end - start) / 60)))
        for node in ids:
            out[node] = {"minutes": 0, "minutes_total": total, "gpu_avg": None, "gpu_max": None, "temp_max": None, "temp_avg": None, "power_avg": None,
                         "power_max": None, "energy_kwh": 0.0, "mem_avg": None, "mem_max": None}
        for node, count, gpu_avg, gpu_max, temp_max, temp_avg, p_avg, p_max, p_sum, mem_avg, mem_max in rows:
            r = lambda v, d=1: None if v is None else round(float(v), d)  # noqa: E731
            out[node] = {"minutes": int(count), "minutes_total": total, "gpu_avg": r(gpu_avg), "gpu_max": r(gpu_max), "temp_max": r(temp_max),
                         "temp_avg": r(temp_avg), "power_avg": r(p_avg), "power_max": r(p_max),
                         "energy_kwh": round(float(p_sum or 0.0) * 60 / 3.6e6, 3), "mem_avg": r(mem_avg), "mem_max": r(mem_max)}
        return out

    def size(self) -> dict[str, Any]:
        with self._lock:
            rows, oldest = self._db.execute("SELECT COUNT(*), MIN(t) FROM minutes").fetchone()
        return {"rows": int(rows or 0), "oldest": oldest, "bytes": self.path.stat().st_size if self.path.exists() else 0}
