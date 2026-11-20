import os
import sqlite3
import threading
from datetime import datetime, timezone

SAAS_DB = os.path.join(os.path.dirname(__file__), "saas_platform.db")
saas_lock = threading.Lock()

# Token -> (tenant_id, allowed_region)
API_TOKENS = {
    "token-acme-ap-south": ("tenant_acme", "ap-south-1"),
    "token-globex-eu-central": ("tenant_globex", "eu-central-1"),
}


def init_saas_db():
    with saas_lock:
        conn = sqlite3.connect(SAAS_DB)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                region TEXT NOT NULL,
                collector_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                category TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                ts_utc TEXT NOT NULL,
                source_tz TEXT NOT NULL,
                severity TEXT NOT NULL,
                context_code TEXT NOT NULL,
                ingested_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, payload_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant_source_ts ON telemetry_events(tenant_id, source_hash, ts_utc)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                likely_cause TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                rollback_plan TEXT NOT NULL,
                confidence REAL NOT NULL,
                signals_json TEXT NOT NULL,
                raw_alert_count INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS remediation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                action_taken TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                policy_check TEXT NOT NULL,
                rollback_plan TEXT NOT NULL,
                ts_utc TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()


def insert_payload_idempotent(payload: dict) -> bool:
    """Returns True if newly inserted, False if duplicate payload_id ignored."""
    now_utc = datetime.now(timezone.utc).isoformat()
    with saas_lock:
        conn = sqlite3.connect(SAAS_DB)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO telemetry_events
            (payload_id, tenant_id, region, collector_id, source_hash, category, metric, value, ts_utc, source_tz, severity, context_code, ingested_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["payload_id"],
                payload["tenant_id"],
                payload["region"],
                payload["collector_id"],
                payload["source_hash"],
                payload["category"],
                payload["metric"],
                payload["value"],
                payload["ts_utc"],
                payload["source_tz"],
                payload["severity"],
                payload["context_code"],
                now_utc,
            ),
        )
        inserted = cur.rowcount == 1
        conn.commit()
        conn.close()
    return inserted
