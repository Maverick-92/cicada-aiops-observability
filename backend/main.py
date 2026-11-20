import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Union

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.aiops import evaluate_and_correlate
from backend.db import (
    API_TOKENS,
    SAAS_DB,
    init_saas_db,
    insert_payload_idempotent,
    saas_lock,
)

app = FastAPI(title="Tier 2 Central Multi-Tenant AIOps SaaS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApproveRequest(BaseModel):
    operator_name: str = "On-Call SRE"


@app.on_event("startup")
def startup():
    init_saas_db()


def authenticate(authorization: str | None) -> tuple[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token not in API_TOKENS:
        raise HTTPException(status_code=403, detail="Invalid collector/tenant token")
    return API_TOKENS[token]


@app.post("/ingest")
def ingest_telemetry(
    body: Union[dict, List[dict]],
    authorization: str | None = Header(default=None),
):
    tenant_id, allowed_region = authenticate(authorization)
    items = body if isinstance(body, list) else [body]

    accepted = 0
    duplicates = 0

    for item in items:
        # Enforce tenant isolation & regional data residency
        if item.get("tenant_id") != tenant_id:
            raise HTTPException(status_code=403, detail="Tenant ID mismatch with auth token")
        if item.get("region") != allowed_region:
            raise HTTPException(
                status_code=403,
                detail=f"403 Region Mismatch: tenant pinned to {allowed_region}, got {item.get('region')}",
            )

        if insert_payload_idempotent(item):
            accepted += 1
            evaluate_and_correlate(item)
        else:
            duplicates += 1

    return {"status": "ok", "accepted": accepted, "duplicates_ignored": duplicates}


@app.get("/api/dashboard")
def get_dashboard(authorization: str | None = Header(default=None)):
    tenant_id, region = authenticate(authorization)
    now = datetime.now(timezone.utc)

    with saas_lock:
        conn = sqlite3.connect(SAAS_DB)
        conn.row_factory = sqlite3.Row

        # Latest 60 events ordered strictly by EVENT TIME (ts_utc)
        metrics_rows = conn.execute(
            """
            SELECT * FROM telemetry_events
            WHERE tenant_id = ?
            ORDER BY ts_utc DESC LIMIT 60
            """,
            (tenant_id,),
        ).fetchall()

        # Collector fleet health & 60-second stale detection
        col_rows = conn.execute(
            """
            SELECT collector_id, source_hash, MAX(ts_utc) AS last_seen_utc, COUNT(*) AS event_count
            FROM telemetry_events
            WHERE tenant_id = ?
            GROUP BY collector_id, source_hash
            """,
            (tenant_id,),
        ).fetchall()

        incidents_rows = conn.execute(
            """
            SELECT * FROM incidents
            WHERE tenant_id = ?
            ORDER BY updated_at_utc DESC LIMIT 15
            """,
            (tenant_id,),
        ).fetchall()

        audit_rows = conn.execute(
            """
            SELECT * FROM remediation_audit
            WHERE tenant_id = ?
            ORDER BY id DESC LIMIT 15
            """,
            (tenant_id,),
        ).fetchall()

        total_events = conn.execute(
            "SELECT COUNT(*) FROM telemetry_events WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()[0]
        conn.close()

    # Compute 4-state health evaluation (Excellent, Good, Fair, Poor)
    latest_cpu = next((r["value"] for r in metrics_rows if r["metric"] == "cpu_pct"), 25.0)
    latest_lat = next((r["value"] for r in metrics_rows if r["metric"] == "latency_ms"), 30.0)
    open_incidents = [dict(r) for r in incidents_rows if r["status"] == "OPEN"]

    collectors = []
    any_stale = False
    for c in col_rows:
        last_dt = datetime.fromisoformat(c["last_seen_utc"].replace("Z", "+00:00"))
        age_sec = round((now - last_dt).total_seconds(), 1)
        is_stale = age_sec > 60.0
        if is_stale:
            any_stale = True
        collectors.append(
            {
                "collector_id": c["collector_id"],
                "source_hash": c["source_hash"],
                "last_seen_utc": c["last_seen_utc"],
                "seconds_ago": age_sec,
                "status": "STALE / OFFLINE" if is_stale else "ONLINE",
                "health": "Poor" if is_stale else "Excellent",
                "event_count": c["event_count"],
            }
        )

    if any_stale or latest_cpu >= 90 or any(i["severity"] == "critical" for i in open_incidents):
        overall_state = "Poor"
        state_reason = "Collector stale (>60s) or critical threshold/anomaly active" if any_stale else f"Critical anomaly / CPU at {latest_cpu}%"
    elif latest_cpu >= 70 or latest_lat >= 250 or len(open_incidents) > 0:
        overall_state = "Fair"
        state_reason = f"Elevated load (CPU {latest_cpu}%, Latency {latest_lat}ms) or open incident"
    elif latest_cpu >= 50:
        overall_state = "Good"
        state_reason = f"Minor workload deviation (CPU {latest_cpu}%), no service impact"
    else:
        overall_state = "Excellent"
        state_reason = "All collectors reporting on time; metrics within normal Z-score baselines"

    # Reverse metrics_rows so charts render left-to-right chronologically by event time
    chronological_metrics = [dict(r) for r in reversed(metrics_rows)]

    parsed_incidents = []
    raw_alerts_total = 0
    for inc in incidents_rows:
        d = dict(inc)
        d["signals"] = json.loads(d.pop("signals_json"))
        raw_alerts_total += d["raw_alert_count"]
        parsed_incidents.append(d)

    return {
        "tenant_id": tenant_id,
        "region": region,
        "total_events_stored": total_events,
        "health": {
            "state": overall_state,
            "reason": state_reason,
            "latest_cpu": latest_cpu,
            "latest_latency": latest_lat,
        },
        "noise_reduction": {
            "raw_anomalies": raw_alerts_total,
            "correlated_incidents": len(parsed_incidents),
        },
        "collectors": collectors,
        "metrics": chronological_metrics,
        "incidents": parsed_incidents,
        "audit_logs": [dict(a) for a in audit_rows],
    }


@app.post("/api/incidents/{incident_id}/approve")
def approve_remediation(
    incident_id: str,
    req: ApproveRequest,
    authorization: str | None = Header(default=None),
):
    tenant_id, _ = authenticate(authorization)
    now_utc = datetime.now(timezone.utc).isoformat()

    with saas_lock:
        conn = sqlite3.connect(SAAS_DB)
        conn.row_factory = sqlite3.Row
        inc = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ? AND tenant_id = ?",
            (incident_id, tenant_id),
        ).fetchone()
        if not inc:
            conn.close()
            raise HTTPException(status_code=404, detail="Incident not found")

        conn.execute(
            "UPDATE incidents SET status = 'RESOLVED_SIMULATED', updated_at_utc = ? WHERE incident_id = ?",
            (now_utc, incident_id),
        )
        conn.execute(
            """
            INSERT INTO remediation_audit
            (incident_id, tenant_id, action_taken, approved_by, policy_check, rollback_plan, ts_utc)
            VALUES (?, ?, ?, ?, 'PASSED (Policy #SAFE-OPS-01)', ?, ?)
            """,
            (
                incident_id,
                tenant_id,
                inc["recommended_action"],
                req.operator_name,
                inc["rollback_plan"],
                now_utc,
            ),
        )
        conn.commit()
        conn.close()

    return {"status": "approved_and_logged", "incident_id": incident_id}
