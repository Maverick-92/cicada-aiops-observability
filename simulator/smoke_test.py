import uuid
import requests
from datetime import datetime, timezone

BASE = "http://localhost:8000"
TOKEN = "token-acme-ap-south"

def payload(**over):
    p = {
        "payload_id": str(uuid.uuid4()),
        "tenant_id": "tenant_acme",
        "region": "ap-south-1",
        "collector_id": "col-acme-01",
        "source_hash": "srv_abc123def456",
        "category": "performance",
        "metric": "cpu_pct",
        "value": 25.0,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "source_tz": "Asia/Kolkata",
        "severity": "info",
        "context_code": "NORMAL",
    }
    p.update(over)
    return p

def post(p, token=TOKEN):
    r = requests.post(f"{BASE}/ingest", json=p, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r.json()

p = payload()
print("1 valid        ->", post(p))                                   # 200, accepted=1
print("2 duplicate    ->", post(p))                                   # 200, duplicates_ignored=1
print("3 wrong region ->", post(payload(region="eu-central-1")))      # 403
print("4 wrong tenant ->", post(payload(tenant_id="tenant_globex")))  # 403
print("5 bad token    ->", post(payload(), token="nope"))             # 401

d = requests.get(f"{BASE}/api/dashboard", headers={"Authorization": f"Bearer {TOKEN}"}).json()
print("dashboard      ->", d["health"]["state"], "| events:", d["total_events_stored"])
print("history        ->", d["health_history"])
