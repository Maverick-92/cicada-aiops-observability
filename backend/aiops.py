import json
import math
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from backend.db import SAAS_DB, saas_lock


def evaluate_and_correlate(event: dict):
    """
    1. Computes rolling Z-score baseline over last 30 event-time samples.
    2. Applies 2-sample hysteresis to avoid flapping.
    3. Correlates anomalies within 60s on the same source_hash into 1 parent incident.
    """
    metric = event["metric"]
    if metric == "collector_heartbeat":
        return

    tenant_id = event["tenant_id"]
    source_hash = event["source_hash"]
    val = float(event["value"])
    event_ts = event["ts_utc"]

    with saas_lock:
        conn = sqlite3.connect(SAAS_DB)
        conn.row_factory = sqlite3.Row

        # Fetch last 31 samples ordered strictly by EVENT TIME (ts_utc)
        rows = conn.execute(
            """
            SELECT value, ts_utc FROM telemetry_events
            WHERE tenant_id = ? AND source_hash = ? AND metric = ?
            ORDER BY ts_utc DESC LIMIT 31
            """,
            (tenant_id, source_hash, metric),
        ).fetchall()

        # Baseline excludes the newest point
        history = [float(r["value"]) for r in rows[1:]]
        has_baseline = len(history) >= 4
        if has_baseline:
            mean = sum(history) / len(history)
            variance = sum((x - mean) ** 2 for x in history) / len(history)
            std_dev = max(math.sqrt(variance), 1.5)  # floor prevents divide-by-zero on flat metrics
            z_score = (val - mean) / std_dev
        else:
            mean, std_dev, z_score = 0.0, 1.5, 0.0  # no history: hard limits only

        # Hard operational guardrails + dynamic Z-score > 2.8
        is_anomalous = (
            (has_baseline and abs(z_score) >= 2.8)
            or (metric == "cpu_pct" and val >= 88.0)
            or (metric == "latency_ms" and val >= 250.0)
            or (metric == "packet_loss_pct" and val >= 5.0)
            or (metric == "failed_logins_1m" and val >= 10.0)
        )

        if not is_anomalous:
            conn.close()
            return

        # Hysteresis check: require either high severity burst or 2 consecutive elevated samples
        prev_val = float(rows[1]["value"]) if len(rows) > 1 else val
        prev_z = (prev_val - mean) / std_dev
        if metric not in ("failed_logins_1m", "packet_loss_pct") and abs(z_score) < 4.5 and abs(prev_z) < 2.0:
            conn.close()
            return

        signal_entry = {
            "metric": metric,
            "value": round(val, 2),
            "baseline_mean": round(mean, 2),
            "z_score": round(z_score, 2),
            "ts_utc": event_ts,
        }

        grp = 'security' if metric == 'failed_logins_1m' else ('network' if metric == 'packet_loss_pct' else 'resource')
        inc_title = f'Correlated Anomaly on {source_hash} [{grp}]'
        # Check for an existing OPEN incident on the same source_hash and signal group within 60 seconds
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        existing = conn.execute(
            """
            SELECT * FROM incidents
            WHERE tenant_id = ? AND source_hash = ? AND status = 'OPEN' AND title = ? AND updated_at_utc >= ?
            ORDER BY updated_at_utc DESC LIMIT 1
            """,
            (tenant_id, source_hash, inc_title, cutoff),
        ).fetchone()

        now_utc = datetime.now(timezone.utc).isoformat()

        if existing:
            signals = json.loads(existing["signals_json"])
            signals.append(signal_entry)
            metrics_present = {s["metric"] for s in signals}
            cause, action, rollback = _diagnose(metrics_present)
            confidence = min(0.98, round(0.68 + 0.08 * len(metrics_present), 2))
            sev = "critical" if ("cpu_pct" in metrics_present or "failed_logins_1m" in metrics_present) else "high"

            conn.execute(
                """
                UPDATE incidents
                SET severity = ?, likely_cause = ?, recommended_action = ?, rollback_plan = ?,
                    confidence = ?, signals_json = ?, raw_alert_count = raw_alert_count + 1, updated_at_utc = ?
                WHERE incident_id = ?
                """,
                (
                    sev,
                    cause,
                    action,
                    rollback,
                    confidence,
                    json.dumps(signals[-8:]),
                    now_utc,
                    existing["incident_id"],
                ),
            )
        else:
            metrics_present = {metric}
            cause, action, rollback = _diagnose(metrics_present)
            confidence = min(0.92, round(0.65 + min(abs(z_score) * 0.04, 0.25), 2))
            sev = "critical" if (metric in ("cpu_pct", "failed_logins_1m") and val >= 10) else "high"
            inc_id = f"INC-{str(uuid.uuid4())[:6].upper()}"

            conn.execute(
                """
                INSERT INTO incidents
                (incident_id, tenant_id, source_hash, status, severity, title, likely_cause,
                 recommended_action, rollback_plan, confidence, signals_json, raw_alert_count, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    inc_id,
                    tenant_id,
                    source_hash,
                    sev,
                    inc_title,
                    cause,
                    action,
                    rollback,
                    confidence,
                    json.dumps([signal_entry]),
                    now_utc,
                    now_utc,
                ),
            )

        conn.commit()
        conn.close()


def _diagnose(metrics_present: set) -> tuple[str, str, str]:
    if "failed_logins_1m" in metrics_present:
        return (
            "Local SSH/IAM brute-force burst detected (raw usernames kept inside Tier 1).",
            "Apply temporary rate-limit policy on source gateway & enforce MFA challenge.",
            "Auto-expire rate-limit rule after 15 minutes or revert firewall policy ID #RL-204.",
        )
    if "cpu_pct" in metrics_present and "latency_ms" in metrics_present:
        return (
            "Compute saturation on host causing request queue latency degradation.",
            "Scale worker pool horizontally (+2 replicas) and shed non-critical batch jobs.",
            "Scale replica count back to baseline (2) once CPU stays <55% for 5 minutes.",
        )
    if "packet_loss_pct" in metrics_present:
        return (
            "Upstream network path packet drops affecting regional link quality.",
            "Shift collector/service traffic to secondary regional failover route.",
            "Restore primary BGP/gateway route once packet loss returns below 0.5%.",
        )
    return (
        f"Statistically significant deviation across {', '.join(sorted(metrics_present))}.",
        "Inspect top local worker threads and throttle background tasks.",
        "Restore default cgroup CPU/memory quota limits.",
    )
