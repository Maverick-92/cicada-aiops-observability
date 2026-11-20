import os
import argparse, json, random, time, uuid
from datetime import datetime, timezone
import requests

URL = os.getenv("SAAS_INGEST_URL", "http://localhost:8000/ingest")
TOKEN = "token-global-us-east"
TENANT = "tenant_global"
REGION = "us-east-1"
COLLECTOR = "col-global-sim"
SPEC = [
    ("cpu_pct", "infrastructure", 2.0, 1, 99),
    ("mem_pct", "infrastructure", 1.2, 1, 99),
    ("disk_pct", "infrastructure", 0.2, 1, 99),
    ("bandwidth_mbps", "network", 15.0, 0, 1000),
    ("latency_ms", "network", 3.0, 1, 2000),
    ("packet_loss_pct", "network", 0.05, 0, 100),
]

def now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--sites", type=int, default=60)
    ap.add_argument("--cycles", type=int, default=0)
    ap.add_argument("--spike", default="")
    ap.add_argument("--silence", default="")
    a = ap.parse_args()
    topo = json.load(open("simulator/topology.json", encoding="utf-8"))
    all_sites = sorted({t["site_id"] for t in topo}, key=lambda s: (s.rsplit("-B", 1)[1], s))
    site_ids = all_sites[: a.sites]
    by_site = {}
    base = {}
    for t in topo:
        if t["site_id"] in site_ids:
            by_site.setdefault(t["site_id"], []).append(t)
        rr = random.Random(t["source_hash"])
        base[t["source_hash"]] = {"cpu_pct": rr.uniform(18, 42), "mem_pct": rr.uniform(40, 70), "disk_pct": rr.uniform(30, 75), "bandwidth_mbps": rr.uniform(60, 400), "latency_ms": rr.uniform(25, 90), "packet_loss_pct": 0.1}
    sess = requests.Session()
    sess.headers.update({"Authorization": "Bearer " + TOKEN})
    print("sites:", len(site_ids), "servers:", sum(len(v) for v in by_site.values()), "interval:", a.interval, "s")
    cycle = 0
    while True:
        t0 = time.time()
        ok = 0
        bad = 0
        for sid in site_ids:
            if sid == a.silence:
                continue
            ts = now_z()
            batch = []
            for t in by_site[sid]:
                h = t["source_hash"]
                for metric, cat, sd, lo, hi in SPEC:
                    v = base[h][metric] + random.gauss(0, sd)
                    sev = "low"
                    ctx = "NORMAL_SAMPLE"
                    if a.spike and sid == a.spike and t["server_no"] == 1 and metric in ("cpu_pct", "latency_ms"):
                        v = random.uniform(94, 97) if metric == "cpu_pct" else random.uniform(400, 430)
                        sev = "high"
                        ctx = "CPU_SPIKE" if metric == "cpu_pct" else "LATENCY_DEGRADED"
                    v = min(max(v, lo), hi)
                    batch.append({"payload_id": str(uuid.uuid4()), "tenant_id": TENANT, "region": REGION, "collector_id": COLLECTOR, "source_hash": h, "category": cat, "metric": metric, "value": round(v, 2), "ts_utc": ts, "source_tz": t["source_tz"], "severity": sev, "context_code": ctx})
            try:
                resp = sess.post(URL, json=batch, timeout=10)
                if resp.status_code in (200, 201):
                    ok += len(batch)
                else:
                    bad += len(batch)
                    print("HTTP", resp.status_code, resp.text[:120])
            except Exception as e:
                bad += len(batch)
                print("send failed:", str(e)[:100])
        dt = time.time() - t0
        cycle += 1
        print("cycle", cycle, "sent", ok, "failed", bad, "took", round(dt, 1), "s =", round(ok / max(dt, 0.001)), "events/s")
        if dt > a.interval:
            print("WARNING: cycle slower than interval; servers can go stale (60 s rule)")
        if a.cycles and cycle >= a.cycles:
            break
        time.sleep(max(a.interval - dt, 0))

if __name__ == "__main__":
    main()
