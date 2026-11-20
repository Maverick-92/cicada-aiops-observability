import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import requests
from pydantic import BaseModel, ConfigDict, ValidationError

SAAS_INGEST_URL = os.getenv("SAAS_INGEST_URL", "http://localhost:8000/ingest")
COLLECTOR_TOKEN = os.getenv("COLLECTOR_TOKEN", "token-acme-ap-south")
TENANT_ID = "tenant_acme"
REGION = "ap-south-1"
COLLECTOR_ID = "col-acme-01"
SOURCE_TZ = "Asia/Kolkata"

# Customer-held secret salt (NEVER leaves Tier 1)
LOCAL_SALT = b"acme-private-local-salt-2026"

BUFFER_DB = os.path.join(os.path.dirname(__file__), "collector_buffer.db")
EGRESS_DB = os.path.join(os.path.dirname(__file__), "collector_egress.db")

db_lock = threading.Lock()


class ApprovedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_id: str
    tenant_id: str
    region: str
    collector_id: str
    source_hash: str
    category: Literal["infrastructure", "network", "security", "fleet"]
    metric: Literal[
        "cpu_pct",
        "mem_pct",
        "disk_pct",
        "bandwidth_mbps",
        "latency_ms",
        "packet_loss_pct",
        "failed_logins_1m",
        "collector_heartbeat",
    ]
    value: float
    ts_utc: str
    source_tz: str
    severity: Literal["low", "medium", "high", "critical"]
    context_code: Literal[
        "NORMAL_SAMPLE",
        "CPU_SPIKE",
        "LATENCY_DEGRADED",
        "PACKET_LOSS_HIGH",
        "AUTH_BURST",
        "HEARTBEAT_OK",
    ]


def init_dbs():
    with db_lock:
        b_conn = sqlite3.connect(BUFFER_DB)
        b_conn.execute("PRAGMA journal_mode=WAL;")
        b_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbound_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_id TEXT UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        b_conn.commit()
        b_conn.close()

        e_conn = sqlite3.connect(EGRESS_DB)
        e_conn.execute("PRAGMA journal_mode=WAL;")
        e_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS egress_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                payload_id TEXT NOT NULL,
                raw_preview TEXT NOT NULL,
                stripped_fields TEXT NOT NULL,
                raw_bytes INTEGER NOT NULL,
                sent_bytes INTEGER NOT NULL,
                outbound_json TEXT NOT NULL,
                delivery_status TEXT NOT NULL
            )
            """
        )
        e_conn.commit()
        e_conn.close()


SECRET_PATTERNS = [
    (re.compile(r"(password|passwd|pwd|secret|token|api_key)\s*=\s*\S+", re.I), r"\1=[REDACTED]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
    (re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"), "[REDACTED_EMAIL]"),
]


def scrub_raw_text(raw_line: str) -> str:
    cleaned = raw_line
    for pattern, replacement in SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def hash_hostname(real_hostname: str) -> str:
    digest = hmac.new(LOCAL_SALT, real_hostname.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"srv_{digest[:12]}"


def process_raw_event(raw_event: dict) -> Optional[dict]:
    raw_line = raw_event.get("raw_log", "")
    scrubbed_preview = scrub_raw_text(raw_line)

    allowed_keys = set(ApprovedPayload.model_fields.keys())
    stripped = [k for k in raw_event.keys() if k not in allowed_keys]
    if "password=" in raw_line.lower() or "token=" in raw_line.lower():
        stripped.append("inline_credentials")

    real_host = raw_event.get("hostname", "prod-app-mumbai-01")
    source_hash = hash_hostname(real_host)

    metric = raw_event["metric"]
    val = float(raw_event["value"])

    severity = "low"
    context_code = "NORMAL_SAMPLE"

    if metric == "cpu_pct" and val >= 90:
        severity, context_code = "critical", "CPU_SPIKE"
    elif metric == "cpu_pct" and val >= 70:
        severity, context_code = "medium", "CPU_SPIKE"
    elif metric == "latency_ms" and val >= 250:
        severity, context_code = "high", "LATENCY_DEGRADED"
    elif metric == "packet_loss_pct" and val >= 5.0:
        severity, context_code = "high", "PACKET_LOSS_HIGH"
    elif metric == "failed_logins_1m" and val >= 10:
        severity, context_code = "critical", "AUTH_BURST"
    elif metric == "collector_heartbeat":
        severity, context_code = "low", "HEARTBEAT_OK"

    candidate = {
        "payload_id": raw_event.get("override_payload_id") or str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "region": REGION,
        "collector_id": COLLECTOR_ID,
        "source_hash": source_hash,
        "category": raw_event.get("category", "infrastructure"),
        "metric": metric,
        "value": round(val, 2),
        "ts_utc": raw_event.get("override_ts_utc") or datetime.now(timezone.utc).isoformat(),
        "source_tz": SOURCE_TZ,
        "severity": severity,
        "context_code": context_code,
    }

    try:
        approved = ApprovedPayload(**candidate)
    except ValidationError as e:
        print(f"[POLICY BLOCKED] Payload failed allow-list check: {e}")
        return None

    outbound_dict = approved.model_dump()
    outbound_json = json.dumps(outbound_dict)
    raw_bytes = len(json.dumps(raw_event).encode("utf-8"))
    sent_bytes = len(outbound_json.encode("utf-8"))

    with db_lock:
        b_conn = sqlite3.connect(BUFFER_DB)
        b_conn.execute(
            "INSERT OR IGNORE INTO outbound_queue (payload_id, payload_json, created_at) VALUES (?, ?, ?)",
            (approved.payload_id, outbound_json, approved.ts_utc),
        )
        b_conn.execute(
            "DELETE FROM outbound_queue WHERE id NOT IN (SELECT id FROM outbound_queue ORDER BY id DESC LIMIT 5000)"
        )
        b_conn.commit()
        b_conn.close()

        e_conn = sqlite3.connect(EGRESS_DB)
        e_conn.execute(
            """
            INSERT INTO egress_audit
            (ts_utc, payload_id, raw_preview, stripped_fields, raw_bytes, sent_bytes, outbound_json, delivery_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approved.ts_utc,
                approved.payload_id,
                scrubbed_preview[:240],
                ", ".join(sorted(set(stripped))) or "none",
                raw_bytes,
                sent_bytes,
                outbound_json,
                "QUEUED",
            ),
        )
        e_conn.commit()
        e_conn.close()

    return outbound_dict


