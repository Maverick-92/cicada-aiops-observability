import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from collector.collector import (
    BUFFER_DB,
    EGRESS_DB,
    db_lock,
    drain_queue_worker,
    init_dbs,
    process_raw_event,
    telemetry_loop,
)

app = FastAPI(title="Tier 1 Collector - Egress Proof Panel")


class ScenarioRequest(BaseModel):
    scenario: Literal[
        "secret_leak",
        "cpu_spike",
        "failed_logins",
        "duplicate_replay",
        "out_of_order",
    ]


@app.on_event("startup")
def start_collector_threads():
    init_dbs()
    threading.Thread(target=drain_queue_worker, daemon=True).start()
    threading.Thread(target=telemetry_loop, daemon=True).start()


@app.get("/api/egress")
def get_egress_records():
    with db_lock:
        e_conn = sqlite3.connect(EGRESS_DB)
        e_conn.row_factory = sqlite3.Row
        rows = e_conn.execute(
            "SELECT * FROM egress_audit ORDER BY id DESC LIMIT 20"
        ).fetchall()
        totals = e_conn.execute(
            "SELECT COALESCE(SUM(raw_bytes),0) AS raw_b, COALESCE(SUM(sent_bytes),0) AS sent_b, COUNT(*) AS cnt FROM egress_audit"
        ).fetchone()
        e_conn.close()

        b_conn = sqlite3.connect(BUFFER_DB)
        queued = b_conn.execute("SELECT COUNT(*) FROM outbound_queue").fetchone()[0]
        b_conn.close()

    return {
        "total_payloads": totals["cnt"],
        "raw_bytes_total": totals["raw_b"],
        "sent_bytes_total": totals["sent_b"],
        "queued_offline": queued,
        "records": [dict(r) for r in rows],
    }


@app.post("/api/trigger")
def trigger_scenario(req: ScenarioRequest):
    if req.scenario == "secret_leak":
        res = process_raw_event(
            {
                "hostname": "hdfc-mumbai-core-db-01",
                "username": "root_admin",
                "password": "SuperSecretPassword!99",
                "api_key": "sk-live-9988776655",
                "ip_address": "192.168.10.55",
                "raw_log": "ALERT auth failure user=root_admin password=SuperSecretPassword!99 ip=192.168.10.55",
                "category": "infrastructure",
                "metric": "cpu_pct",
                "value": 44.5,
            }
        )
        return {"triggered": req.scenario, "outbound": res}

    if req.scenario == "cpu_spike":
        for v in (94.2, 96.8):
            res = process_raw_event(
                {
                    "hostname": "hdfc-mumbai-core-db-01",
                    "raw_log": f"kernel: high load average cpu={v}%",
                    "category": "infrastructure",
                    "metric": "cpu_pct",
                    "value": v,
                }
            )
            process_raw_event(
                {
                    "hostname": "hdfc-mumbai-core-db-01",
                    "raw_log": "probe rtt spike 410ms",
                    "category": "network",
                    "metric": "latency_ms",
                    "value": 410.0,
                }
            )
        return {"triggered": req.scenario, "outbound": res}

    if req.scenario == "failed_logins":
        res = process_raw_event(
            {
                "hostname": "hdfc-mumbai-core-db-01",
                "raw_log": "LOCAL AGGREGATION: 37 failed SSH logins for user=admin from 45.33.12.9 (raw lines kept local)",
                "category": "security",
                "metric": "failed_logins_1m",
                "value": 37.0,
            }
        )
        return {"triggered": req.scenario, "outbound": res}

    if req.scenario == "duplicate_replay":
        fixed_id = f"dup-test-{int(datetime.now(timezone.utc).timestamp())}"
        for _ in range(3):
            res = process_raw_event(
                {
                    "hostname": "hdfc-mumbai-core-db-01",
                    "raw_log": "replayed packet test",
                    "category": "infrastructure",
                    "metric": "mem_pct",
                    "value": 61.0,
                    "override_payload_id": fixed_id,
                }
            )
        return {"triggered": req.scenario, "replayed_payload_id": fixed_id, "outbound": res}

    if req.scenario == "out_of_order":
        past_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        res = process_raw_event(
            {
                "hostname": "hdfc-mumbai-core-db-01",
                "raw_log": "delayed packet from 5 mins ago",
                "category": "network",
                "metric": "packet_loss_pct",
                "value": 8.5,
                "override_ts_utc": past_ts,
            }
        )
        return {"triggered": req.scenario, "event_ts_utc": past_ts, "outbound": res}


