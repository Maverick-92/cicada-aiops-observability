import sqlite3
from datetime import datetime, timedelta, timezone
from fastapi import Header, HTTPException
from backend.db import SAAS_DB, saas_lock

WINDOW_S = 90
STALE_S = 60
ORDER = {"Excellent": 0, "Good": 1, "Fair": 2, "Poor": 3}
METRICS = ["cpu_pct", "mem_pct", "disk_pct", "bandwidth_mbps", "latency_ms", "packet_loss_pct"]
DEFAULTS = {"mem_pct": (75.0, 90.0), "disk_pct": (80.0, 92.0), "bandwidth_mbps": (700.0, 900.0), "packet_loss_pct": (2.0, 5.0)}
SEEN = {}

def parse_ts(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

def metric_state(th, metric, v):
    fair, poor = th[metric]
    if v >= poor:
        return "Poor"
    if v >= fair:
        return "Fair"
    if v >= 0.7 * fair:
        return "Good"
    return "Excellent"

def worst(a, b):
    return a if ORDER[a] >= ORDER[b] else b

def load_thresholds(conn, tenant_id):
    conn.execute("CREATE TABLE IF NOT EXISTS tenant_thresholds (tenant_id TEXT NOT NULL, metric TEXT NOT NULL, fair REAL NOT NULL, poor REAL NOT NULL, PRIMARY KEY (tenant_id, metric))")
    n = conn.execute("SELECT COUNT(*) FROM tenant_thresholds WHERE tenant_id = ?", (tenant_id,)).fetchone()[0]
    if n == 0:
        t = conn.execute("SELECT cpu_fair_threshold, cpu_poor_threshold, latency_fair_threshold FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        seed = dict(DEFAULTS)
        seed["cpu_pct"] = (t[0], t[1]) if t else (70.0, 90.0)
        seed["latency_ms"] = (t[2], t[2] * 2) if t else (250.0, 500.0)
        for m, fp in seed.items():
            conn.execute("INSERT OR IGNORE INTO tenant_thresholds VALUES (?, ?, ?, ?)", (tenant_id, m, fp[0], fp[1]))
        conn.commit()
    out = {}
    for r in conn.execute("SELECT metric, fair, poor FROM tenant_thresholds WHERE tenant_id = ?", (tenant_id,)).fetchall():
        out[r[0]] = (r[1], r[2])
    return out

def load_topology(conn, tenant_id):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", ("table",)).fetchall()}
    if "servers" not in have or "sites" not in have:
        return {}
    out = {}
    for r in conn.execute("SELECT s.source_hash, s.site_id, t.country, t.city, t.building FROM servers s JOIN sites t ON t.tenant_id = s.tenant_id AND t.site_id = s.site_id WHERE s.tenant_id = ?", (tenant_id,)).fetchall():
        out[r[0]] = (r[2], r[3], r[4], r[1])
    return out

def eval_server(th, s, now):
    if s["last"] is None:
        return "Poor", "no data in last " + str(WINDOW_S) + " s: stale or offline", True
    age = (now - parse_ts(s["last"])).total_seconds()
    if age > STALE_S:
        return "Poor", "last data " + str(int(age)) + " s ago (stale after " + str(STALE_S) + " s)", True
    state = "Excellent"
    reason = "all metrics within thresholds"
    for m, v in s["vals"].items():
        if m not in th:
            continue
        st = metric_state(th, m, v)
        if ORDER[st] > ORDER[state]:
            state = st
            if st == "Poor":
                reason = m + " " + str(round(v, 1)) + " >= poor limit " + str(th[m][1])
            elif st == "Fair":
                reason = m + " " + str(round(v, 1)) + " >= fair limit " + str(th[m][0])
            else:
                reason = m + " " + str(round(v, 1)) + " is in the upper normal band"
    return state, reason, False

def register(app, authenticate):
    @app.get("/api/fleet/summary")
    def fleet_summary(authorization: str | None = Header(default=None)):
        tenant_id, region = authenticate(authorization)
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=WINDOW_S)).strftime("%Y-%m-%dT%H:%M:%S")
        with saas_lock:
            conn = sqlite3.connect(SAAS_DB)
            th = load_thresholds(conn, tenant_id)
            rows = conn.execute("SELECT source_hash, metric, value, ts_utc FROM telemetry_events WHERE tenant_id = ? AND ts_utc >= ? AND metric != ?", (tenant_id, cutoff, "collector_heartbeat")).fetchall()
            topo = load_topology(conn, tenant_id)
            conn.close()
        latest = {}
        for r in rows:
            key = (r[0], r[1])
            cur = latest.get(key)
            if cur is None or r[3] > cur[1]:
                latest[key] = (r[2], r[3])
        servers = {}
        for (h, m), (v, ts) in latest.items():
            s = servers.setdefault(h, {"last": ts, "vals": {}})
            s["vals"][m] = v
            if ts > s["last"]:
                s["last"] = ts
        seen = SEEN.setdefault(tenant_id, set())
        for h in list(servers):
            seen.add(h)
        for h in topo:
            if h in seen and h not in servers:
                servers[h] = {"last": None, "vals": {}}
        tree = {}
        acc = {}
        for h, s in servers.items():
            country, city, building, site = topo.get(h, ("Unmapped", "Unmapped", "Unmapped", "-"))
            state, reason, stale = eval_server(th, s, now)
            if not stale:
                for m, v in s["vals"].items():
                    acc.setdefault(m, []).append((v, h))
            node = tree.setdefault(country, {}).setdefault(city, {}).setdefault(building, {"site_id": site, "servers": []})
            node["servers"].append({"source_hash": h, "state": state, "reason": reason, "stale": stale, "vals": {k: round(x, 1) for k, x in s["vals"].items()}})
        cards = []
        for m in METRICS:
            pts = acc.get(m, [])
            if not pts:
                cards.append({"metric": m, "avg": None, "max": None, "max_server": None, "fair": th[m][0], "poor": th[m][1], "state": "No data", "servers_reporting": 0, "servers_over_fair": 0})
                continue
            top = max(pts, key=lambda p: p[0])
            cards.append({"metric": m, "avg": round(sum(p[0] for p in pts) / len(pts), 1), "max": round(top[0], 1), "max_server": top[1], "fair": th[m][0], "poor": th[m][1], "state": metric_state(th, m, top[0]), "servers_reporting": len(pts), "servers_over_fair": sum(1 for p in pts if p[0] >= th[m][0])})
        rollup = []
        for country in sorted(tree):
            cities = []
            c_state = "Excellent"
            c_n = 0
            c_stale = 0
            for city in sorted(tree[country]):
                buildings = []
                ci_state = "Excellent"
                ci_n = 0
                ci_stale = 0
                for b in sorted(tree[country][city]):
                    node = tree[country][city][b]
                    sl = sorted(node["servers"], key=lambda x: x["source_hash"])
                    b_state = "Excellent"
                    for x in sl:
                        b_state = worst(b_state, x["state"])
                    stale_n = sum(1 for x in sl if x["stale"])
                    bad = sorted([x for x in sl if x["state"] in ("Fair", "Poor")], key=lambda x: -ORDER[x["state"]])
                    if bad:
                        reason = str(len(bad)) + " of " + str(len(sl)) + " servers degraded; worst " + bad[0]["source_hash"] + ": " + bad[0]["reason"]
                    else:
                        reason = "all " + str(len(sl)) + " servers within thresholds"
                    buildings.append({"building": b, "site_id": node["site_id"], "state": b_state, "servers": len(sl), "stale": stale_n, "reason": reason, "server_list": sl})
                    ci_state = worst(ci_state, b_state)
                    ci_n += len(sl)
                    ci_stale += stale_n
                cities.append({"city": city, "state": ci_state, "servers": ci_n, "stale": ci_stale, "buildings": buildings})
                c_state = worst(c_state, ci_state)
                c_n += ci_n
                c_stale += ci_stale
            rollup.append({"country": country, "state": c_state, "servers": c_n, "stale": c_stale, "cities": cities})
        return {"tenant_id": tenant_id, "region": region, "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_s": WINDOW_S, "stale_after_s": STALE_S, "servers_registered": len(topo), "servers_seen": len(servers), "thresholds": {m: {"fair": th[m][0], "poor": th[m][1]} for m in th}, "metrics": cards, "rollup": rollup}
    
    @app.put("/api/thresholds")
    def set_threshold(body: dict, authorization: str | None = Header(default=None)):
        tenant_id, region = authenticate(authorization)
        m = body.get("metric")
        if m not in METRICS:
            raise HTTPException(status_code=422, detail="metric must be one of " + ", ".join(METRICS))
        try:
            fair = float(body.get("fair"))
            poor = float(body.get("poor"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="fair and poor must be numbers")
        if not (0 <= fair < poor):
            raise HTTPException(status_code=422, detail="need 0 <= fair < poor")
        with saas_lock:
            conn = sqlite3.connect(SAAS_DB)
            load_thresholds(conn, tenant_id)
            conn.execute("UPDATE tenant_thresholds SET fair = ?, poor = ? WHERE tenant_id = ? AND metric = ?", (fair, poor, tenant_id, m))
            conn.commit()
            conn.close()
        return {"status": "ok", "tenant_id": tenant_id, "metric": m, "fair": fair, "poor": poor}
