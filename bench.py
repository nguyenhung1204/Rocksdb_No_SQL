"""
bench.py - So sanh LogStore (RocksDB) voi SQLite (thu vien chuan) tren cung khoi luong nhat ky.

Cong bang: ca hai deu co 3 chi muc tuong duong (theo thoi gian, theo muc, theo nguon),
SQLite dung WAL + synchronous=NORMAL, RocksDB khong fsync tung lan ghi (mac dinh).

Chay:  python bench.py --n 200000
Ket qua tuy may; hay chay nhieu lan va ghi lai may/phien ban khi dua vao bao cao.
"""

import argparse
import json
import os
import platform
import random
import shutil
import sqlite3
import statistics
import tempfile
import time

from logstore import LogStore, LEVELS
from logstore.gen import generate

LEVEL_IDX = {n: i for i, n in enumerate(LEVELS)}


def timed(fn):
    t = time.perf_counter()
    r = fn()
    return time.perf_counter() - t, r


def percentile(samples, p):
    """Nearest-rank percentile of observed values (no interpolation)."""
    if not samples:
        raise ValueError("samples must not be empty")
    if not 0 <= p <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(samples)
    rank = max(1, (len(ordered) * p + 99) // 100)
    return ordered[rank - 1]


def latency_summary(samples):
    return {"p50": percentile(samples, 50), "p95": percentile(samples, 95),
            "p99": percentile(samples, 99)}


# ------------------------------------------------------------------ SQLite
class SqliteStore:
    def __init__(self, path):
        self.c = sqlite3.connect(path)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")
        self.c.execute("""CREATE TABLE events(
            id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, level INTEGER NOT NULL,
            source TEXT NOT NULL, msg TEXT NOT NULL, fields TEXT)""")
        self.c.execute("CREATE INDEX i_ts ON events(ts)")
        self.c.execute("CREATE INDEX i_level ON events(level, ts)")
        self.c.execute("CREATE INDEX i_source ON events(source, ts)")

    def put_many(self, evs):
        self.c.executemany(
            "INSERT INTO events(ts,level,source,msg,fields) VALUES(?,?,?,?,?)",
            [(ts, LEVEL_IDX[lv], src, msg, json.dumps(f)) for lv, src, msg, f, ts in evs])
        self.c.commit()

    def put_one(self, ev):
        self.put_many([ev])

    def range_query(self, lo, hi, limit):
        return self.c.execute("SELECT * FROM events WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT ?",
                              (lo, hi, limit)).fetchall()

    def level_query(self, lo, hi, minlv, limit):
        return self.c.execute(
            "SELECT * FROM events WHERE ts BETWEEN ? AND ? AND level>=? ORDER BY ts DESC LIMIT ?",
            (lo, hi, minlv, limit)).fetchall()

    def source_query(self, lo, hi, src, limit):
        return self.c.execute(
            "SELECT * FROM events WHERE source=? AND ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT ?",
            (src, lo, hi, limit)).fetchall()

    def purge(self, cutoff):
        self.c.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.c.commit()

    def close(self):
        self.c.close()


def dir_size(p):
    if os.path.isfile(p):
        return sum(os.path.getsize(p + s) for s in ("", "-wal", "-shm") if os.path.exists(p + s))
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)