@app.get("/", response_class=HTMLResponse)
def egress_dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
  <title>Tier 1 Collector - Egress Proof Panel</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0b1120; color: #e2e8f0; margin: 0; padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 14px; }
    .badge { background: #065f46; color: #6ee7b7; padding: 6px 12px; border-radius: 999px; font-weight: 600; font-size: 13px; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 18px 0; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 14px; }
    .card h3 { margin: 0; font-size: 12px; color: #94a3b8; text-transform: uppercase; }
    .card p { margin: 6px 0 0; font-size: 22px; font-weight: 700; color: #38bdf8; }
    .controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }
    button { background: #1d4ed8; color: white; border: none; padding: 9px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    button:hover { background: #2563eb; }
    button.warn { background: #b91c1c; }
    table { width: 100%; border-collapse: collapse; background: #111827; border-radius: 10px; overflow: hidden; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #1f2937; text-align: left; vertical-align: top; }
    th { background: #1e293b; color: #94a3b8; }
    pre { margin: 0; white-space: pre-wrap; color: #34d399; font-size: 12px; }
    .stripped { color: #f87171; font-weight: 600; }
    .status-DELIVERED { color: #34d399; font-weight: 700; }
    .status-BUFFERED_OFFLINE { color: #fbbf24; font-weight: 700; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1 style="margin:0;font-size:20px;">TIER 1 CUSTOMER COLLECTOR - EGRESS PROOF PANEL</h1>
      <div style="color:#94a3b8;font-size:13px;">Trust Boundary Audit: Exact payload logged before crossing to SaaS</div>
    </div>
    <span class="badge">ZERO RAW LOGS TRANSMITTED</span>
  </div>

  <div class="stats">
    <div class="card"><h3>Payloads Inspected</h3><p id="cnt">0</p></div>
    <div class="card"><h3>Raw Local Bytes</h3><p id="rawb">0 B</p></div>
    <div class="card"><h3>Metadata Sent Bytes</h3><p id="sentb">0 B</p></div>
    <div class="card"><h3>Offline Disk Queue</h3><p id="queue" style="color:#fbbf24;">0</p></div>
  </div>

  <div class="controls">
    <button onclick="fire('secret_leak')">1. Inject Secret Leak Log</button>
    <button class="warn" onclick="fire('cpu_spike')">2. Inject CPU + Latency Spike</button>
    <button onclick="fire('failed_logins')">3. Inject 37 Failed Logins</button>
    <button onclick="fire('duplicate_replay')">4. Replay 3x Duplicate ID</button>
    <button onclick="fire('out_of_order')">5. Inject 5m Late Packet</button>
  </div>

  <table>
    <thead>
      <tr>
        <th>Time (UTC)</th>
        <th>Local Scrubbed Preview (Stays Local)</th>
        <th>Forbidden Fields Dropped</th>
        <th>Exact 12-Field JSON Sent Across Trust Boundary</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

  <script>
    async function fire(scenario) {
      await fetch('/api/trigger', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario})
      });
      refresh();
    }
    async function refresh() {
      const r = await fetch('/api/egress');
      const d = await r.json();
      document.getElementById('cnt').innerText = d.total_payloads;
      document.getElementById('rawb').innerText = d.raw_bytes_total + ' B';
      document.getElementById('sentb').innerText = d.sent_bytes_total + ' B';
      document.getElementById('queue').innerText = d.queued_offline;
      document.getElementById('rows').innerHTML = d.records.map(row => `
        <tr>
          <td>${row.ts_utc.split('T')[1].slice(0,8)}Z</td>
          <td>${row.raw_preview}</td>
          <td class="stripped">${row.stripped_fields}</td>
          <td><pre>${JSON.stringify(JSON.parse(row.outbound_json), null, 2)}</pre></td>
          <td class="status-${row.delivery_status}">${row.delivery_status}</td>
        </tr>
      `).join('');
    }
    setInterval(refresh, 1500);
    refresh();
  </script>
</body>
</html>
"""
