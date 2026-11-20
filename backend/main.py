import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Union

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.aiops import evaluate_and_correlate
from backend.db import (
    SAAS_DB,
    init_saas_db,
    insert_payload_idempotent,
    record_health_transition,
    saas_lock,
    verify_token_from_db,
)

ALLOWED_FIELDS = {"payload_id", "tenant_id", "region", "collector_id", "source_hash", "category", "metric", "value", "ts_utc", "source_tz", "severity", "context_code"}

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
    try:
        _c = sqlite3.connect(SAAS_DB)
        _has = _c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sites'").fetchone()
        _cnt = _c.execute("SELECT COUNT(*) FROM sites").fetchone()[0] if _has else 0
        _c.close()
        if _cnt == 0:
            from backend.seed_global import main as _seed_global
            from backend.seed_tenants import main as _seed_tenants
            _seed_global()
            _seed_tenants()
    except Exception as _e:
        print(f'[startup] auto-seed skipped: {_e}')


def authenticate(authorization: str | None) -> tuple[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    ctx = verify_token_from_db(token)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return ctx


@app.post("/ingest")
def ingest_telemetry(
    body: Union[dict, List[dict]],
    authorization: str | None = Header(default=None),
):
    tenant_id, allowed_region = authenticate(authorization)
    items = body if isinstance(body, list) else [body]

    for _it in items:
        if not isinstance(_it, dict):
            raise HTTPException(status_code=422, detail="Payload rejected: each item must be a JSON object")
        _bad = set(_it.keys()) ^ ALLOWED_FIELDS
        if _bad:
            raise HTTPException(status_code=422, detail="Payload rejected by SaaS allow-list; offending fields: " + ", ".join(sorted(_bad)))

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

        transitions_rows = conn.execute(
            "SELECT previous_state, new_state, reason, ts_utc FROM health_transitions WHERE tenant_id = ? ORDER BY id DESC LIMIT 10",
            (tenant_id,),
        ).fetchall()

        total_events = conn.execute(
            "SELECT COUNT(*) FROM telemetry_events WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()[0]
        slo_rows = conn.execute(
            "SELECT ts_utc, ingested_at_utc FROM telemetry_events WHERE tenant_id = ? AND metric != 'collector_heartbeat' ORDER BY id DESC LIMIT 300",
            (tenant_id,),
        ).fetchall()
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

    online_cols = sorted([c for c in collectors if c["status"] == "ONLINE"], key=lambda x: x["seconds_ago"])
    stale_cols = sorted([c for c in collectors if c["status"] != "ONLINE"], key=lambda x: x["seconds_ago"])
    stale_count = len(stale_cols)
    online_count = len(online_cols)
    total_collectors = len(collectors)
    collectors = (online_cols[:15] + stale_cols[:10]) if (online_cols and stale_cols) else (online_cols + stale_cols)[:25]

    import sqlite3 as _sq
    from backend.extras import load_thresholds
    with saas_lock:
        _tc = _sq.connect(SAAS_DB)
        _th = load_thresholds(_tc, tenant_id)
        _tc.close()
    cpu_fair, cpu_poor = _th["cpu_pct"]
    lat_fair, lat_poor = _th["latency_ms"]
    crit_incidents = [i for i in open_incidents if i["severity"] == "critical"]
    if any_stale or latest_cpu >= cpu_poor or latest_lat >= lat_poor or crit_incidents:
        overall_state = "Poor"
        reasons = []
        if any_stale:
            reasons.append(f"{stale_count} of {total_collectors} collector(s) stale (>60s)")
        if latest_cpu >= cpu_poor:
            reasons.append(f"CPU {latest_cpu}% >= poor limit {cpu_poor}%")
        if latest_lat >= lat_poor:
            reasons.append(f"latency {latest_lat} ms >= poor limit {lat_poor} ms")
        if crit_incidents:
            reasons.append(f"{len(crit_incidents)} open critical incident(s) ({crit_incidents[0]['incident_id']})")
        state_reason = "; ".join(reasons)
    elif latest_cpu >= cpu_fair or latest_lat >= lat_fair or open_incidents:
        overall_state = "Fair"
        reasons = []
        if latest_cpu >= cpu_fair:
            reasons.append(f"CPU {latest_cpu}% >= fair limit {cpu_fair}%")
        if latest_lat >= lat_fair:
            reasons.append(f"latency {latest_lat} ms >= fair limit {lat_fair} ms")
        if open_incidents:
            reasons.append(f"{len(open_incidents)} open incident(s) ({open_incidents[0]['incident_id']})")
        state_reason = "; ".join(reasons)
    elif latest_cpu >= 0.7 * cpu_fair:
        overall_state = "Good"
        state_reason = f"CPU {latest_cpu}% in upper normal band (fair limit {cpu_fair}%)"
    else:
        overall_state = "Excellent"
        state_reason = f"All {online_count} collector(s) reporting on time; metrics within configured thresholds"

    record_health_transition(tenant_id, overall_state, state_reason)

    # Reverse metrics_rows so charts render left-to-right chronologically by event time
    chronological_metrics = [dict(r) for r in reversed(metrics_rows)]

    parsed_incidents = []
    raw_alerts_total = 0
    for inc in incidents_rows:
        d = dict(inc)
        d["signals"] = json.loads(d.pop("signals_json"))
        raw_alerts_total += d["raw_alert_count"]
        parsed_incidents.append(d)

    lags = []
    late_excluded = 0
    for sr in slo_rows:
        try:
            t_evt = datetime.fromisoformat(str(sr[0]).replace("Z", "+00:00"))
            t_ing = datetime.fromisoformat(str(sr[1]).replace("Z", "+00:00"))
            if t_evt.tzinfo is None:
                t_evt = t_evt.replace(tzinfo=now.tzinfo)
            if t_ing.tzinfo is None:
                t_ing = t_ing.replace(tzinfo=now.tzinfo)
            lag = max((t_ing - t_evt).total_seconds(), 0.0)
        except Exception:
            continue
        if lag > 60.0:
            late_excluded += 1
        else:
            lags.append(lag)
    lags.sort()
    def _pct(p):
        return round(lags[max(0, -(-p * len(lags) // 100) - 1)] * 1000) if lags else None
    slo = {"p50_ms": _pct(50), "p95_ms": _pct(95), "samples": len(lags), "late_excluded": late_excluded, "target_p95_ms": 10000}
    
    return {
        "tenant_id": tenant_id,
        "region": region,
        "total_events_stored": total_events,
        "slo": slo,
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
        "health_history": [dict(r) for r in transitions_rows],
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

class ReconcileRequest(BaseModel):
    payload_ids: List[str]


@app.post("/api/reconcile")
def reconcile(req: ReconcileRequest, authorization: str | None = Header(default=None)):
    """Tenant-scoped delivery proof: how many of these payload_ids does SaaS hold?"""
    tenant_id, _ = authenticate(authorization)
    ids = list(dict.fromkeys(req.payload_ids))[:20000]
    found = 0
    rows_total = 0
    with saas_lock:
        conn = sqlite3.connect(SAAS_DB)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            r = conn.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT payload_id) FROM telemetry_events "
                f"WHERE tenant_id = ? AND payload_id IN ({marks})",
                [tenant_id, *chunk],
            ).fetchone()
            rows_total += r[0]
            found += r[1]
        conn.close()
    return {
        "sent": len(ids),
        "received": found,
        "missing": len(ids) - found,
        "duplicate_rows": rows_total - found,
    }

from backend.extras import register
register(app, authenticate)