def throughput_trials(events, work, repeats):
    rates = {"rocksdb": [], "sqlite": []}
    for trial in range(repeats):
        rocks = LogStore(os.path.join(work, "repeat-rocks-%d" % trial))
        lite = SqliteStore(os.path.join(work, "repeat-lite-%d.db" % trial))
        for name, store in (("rocksdb", rocks), ("sqlite", lite)):
            started = time.perf_counter()
            for offset in range(0, len(events), 1000):
                store.put_many(events[offset:offset + 1000])
            elapsed = time.perf_counter() - started
            rates[name].append(len(events) / elapsed if elapsed else float("inf"))
        rocks.close()
        lite.close()
    return {name: latency_summary(samples) for name, samples in rates.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--single", type=int, default=5_000, help="so lan ghi tung su kien rieng le")
    ap.add_argument("--repeat", type=int, default=30)
    ap.add_argument("--write-repeat", type=int, default=3, help="so lan lap ghi tren DB moi")
    ap.add_argument("--json", help="ghi ket qua ra file JSON")
    a = ap.parse_args()
    if a.n < 1 or a.single < 1 or a.repeat < 1 or a.write_repeat < 1:
        ap.error("n, single, repeat va write-repeat phai lon hon 0")

    work = tempfile.mkdtemp(prefix="bench_")
    events = generate(a.n, span_hours=24, seed=1)
    lo_all = min(e[4] for e in events)
    hi_all = max(e[4] for e in events)
    print("May: %s | Python %s | N=%d su kien" % (platform.platform(), platform.python_version(), a.n))

    res = {}

    rocks = LogStore(os.path.join(work, "rocks"))
    lite = SqliteStore(os.path.join(work, "lite.db"))

    # --- 1. ghi hang loat (batch 1000)
    def load(store):
        for i in range(0, a.n, 1000):
            store.put_many(events[i:i + 1000])
    t_r, _ = timed(lambda: load(rocks))
    t_s, _ = timed(lambda: load(lite))
    res["write_batch_1000_events_per_s"] = {"rocksdb": a.n / t_r, "sqlite": a.n / t_s}
    res["write_batch_1000_events_per_s_repeated"] = throughput_trials(events, work, a.write_repeat)

    # --- 2. ghi tung su kien (moi lan 1 ghi/commit)
    extra = generate(a.single, end_ms=hi_all + 10_000_000, span_hours=1, seed=2)
    t_r, _ = timed(lambda: [rocks.put_many([e]) for e in extra])
    t_s, _ = timed(lambda: [lite.put_one(e) for e in extra])
    res["write_single_events_per_s"] = {"rocksdb": a.single / t_r, "sqlite": a.single / t_s}
    hi_all = max(hi_all, max(e[4] for e in extra))

    rocks.flush()

    # --- 3. truy van (trung vi cua nhieu lan, cua so thoi gian ngau nhien 10 phut)
    rnd = random.Random(5)
    windows = []
    for _ in range(a.repeat):
        s = rnd.randint(lo_all, hi_all - 600_000)
        windows.append((s, s + 600_000))
    it = iter(range(10**9))

    def pick():
        return windows[next(it) % len(windows)]

    def bench_pair(name, fr, fs):
        samples = {"rocksdb": [], "sqlite": []}
        for _ in range(a.repeat):
            lo, hi = pick()
            for db_name, query in (("rocksdb", fr), ("sqlite", fs)):
                started = time.perf_counter()
                query(lo, hi)
                samples[db_name].append((time.perf_counter() - started) * 1000)
        res[name] = {db_name: statistics.median(values) for db_name, values in samples.items()}
        res[name + "_percentiles_ms"] = {
            db_name: latency_summary(values) for db_name, values in samples.items()
        }

    bench_pair("range_10min_limit100_ms",
               lambda lo, hi: rocks.query(lo, hi, limit=100),
               lambda lo, hi: lite.range_query(lo, hi, 100))
    bench_pair("level>=ERROR_10min_ms",
               lambda lo, hi: rocks.query(lo, hi, level="ERROR", limit=100),
               lambda lo, hi: lite.level_query(lo, hi, 3, 100))
    bench_pair("source_10min_ms",
               lambda lo, hi: rocks.query(lo, hi, source="payment-service", limit=100),
               lambda lo, hi: lite.source_query(lo, hi, "payment-service", 100))

    # --- 4. lay 1 ban ghi theo id (chi RocksDB co "id" dang key; SQLite lay theo rowid)
    ids = [e["id"] for e in rocks.query(limit=2000)]
    ids_b = [bytes.fromhex(i) for i in ids]
    rowids = [rnd.randint(1, a.n) for _ in range(2000)]
    t_r, _ = timed(lambda: [rocks.get(i) for i in ids_b])
    t_s, _ = timed(lambda: [lite.c.execute("SELECT * FROM events WHERE id=?", (r,)).fetchone() for r in rowids])
    res["point_get_us"] = {"rocksdb": t_r / len(ids_b) * 1e6, "sqlite": t_s / len(rowids) * 1e6}

    # --- 5. dung luong dia
    rocks.flush()
    rocks.compact()
    lite.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    res["disk_mb"] = {"rocksdb": dir_size(os.path.join(work, "rocks")) / 1e6,
                      "sqlite": dir_size(os.path.join(work, "lite.db")) / 1e6}

    # --- 6. xoa 1 nua du lieu cu (retention)
    cutoff = lo_all + (hi_all - lo_all) // 2
    t_r, nr = timed(lambda: rocks.purge_older_than(cutoff))
    t_s, _ = timed(lambda: lite.purge(cutoff))
    res["purge_half_ms"] = {"rocksdb": t_r * 1000, "sqlite": t_s * 1000}

    # ----- in bang
    print("\n%-34s %14s %14s" % ("Phep do", "RocksDB", "SQLite"))
    print("-" * 64)
    for k, v in res.items():
        if isinstance(v["rocksdb"], dict):
            fmt = lambda stats: " ".join("%s=%.2f" % (p, val) for p, val in stats.items())
            print("%-34s %14s %14s" % (k, fmt(v["rocksdb"]), fmt(v["sqlite"])))
        else:
            print("%-34s %14.2f %14.2f" % (k, v["rocksdb"], v["sqlite"]))
    print("\n(events_per_s: cao hon la tot; ms/us/mb: thap hon la tot; p50/p95/p99: phan vi cac lan chay)")

    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)
    rocks.close()
    lite.close()
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
