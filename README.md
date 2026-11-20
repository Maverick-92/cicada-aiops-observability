# Zero-Trust Two-Tier AIOps Observability Platform (CICADA 2026)

Local-first observability platform that scrubs secrets inside the customer environment (Tier 1) and transmits only a strict 12-field metadata allow-list to a multi-tenant AIOps SaaS dashboard (Tier 2).

## Folder Ownership
- `collector/` — Person 1: Telemetry ingestion, secret redaction, HMAC-SHA256 source hashing, strict allow-list gate, SQLite WAL offline buffer, Egress Proof Panel (`:8001`).
- `backend/` — Person 2: FastAPI multi-tenant ingest API (`:8000`), `payload_id` idempotency, `ts_utc` event-time sorting, regional residency checks.
- `frontend/` — Person 3: React + Vite + Tailwind live dashboard (`:5173`), 4 health states, 60s stale detector, incident & remediation UI.
- `simulator/` — Person 4: Telemetry stream runner + live demo fault injector (secret leak, SaaS outage, failed login burst, CPU spike, duplicate/out-of-order replay).
