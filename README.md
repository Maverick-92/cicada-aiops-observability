# Sentinel AIOps: Zero-Trust Two-Tier Observability Console (CICADA 2026)

React + Vite + Tailwind + Recharts frontend. All data is **simulated** (see `src/data/mock.js`).
Only the 12 allow-listed metadata fields are shown as leaving the customer environment:
`payload_id, tenant_id, region, collector_id, source_hash, category, metric, value, ts_utc, source_tz, severity, context_code`

## Run
```bash
npm install
cp .env.example .env
npm run dev
```
## Backend later
Set `VITE_USE_MOCK=false` and `VITE_API_BASE_URL` (FastAPI). API calls live in `src/services/api.js`; WebSocket stub in `src/services/ws.js`.
