import hashlib, json, sqlite3, sys
from datetime import datetime, timezone
from backend.db import SAAS_DB, _hash_token

# tenant -> (country in topology.json, region, simulator token, simulator collector)
FLEETS = {
    "tenant_acme": ("India", "ap-south-1", "token-acme-sim-ap-south", "col-acme-sim"),
    "tenant_globex": ("Germany", "eu-central-1", "token-globex-sim-eu-central", "col-globex-sim"),
    "tenant_initech": ("USA", "us-east-1", "token-initech-sim-us-east", "col-initech-sim"),
}
REAL_ACME_SERVER = "srv_ceebef596acd"
REAL_ACME_SITE = "IN-BEN-B1"


def derive(tenant, h):
    # keep identical to derive() in simulator/fleet_tenants.py
    return "srv_" + hashlib.sha256((tenant + ":" + h).encode()).hexdigest()[:12]


def main():
    topo = json.load(open("simulator/topology.json", encoding="utf-8"))
    conn = sqlite3.connect(SAAS_DB, timeout=30)
    has = conn.execute("SELECT name FROM sqlite_master WHERE type = ? AND name = ?", ("table", "tenants")).fetchone()
    if not has:
        sys.exit("tenants table missing: start the backend once, then rerun")
    have = {r[0] for r in conn.execute("SELECT tenant_id FROM tenants").fetchall()}
    missing = [t for t in FLEETS if t not in have]
    if missing:
        sys.exit("tenants missing in DB: " + ", ".join(missing))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("CREATE TABLE IF NOT EXISTS sites (tenant_id TEXT NOT NULL, site_id TEXT NOT NULL, country TEXT NOT NULL, city TEXT NOT NULL, building TEXT NOT NULL, PRIMARY KEY (tenant_id, site_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS servers (tenant_id TEXT NOT NULL, source_hash TEXT NOT NULL, site_id TEXT NOT NULL, server_no INTEGER NOT NULL, source_tz TEXT NOT NULL, PRIMARY KEY (tenant_id, source_hash))")
    for tenant, (country, region, token, collector) in FLEETS.items():
        rows = [t for t in topo if t["country"] == country]
        if not rows:
            sys.exit("no servers for country " + country)
        sites = {}
        for t in rows:
            sites[t["site_id"]] = (tenant, t["site_id"], t["country"], t["city"], t["building"])
        conn.executemany("INSERT OR IGNORE INTO sites VALUES (?, ?, ?, ?, ?)", list(sites.values()))
        conn.executemany("INSERT OR IGNORE INTO servers VALUES (?, ?, ?, ?, ?)", [(tenant, derive(tenant, t["source_hash"]), t["site_id"], t["server_no"], t["source_tz"]) for t in rows])
        conn.execute("INSERT OR IGNORE INTO collector_tokens (token_hash, token_hint, tenant_id, allowed_region, collector_id, is_active, created_at_utc) VALUES (?, ?, ?, ?, ?, 1, ?)", (_hash_token(token), "sim-token-***", tenant, region, collector, now))
    acme_sites = {r[0] for r in conn.execute("SELECT site_id FROM sites WHERE tenant_id = ?", ("tenant_acme",)).fetchall()}
    site = REAL_ACME_SITE if REAL_ACME_SITE in acme_sites else sorted(acme_sites)[0]
    conn.execute("INSERT OR IGNORE INTO servers VALUES (?, ?, ?, ?, ?)", ("tenant_acme", REAL_ACME_SERVER, site, 0, "Asia/Kolkata"))
    conn.commit()
    for tenant in FLEETS:
        n_sites = conn.execute("SELECT COUNT(*) FROM sites WHERE tenant_id = ?", (tenant,)).fetchone()[0]
        n_srv = conn.execute("SELECT COUNT(*) FROM servers WHERE tenant_id = ?", (tenant,)).fetchone()[0]
        cities = sorted({r[0] for r in conn.execute("SELECT city FROM sites WHERE tenant_id = ?", (tenant,)).fetchall()})
        print(tenant, "sites", n_sites, "servers", n_srv, "cities", ", ".join(cities))
    print("real Acme collector", REAL_ACME_SERVER, "mapped to", site)
    conn.close()
    print("SEED OK")


if __name__ == "__main__":
    main()