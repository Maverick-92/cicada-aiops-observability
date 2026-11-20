import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

SAAS_DB = os.path.join(os.path.dirname(__file__), "saas_platform.db")
saas_lock = threading.Lock()

# Backward-compatible token map + extended global registry
API_TOKENS = {
    "token-acme-ap-south": ("tenant_acme", "ap-south-1"),
    "token-globex-eu-central": ("tenant_globex", "eu-central-1"),
    "token-initech-us-east": ("tenant_initech", "us-east-1"),
}


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(SAAS_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_saas_db():
    with saas_lock:
        conn = get_connection()

        # 1. TENANTS & DATA RESIDENCY CONFIGURATION
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                home_region TEXT NOT NULL,
                residency_enforced INTEGER NOT NULL DEFAULT 1,
                cpu_fair_threshold REAL NOT NULL DEFAULT 70.0,
                cpu_poor_threshold REAL NOT NULL DEFAULT 90.0,
                latency_fair_threshold REAL NOT NULL DEFAULT 250.0,
                stale_timeout_sec INTEGER NOT NULL DEFAULT 60,
                created_at_utc TEXT NOT NULL
            )
            """
        )

        # 2. COLLECTOR AUTHENTICATION TOKENS (ZERO-TRUST REGISTRY)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_tokens (
                token_hash TEXT PRIMARY KEY,
                token_hint TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                allowed_region TEXT NOT NULL,
                collector_id TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
            )
            """
        )

        # 3. COLLECTOR FLEET HEALTH & HEARTBEAT TELEMETRY
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_fleet (
                tenant_id TEXT NOT NULL,
                collector_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                region TEXT NOT NULL,
                agent_version TEXT NOT NULL DEFAULT 'v1.4.2-zt',
                agent_cpu_pct REAL NOT NULL DEFAULT 1.1,
                agent_mem_mb REAL NOT NULL DEFAULT 28.4,
                config_status TEXT NOT NULL DEFAULT 'POLICY_ENFORCED',
                last_heartbeat_utc TEXT NOT NULL,
                total_payloads_accepted INTEGER NOT NULL DEFAULT 0,
                total_duplicates_blocked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, collector_id, source_hash)
            )
            """
        )

        # 4. METADATA-ONLY TELEMETRY EVENTS (STRICT ALLOW-LIST + EVENT-TIME INDEXED)
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
            "CREATE INDEX IF NOT EXISTS idx_events_tenant_source_ts ON telemetry_events(tenant_id, source_hash, ts_utc DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_tenant_metric_ts ON telemetry_events(tenant_id, metric, ts_utc DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_tenant_source_metric_ts ON telemetry_events(tenant_id, source_hash, metric, ts_utc DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_tenant_ts ON telemetry_events(tenant_id, ts_utc DESC)"
        )

        # 5. AUDITABLE HEALTH STATE TRANSITIONS (UTC TIMESTAMPED)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS health_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                previous_state TEXT NOT NULL,
                new_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                ts_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_tenant_ts ON health_transitions(tenant_id, ts_utc DESC)"
        )

        # 6. CORRELATED AIOPS INCIDENTS & EXPLAINABLE RECOMMENDATIONS
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
            "CREATE INDEX IF NOT EXISTS idx_incidents_tenant_status ON incidents(tenant_id, status, updated_at_utc DESC)"
        )

        # 7. AUTONOMOUS OPS POLICY, APPROVAL & ROLLBACK AUDIT TRAIL
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
                rollback_status TEXT NOT NULL DEFAULT 'READY',
                ts_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON remediation_audit(tenant_id, ts_utc DESC)"
        )

        # SEED MULTI-TENANT GLOBAL ORGANIZATIONS & TOKENS
        now_utc = datetime.now(timezone.utc).isoformat()
        seed_tenants = [
            ("tenant_acme", "Acme Financial India Pvt Ltd", "ap-south-1", 1, 70.0, 90.0, 250.0, 60, now_utc),
            ("tenant_globex", "Globex Cloud GmbH (Frankfurt)", "eu-central-1", 1, 65.0, 85.0, 200.0, 60, now_utc),
            ("tenant_initech", "Initech Logistics US East", "us-east-1", 1, 75.0, 92.0, 300.0, 60, now_utc),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO tenants
            (tenant_id, company_name, home_region, residency_enforced, cpu_fair_threshold, cpu_poor_threshold, latency_fair_threshold, stale_timeout_sec, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            seed_tenants,
        )

        seed_tokens = [
            (_hash_token("token-acme-ap-south"), "token-acme-***", "tenant_acme", "ap-south-1", "col-acme-01", 1, now_utc),
            (_hash_token("token-globex-eu-central"), "token-globex-***", "tenant_globex", "eu-central-1", "col-globex-01", 1, now_utc),
            (_hash_token("token-initech-us-east"), "token-initech-***", "tenant_initech", "us-east-1", "col-initech-01", 1, now_utc),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO collector_tokens
            (token_hash, token_hint, tenant_id, allowed_region, collector_id, is_active, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            seed_tokens,
        )

        conn.commit()
        conn.close()


def verify_token_from_db(raw_token: str) -> Optional[tuple[str, str]]:
    """Verifies hashed token against collector_tokens registry."""
    t_hash = _hash_token(raw_token)
    with saas_lock:
        conn = get_connection()
        row = conn.execute(
            "SELECT tenant_id, allowed_region FROM collector_tokens WHERE token_hash = ? AND is_active = 1",
            (t_hash,),
        ).fetchone()
        conn.close()
    if row:
        return (row["tenant_id"], row["allowed_region"])
    return None


def insert_payload_idempotent(payload: dict) -> bool:
    """
    Idempotent write:
    - Enforces UNIQUE(tenant_id, payload_id) so retries never duplicate rows.
    - Updates collector_fleet heartbeat, payload counters, and duplicate counters.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    with saas_lock:
        conn = get_connection()
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

        # Upsert collector fleet metadata (keeps latest event-time heartbeat)
        conn.execute(
            """
            INSERT INTO collector_fleet
            (tenant_id, collector_id, source_hash, region, last_heartbeat_utc, total_payloads_accepted, total_duplicates_blocked)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, collector_id, source_hash) DO UPDATE SET
                last_heartbeat_utc = CASE
                    WHEN excluded.last_heartbeat_utc > collector_fleet.last_heartbeat_utc
                    THEN excluded.last_heartbeat_utc
                    ELSE collector_fleet.last_heartbeat_utc
                END,
                total_payloads_accepted = collector_fleet.total_payloads_accepted + ?,
                total_duplicates_blocked = collector_fleet.total_duplicates_blocked + ?
            """,
            (
                payload["tenant_id"],
                payload["collector_id"],
                payload["source_hash"],
                payload["region"],
                payload["ts_utc"],
                1 if inserted else 0,
                0 if inserted else 1,
                1 if inserted else 0,
                0 if inserted else 1,
            ),
        )

        conn.commit()
        conn.close()
    return inserted


def record_health_transition(tenant_id: str, new_state: str, reason: str):
    """Logs timestamped UTC state changes whenever fleet health transitions."""
    now_utc = datetime.now(timezone.utc).isoformat()
    with saas_lock:
        conn = get_connection()
        last = conn.execute(
            "SELECT new_state FROM health_transitions WHERE tenant_id = ? ORDER BY id DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()
        prev_state = last["new_state"] if last else "INIT"
        if prev_state != new_state:
            conn.execute(
                """
                INSERT INTO health_transitions (tenant_id, previous_state, new_state, reason, ts_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tenant_id, prev_state, new_state, reason, now_utc),
            )
            conn.commit()
        conn.close()
