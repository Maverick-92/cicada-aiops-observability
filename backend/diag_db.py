import os, shutil, sqlite3, tempfile, time
from backend.db import SAAS_DB

T = "tenant_global"

def timeit(conn, label, sql, args, n=10):
    t0 = time.perf_counter()
    for _ in range(n):
        conn.execute(sql, args).fetchall()
    ms = (time.perf_counter() - t0) / n * 1000
    print("TIME", label, "avg", round(ms, 1), "ms")

def plan(conn, label, sql, args):
    print("PLAN", label)
    for r in conn.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall():
        print("    ", r[-1])

def commit_bench(mode):
    d = tempfile.mkdtemp()
    c = sqlite3.connect(os.path.join(d, "bench.db"))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=" + mode)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    c.commit()
    t0 = time.perf_counter()
    for i in range(200):
        c.execute("INSERT INTO t (v) VALUES (?)", ("x" * 100,))
        c.commit()
    ms = (time.perf_counter() - t0) / 200 * 1000
    c.close()
    shutil.rmtree(d, ignore_errors=True)
    print("COMMIT bench synchronous=" + mode, "avg", round(ms, 2), "ms per commit")

def main():
    commit_bench("FULL")
    commit_bench("NORMAL")
    conn = sqlite3.connect(SAAS_DB)
    print("db:", SAAS_DB)
    print("journal_mode:", conn.execute("PRAGMA journal_mode").fetchone()[0])
    print("synchronous default (2=FULL, 1=NORMAL):", conn.execute("PRAGMA synchronous").fetchone()[0])
    for row in conn.execute("SELECT tenant_id, COUNT(*) FROM telemetry_events GROUP BY tenant_id").fetchall():
        print("rows", row[0], row[1])
    for r in conn.execute("PRAGMA index_list(telemetry_events)").fetchall():
        cols = [c[2] for c in conn.execute("PRAGMA index_info(" + r[1] + ")").fetchall()]
        print("index", r[1], cols)
    first = conn.execute("SELECT source_hash FROM telemetry_events WHERE tenant_id = ? LIMIT 1", (T,)).fetchone()
    if not first:
        print("no rows for", T)
        return
    h = first[0]
    qs = [
        ("baseline query (aiops, per event)", "SELECT value, ts_utc FROM telemetry_events WHERE tenant_id = ? AND source_hash = ? AND metric = ? ORDER BY ts_utc DESC LIMIT 31", (T, h, "cpu_pct")),
        ("dashboard latest-60", "SELECT * FROM telemetry_events WHERE tenant_id = ? ORDER BY ts_utc DESC LIMIT 60", (T,)),
        ("dashboard COUNT", "SELECT COUNT(*) FROM telemetry_events WHERE tenant_id = ?", (T,)),
        ("collectors rollup (approx)", "SELECT source_hash, MAX(ts_utc), COUNT(*) FROM telemetry_events WHERE tenant_id = ? GROUP BY source_hash", (T,)),
    ]
    for label, sql, args in qs:
        plan(conn, label, sql, args)
        timeit(conn, label, sql, args)
    conn.close()

if __name__ == "__main__":
    main()