def drain_queue_worker():
    backoff = 1.0
    while True:
        with db_lock:
            b_conn = sqlite3.connect(BUFFER_DB)
            rows = b_conn.execute(
                "SELECT id, payload_id, payload_json FROM outbound_queue ORDER BY id ASC LIMIT 25"
            ).fetchall()
            b_conn.close()

        if not rows:
            time.sleep(0.5)
            continue

        batch = [json.loads(r[2]) for r in rows]
        ids = [r[0] for r in rows]
        payload_ids = [r[1] for r in rows]

        try:
            resp = requests.post(
                SAAS_INGEST_URL,
                json=batch,
                headers={"Authorization": f"Bearer {COLLECTOR_TOKEN}"},
                timeout=3.0,
            )
            if resp.status_code in (200, 201):
                with db_lock:
                    b_conn = sqlite3.connect(BUFFER_DB)
                    b_conn.executemany("DELETE FROM outbound_queue WHERE id = ?", [(i,) for i in ids])
                    b_conn.commit()
                    b_conn.close()

                    e_conn = sqlite3.connect(EGRESS_DB)
                    e_conn.executemany(
                        "UPDATE egress_audit SET delivery_status = 'DELIVERED' WHERE payload_id = ?",
                        [(pid,) for pid in payload_ids],
                    )
                    e_conn.commit()
                    e_conn.close()

                backoff = 1.0
                print(f"[SENT OK] Delivered {len(batch)} allow-listed payload(s) to SaaS.")
            else:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        except Exception as exc:
            with db_lock:
                e_conn = sqlite3.connect(EGRESS_DB)
                e_conn.executemany(
                    "UPDATE egress_audit SET delivery_status = 'BUFFERED_OFFLINE' WHERE payload_id = ?",
                    [(pid,) for pid in payload_ids],
                )
                e_conn.commit()
                e_conn.close()

            jitter = random.uniform(0.1, 0.6)
            wait_time = min(backoff + jitter, 15.0)
            print(f"[OFFLINE BUFFER] SaaS unreachable ({exc}). {len(rows)} queued. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)
            backoff = min(backoff * 2, 15.0)


def telemetry_loop():
    tick = 0
    while True:
        tick += 1
        process_raw_event(
            {
                "hostname": "hdfc-mumbai-core-db-01",
                "ip_address": "10.240.12.88",
                "username": "svc_deploy",
                "raw_log": f"2026-11-21 server=hdfc-mumbai-core-db-01 user=svc_deploy ip=10.240.12.88 status=OK tick={tick}",
                "category": "infrastructure",
                "metric": "cpu_pct",
                "value": round(random.uniform(24.0, 38.0), 2),
            }
        )
        process_raw_event(
            {
                "hostname": "hdfc-mumbai-core-db-01",
                "ip_address": "10.240.12.88",
                "raw_log": "probe target=10.240.12.1 rtt_ok",
                "category": "network",
                "metric": "latency_ms",
                "value": round(random.uniform(28.0, 48.0), 2),
            }
        )
        if tick % 5 == 0:
            process_raw_event(
                {
                    "hostname": "hdfc-mumbai-core-db-01",
                    "raw_log": "collector_watchdog version=1.0.0 cpu=1.2% mem=28MB",
                    "category": "fleet",
                    "metric": "collector_heartbeat",
                    "value": 1.0,
                }
            )
        time.sleep(2.0)
