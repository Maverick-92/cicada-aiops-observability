"""Outage demo: stop SaaS, watch the collector buffer, restart, prove zero loss / zero duplicates.
Run from repo root:  py simulator/outage_demo.py [outage_seconds]
"""
import os, sys, time, sqlite3, requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from collector.collector import BUFFER_DB, EGRESS_DB

BASE = "http://localhost:8000"
H = {"Authorization": "Bearer token-acme-ap-south"}
OUTAGE = int(sys.argv[1]) if len(sys.argv) > 1 else 75


def depth():
    c = sqlite3.connect(BUFFER_DB)
    n = c.execute("SELECT COUNT(*) FROM outbound_queue").fetchone()[0]
    c.close()
    return n


def up():
    try:
        return requests.get(BASE + "/api/dashboard", headers=H, timeout=2).status_code == 200
    except requests.RequestException:
        return False


def mark():
    c = sqlite3.connect(EGRESS_DB)
    m = c.execute("SELECT COALESCE(MAX(rowid),0) FROM egress_audit").fetchone()[0]
    c.close()
    return m


start_mark = mark()
print("Collector must be running. Backend must be running now.")
input("\n[1] Press Enter, THEN stop the backend (Ctrl+C in its window)... ")
t0 = time.time()
peak = 0
print(f"[2] Outage window: {OUTAGE}s (>60s shows STALE/Poor on the dashboard)")
while time.time() - t0 < OUTAGE:
    d = depth()
    peak = max(peak, d)
    print(f"  t+{int(time.time()-t0):3d}s  SaaS {'UP  ' if up() else 'DOWN'}  collector queue depth = {d}")
    time.sleep(5)

input("\n[3] Start the backend again (same command, do NOT delete the DB), then press Enter... ")
while not up():
    time.sleep(1)
t_up = time.time()
print("  SaaS reachable again. Waiting for collector to drain its buffer...")
while depth() > 8 and time.time() - t_up < 120:
    print(f"  queue depth = {depth()}")
    time.sleep(2)
drain = time.time() - t_up
time.sleep(3)

c = sqlite3.connect(EGRESS_DB)
delivered = [r[0] for r in c.execute(
    "SELECT payload_id FROM egress_audit WHERE rowid > ? AND delivery_status = 'DELIVERED'", (start_mark,))]
pending = c.execute(
    "SELECT COUNT(*) FROM egress_audit WHERE rowid > ? AND delivery_status != 'DELIVERED'", (start_mark,)).fetchone()[0]
c.close()

r = requests.post(BASE + "/api/reconcile", json={"payload_ids": delivered}, headers=H, timeout=30).json()
ok = r["missing"] == 0 and r["duplicate_rows"] == 0 and pending <= 8
print("\n=== RECONCILIATION (collector audit log vs SaaS store) ===")
print(f"Peak buffered during outage : {peak}")
print(f"Drain time after reconnect  : {drain:.1f}s")
print(f"Collector says delivered    : {r['sent']}")
print(f"SaaS actually holds         : {r['received']}")
print(f"Missing (lost)              : {r['missing']}")
print(f"Duplicate rows              : {r['duplicate_rows']}")
print(f"Still in flight             : {pending}")
print("RESULT:", "PASS - zero loss, zero duplicates" if ok else "FAIL - investigate")