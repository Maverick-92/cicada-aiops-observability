import argparse, hashlib, json, random, time, uuid
from datetime import datetime, timezone
import requests

URL = "http://localhost:8000/ingest"
# key -> (tenant, country in topology.json, region, token, collector)
FLEETS = {
    "acme": ("tenant_acme", "India", "ap-south-1", "token-acme-sim-ap-south", "col-acme-sim"),
    "globex": ("tenant_globex", "Germany", "eu-central-1", "token-globex-sim-eu-central", "col-globex-sim"),
    "initech": ("tenant_initech", "USA", "us-east-1", "token-initech-sim-us-east", "col-initech-sim"),
}
SPEC = [
    ("cpu_pct", "infrastructure", 2.0, 1, 99),
    ("mem_pct", "infrastructure", 1.2, 1, 99),
    ("disk_pct", "infrastructure", 0.2, 1, 99),
    ("bandwidth_mbps", "network", 15.0, 0, 1000),
    ("latency_ms", "network", 3.0, 1, 2000),
    ("packet_loss_pct", "network", 0.05, 0, 100),
]


def derive(tenant, h):
    # keep identical to derive() in backend/seed_tenants.py
    return "srv_" + hashlib.sha256((tenant + ":" + h).encode()).hexdigest()[:12]


def now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=45.0)
    ap.add_argument("--sites", type=int, default=4, help="sites per tenant (4 = one building per city)")
    ap.add_argument("--per-site", type=int, default=5, help="servers streamed per site")
    ap.add_argument("--only", default="acme,globex,initech")
    ap.add_argument("--cycles", type=int, default=0)
    ap.add_argument("--spike", default="")
    ap.add_argument("--silence", default="")
    a = ap.parse_args()
    topo = json.load(open("simulator/topology.json", encoding="utf-8"))
    keys = [k.strip() for k in a.only.split(",") if k.strip()]
    unknown = [k for k in keys if k not in FLEETS]
    if unknown:
        raise SystemExit("unknown fleet: " + ", ".join(unknown))
    fleets = []
    for k in keys:
        tenant, country, region, token, collector = FLEETS[k]
        rows = [t for t in topo if t["country"] == country]
        site_ids = sorted({t["site_id"] for t in rows}, key=lambda s: (s.rsplit("-B", 1)[1], s))[: a.sites]
        by_site = {}
        base = {}
        for sid in site_ids:
            members = sorted([t for t in rows if t["site_id"] == sid], key=lambda t: t["server_no"])[: a.per_site]
            by_site[sid] = [(derive(tenant, t["source_hash"]), t["source_tz"]) for t in members]
            for h, tz in by_site[sid]:
                rr = random.Random(h)
                base[h] = {"cpu_pct": rr.uniform(18, 42), "mem_pct": rr.uniform(40, 70), "disk_pct": rr.uniform(30, 75), "bandwidth_mbps": rr.uniform(60, 400), "latency_ms": rr.uniform(25, 90), "packet_loss_pct": 0.1}
        sess = requests.Session()
        sess.headers.update({"Authorization": "Bearer " + token})
        fleets.append({"tenant": tenant, "region": region, "collector": collector, "sess": sess, "site_ids": site_ids, "by_site": by_site, "base": base})
        print(tenant, "sites:", len(site_ids), "servers:", sum(len(v) for v in by_site.values()))
    print("interval:", a.interval, "s")
    cycle = 0
    while True:
        t0 = time.time()
        ok = 0
        bad = 0
        for f in fleets:
            for sid in f["site_ids"]:
                if sid == a.silence:
                    continue
                ts = now_z()
                batch = []
                for idx, (h, tz) in enumerate(f["by_site"][sid]):
                    for metric, cat, sd, lo, hi in SPEC:
                        v = f["base"][h][metric] + random.gauss(0, sd)
                        sev = "low"
                        ctx = "NORMAL_SAMPLE"
                        if a.spike and sid == a.spike and idx == 0 and metric in ("cpu_pct", "latency_ms"):
                            v = random.uniform(94, 97) if metric == "cpu_pct" else random.uniform(400, 430)
                            sev = "high"
                            ctx = "CPU_SPIKE" if metric == "cpu_pct" else "LATENCY_DEGRADED"
                        v = min(max(v, lo), hi)
                        batch.append({"payload_id": str(uuid.uuid4()), "tenant_id": f["tenant"], "region": f["region"], "collector_id": f["collector"], "source_hash": h, "category": cat, "metric": metric, "value": round(v, 2), "ts_utc": ts, "source_tz": tz, "severity": sev, "context_code": ctx})
                try:
                    resp = f["sess"].post(URL, json=batch, timeout=10)
                    if resp.status_code in (200, 201):
                        ok += len(batch)
                    else:
                        bad += len(batch)
                        print("HTTP", resp.status_code, f["tenant"], resp.text[:120])
                except Exception as e:
                    bad += len(batch)
                    print("send failed:", str(e)[:100])
        dt = time.time() - t0
        cycle += 1
        print("cycle", cycle, "sent", ok, "failed", bad, "took", round(dt, 1), "s =", round(ok / max(dt, 0.001)), "events/s")
        if dt > a.interval * 0.6:
            print("WARNING: cycle is using more than 60 percent of the interval; servers can go stale (60 s rule)")
        if a.cycles and cycle >= a.cycles:
            break
        time.sleep(max(a.interval - dt, 0))


if __name__ == "__main__":
    main()