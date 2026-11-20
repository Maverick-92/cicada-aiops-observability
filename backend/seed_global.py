import json, sqlite3, sys
from datetime import datetime, timezone
from backend.db import SAAS_DB, _hash_token

TENANT = "tenant_global"
REGION = "us-east-1"
TOKEN = "token-global-us-east"

def main():
    topo = json.load(open("simulator/topology.json", encoding="utf-8"))
    if len(topo) != 600:
        sys.exit("topology.json must hold 600 servers")
    conn = sqlite3.connect(SAAS_DB, timeout=30)
    has = conn.execute("SELECT name FROM sqlite_master WHERE type = ? AND name = ?", ("table", "tenants")).fetchone()
    if not has:
        sys.exit("tenants table missing: start the backend once, then rerun")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("CREATE TABLE IF NOT EXISTS sites (tenant_id TEXT NOT NULL, site_id TEXT NOT NULL, country TEXT NOT NULL, city TEXT NOT NULL, building TEXT NOT NULL, PRIMARY KEY (tenant_id, site_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS servers (tenant_id TEXT NOT NULL, source_hash TEXT NOT NULL, site_id TEXT NOT NULL, server_no INTEGER NOT NULL, source_tz TEXT NOT NULL, PRIMARY KEY (tenant_id, source_hash))")
    conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, company_name, home_region, created_at_utc) VALUES (?, ?, ?, ?)", (TENANT, "Acme Global", REGION, now))
    conn.execute("INSERT OR IGNORE INTO collector_tokens (token_hash, token_hint, tenant_id, allowed_region, collector_id, is_active, created_at_utc) VALUES (?, ?, ?, ?, ?, 1, ?)", (_hash_token(TOKEN), "token-global-***", TENANT, REGION, "col-global-sim", now))
    sites = {}
    for t in topo:
        sites[t["site_id"]] = (TENANT, t["site_id"], t["country"], t["city"], t["building"])
    conn.executemany("INSERT OR IGNORE INTO sites VALUES (?, ?, ?, ?, ?)", list(sites.values()))
    conn.executemany("INSERT OR IGNORE INTO servers VALUES (?, ?, ?, ?, ?)", [(TENANT, t["source_hash"], t["site_id"], t["server_no"], t["source_tz"]) for t in topo])
    conn.commit()
    n_sites = conn.execute("SELECT COUNT(*) FROM sites WHERE tenant_id = ?", (TENANT,)).fetchone()[0]
    n_srv = conn.execute("SELECT COUNT(*) FROM servers WHERE tenant_id = ?", (TENANT,)).fetchone()[0]
    n_tok = conn.execute("SELECT COUNT(*) FROM collector_tokens WHERE tenant_id = ?", (TENANT,)).fetchone()[0]
    conn.close()
    print("tenant", TENANT, "sites", n_sites, "servers", n_srv, "tokens", n_tok)
    if n_sites != 60 or n_srv != 600 or n_tok != 1:
        sys.exit("SEED CHECK FAILED")
    print("SEED OK")

if __name__ == "__main__":
    main()
