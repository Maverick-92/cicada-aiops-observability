import { useCallback, useEffect, useMemo, useState } from "react";
import FleetPanel from "./FleetPanel.jsx";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Activity, CheckCircle2, ExternalLink, Server, ShieldCheck, TriangleAlert } from "lucide-react";

const API = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");
const COLLECTOR_URL = "http://127.0.0.1:8001/";

// DEMO ONLY: in production, tokens never live in browser code.
const TENANTS = [
  { id: "tenant_acme", name: "Acme India", region: "ap-south-1", token: "token-acme-ap-south" },
  { id: "tenant_global", name: "Acme Global (600 servers)", region: "us-east-1", token: "token-global-us-east" },
  { id: "tenant_globex", name: "Globex EU", region: "eu-central-1", token: "token-globex-eu-central" },
  { id: "tenant_initech", name: "Initech US", region: "us-east-1", token: "token-initech-us-east" },
];

const HEALTH_STATES = ["Excellent", "Good", "Fair", "Poor"];

const HEALTH_STYLE = {
  Excellent: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  Good: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  Fair: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  Poor: "bg-red-500/15 text-red-300 border-red-500/40",
};

const fmtUtcTime = (ts) => {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d) ? "" : `${d.toISOString().slice(11, 19)}Z`;
};

const fmtMs = (ms) => {
  if (ms === null || ms === undefined) return "--";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
};

function Card({ title, icon: Icon, children, right }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-300">
          {Icon && <Icon size={16} />} {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export default function App() {
  const [tenant, setTenant] = useState(TENANTS[0]);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/dashboard`, {
        headers: { Authorization: `Bearer ${tenant.token}` },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError("");
    } catch (e) {
      setError(`Cannot reach backend at ${API} (${e.message}). Is it running?`);
    }
  }, [tenant]);

  useEffect(() => {
    setData(null);
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const approve = async (id) => {
    setBusy(id);
    try {
      const r = await fetch(`${API}/api/incidents/${id}/approve`, {
        method: "POST",
        headers: { Authorization: `Bearer ${tenant.token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ operator_name: "On-Call SRE" }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e) {
      setError(`Approve failed: ${e.message}`);
    } finally {
      setBusy("");
    }
  };

  // Group chronologically by UTC second so all 6 metrics plot cleanly
  const chart = useMemo(() => {
    if (!data?.metrics) return [];
    const buckets = new Map();
    for (const m of data.metrics) {
      if (!m.ts_utc) continue;
      const key = String(m.ts_utc).slice(0, 19);
      if (!buckets.has(key)) buckets.set(key, { t: fmtUtcTime(m.ts_utc), _acc: {} });
      const b = buckets.get(key);
      const acc = b._acc[m.metric] || { sum: 0, n: 0 };
      acc.sum += Number(m.value);
      acc.n += 1;
      b._acc[m.metric] = acc;
    }
    return Array.from(buckets.values()).map((b) => {
      const row = { t: b.t };
      for (const [k, v] of Object.entries(b._acc)) {
        row[k] = Number((v.sum / v.n).toFixed(2));
      }
      return row;
    });
  }, [data]);

  const health = data?.health;
  const slo = data?.slo;
  const sloPass = slo?.p95_ms !== null && slo?.p95_ms !== undefined ? slo.p95_ms <= (slo.target_p95_ms || 10000) : true;

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="text-emerald-400" />
          <div>
            <h1 className="text-lg font-bold">Cicada Zero-Trust AIOps Console</h1>
            <p className="text-xs text-slate-400">
              Tier 2 Multi-Tenant SaaS {"\u00b7"} Strict 12-field metadata only {"\u00b7"} UTC event-time
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <select
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm"
            value={tenant.id}
            onChange={(e) => setTenant(TENANTS.find((t) => t.id === e.target.value))}
          >
            {TENANTS.map((t) => (
              <option key={t.id} value={t.id}>{t.name} ({t.region})</option>
            ))}
          </select>
          <a
            href={COLLECTOR_URL}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-sm font-medium text-emerald-300 hover:bg-emerald-500/20"
          >
            Tier 1 Egress Proof (127.0.0.1:8001) <ExternalLink size={14} />
          </a>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">{error}</div>
      )}

      {!data && !error && <p className="text-slate-400">Loading tenant telemetry...</p>}

      {data && (
        <>
          {/* Phase 3 Real-Time SLO, Residency & Zero-Trust Verification Strip */}
          <div className="grid gap-2 rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-xs sm:grid-cols-2 lg:grid-cols-5">
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5">
              <div className="text-slate-400">Zero-Trust Allow-List</div>
              <div className="mt-0.5 font-semibold text-emerald-300">12 Fields Only (422 Enforced)</div>
              <div className="text-slate-500">0 raw logs {"\u00b7"} 0 PII {"\u00b7"} HMAC srv_ hash</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5">
              <div className="text-slate-400">Ingest SLO (Target {"\u2264"} 10s)</div>
              <div className={`mt-0.5 font-semibold ${sloPass ? "text-emerald-300" : "text-amber-300"}`}>
                p50 {fmtMs(slo?.p50_ms)} / p95 {fmtMs(slo?.p95_ms)}
              </div>
              <div className="text-slate-500">{slo?.samples || 0} samples {"\u00b7"} {slo?.late_excluded || 0} late excluded</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5">
              <div className="text-slate-400">Offline & Stale Rule ({"\u2264"} 60s)</div>
              <div className={`mt-0.5 font-semibold ${(health.stale_collectors || 0) > 0 ? "text-red-400" : "text-emerald-300"}`}>
                {health.online_collectors ?? 0} online {"\u00b7"} {health.stale_collectors ?? 0} stale
              </div>
              <div className="text-slate-500">Tier 1 WAL disk buffer + backoff retry</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5">
              <div className="text-slate-400">Idempotency & Zero Loss</div>
              <div className="mt-0.5 font-semibold text-sky-300">
                {data.duplicates_blocked ?? 0} duplicates blocked
              </div>
              <div className="text-slate-500">UNIQUE(tenant_id, payload_id)</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5">
              <div className="text-slate-400">Data Residency & Alert SLO</div>
              <div className="mt-0.5 font-semibold text-slate-200">
                Region {data.region} {"\u00b7"} UTC
              </div>
              <div className="text-slate-500">Alert {"\u2264"} 15s {"\u00b7"} 403 on region mismatch</div>
            </div>
          </div>

          {/* Top 4 KPI Summary Cards */}
          <div className="grid gap-4 md:grid-cols-4">
            <Card title="4-State Tenant Health" icon={Activity}>
              <div className="flex flex-wrap gap-1.5">
                {HEALTH_STATES.map((s) => (
                  <span
                    key={s}
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${health.state === s ? HEALTH_STYLE[s] : "border-slate-800 text-slate-600"
                      }`}
                  >
                    {s}
                  </span>
                ))}
              </div>
              <p className="mt-2 text-xs text-slate-400">{health.reason}</p>
            </Card>
            <Card title="Fleet Average (All Servers)">
              <p className="text-xl font-bold">
                {health.latest_cpu}% <span className="text-xs font-normal text-slate-400">CPU</span>{" "}
                <span className="text-slate-600">/</span>{" "}
                {health.latest_mem ?? "-"}% <span className="text-xs font-normal text-slate-400">MEM</span>
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Disk {health.latest_disk ?? "-"}% {"\u00b7"} Latency {health.latest_latency} ms
                {health.servers_averaged ? ` \u00b7 avg of ${health.servers_averaged} servers` : ""}
              </p>
            </Card>
            <Card title="AIOps Noise Reduction">
              <p className="text-2xl font-bold">
                {data.noise_reduction.raw_anomalies}{" "}
                <span className="text-slate-500">alerts {"\u2192"}</span>{" "}
                {data.noise_reduction.correlated_incidents}
              </p>
              <p className="text-xs text-slate-400">Z-score anomalies correlated into incidents</p>
            </Card>
            <Card title="Metadata Events Stored">
              <p className="text-2xl font-bold">{data.total_events_stored}</p>
              <p className="text-xs text-slate-400">tenant {data.tenant_id} {"\u00b7"} {data.region}</p>
            </Card>
          </div>

          <FleetPanel api={API} token={tenant.token} />

          {/* All 6 Metrics Graphed in Event-Time (UTC) */}
          <div className="grid gap-4 lg:grid-cols-2">
            <Card
              title="System Utilization (CPU %, Memory %, Disk %)"
              right={<span className="text-xs text-slate-400">Event-time order (UTC)</span>}
            >
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chart}>
                    <CartesianGrid stroke="#1e293b" />
                    <XAxis dataKey="t" stroke="#64748b" fontSize={11} />
                    <YAxis domain={[0, 100]} stroke="#94a3b8" fontSize={11} unit="%" />
                    <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} />
                    <Legend />
                    <Line dataKey="cpu_pct" name="CPU %" stroke="#38bdf8" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                    <Line dataKey="mem_pct" name="Memory %" stroke="#a78bfa" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                    <Line dataKey="disk_pct" name="Disk %" stroke="#34d399" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card
              title="Network Telemetry (Latency, Bandwidth, Packet Loss)"
              right={<span className="text-xs text-slate-400">Event-time order (UTC)</span>}
            >
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chart}>
                    <CartesianGrid stroke="#1e293b" />
                    <XAxis dataKey="t" stroke="#64748b" fontSize={11} />
                    <YAxis yAxisId="l" stroke="#f59e0b" fontSize={11} />
                    <YAxis yAxisId="r" orientation="right" stroke="#22d3ee" fontSize={11} />
                    <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} />
                    <Legend />
                    <Line yAxisId="l" dataKey="latency_ms" name="Latency (ms)" stroke="#f59e0b" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                    <Line yAxisId="r" dataKey="bandwidth_mbps" name="Bandwidth (Mbps)" stroke="#22d3ee" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                    <Line yAxisId="l" dataKey="packet_loss_pct" name="Packet Loss (%)" stroke="#f43f5e" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          {/* Actionable AIOps Incidents */}
          <Card
            title="Actionable AIOps Incidents (Correlated Signals & Human-in-the-Loop Remediation)"
            icon={TriangleAlert}
            right={<span className="text-xs text-slate-400">Explainable cause + rollback plan</span>}
          >
            {data.incidents.length === 0 && (
              <p className="text-sm text-slate-400">
                No active incidents. Trigger a live scenario from Tier 1 (127.0.0.1:8001) or simulator --spike to demonstrate detection & remediation.
              </p>
            )}
            <div className="space-y-3">
              {data.incidents.map((i) => (
                <div key={i.incident_id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-semibold">
                      {i.incident_id} <span className="text-slate-400">{"\u00b7"} {i.title}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="rounded bg-amber-500/15 px-2 py-0.5 text-amber-300">{i.severity}</span>
                      <span className="rounded bg-sky-500/15 px-2 py-0.5 text-sky-300">confidence {Math.round(i.confidence * 100)}%</span>
                      <span className="rounded bg-slate-800 px-2 py-0.5 text-slate-200">{i.status}</span>
                    </div>
                  </div>
                  <div className="mt-2 grid gap-2 text-sm md:grid-cols-3">
                    <div className="rounded border border-slate-800/80 bg-slate-900/50 p-2">
                      <div className="text-xs font-semibold uppercase text-slate-400">Root Cause</div>
                      <div className="mt-0.5">{i.likely_cause}</div>
                    </div>
                    <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2">
                      <div className="text-xs font-semibold uppercase text-emerald-300">Why This Action</div>
                      <div className="mt-0.5">{i.recommended_action}</div>
                    </div>
                    <div className="rounded border border-slate-800/80 bg-slate-900/50 p-2">
                      <div className="text-xs font-semibold uppercase text-slate-400">Rollback Safety Plan</div>
                      <div className="mt-0.5 text-slate-300">{i.rollback_plan}</div>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-slate-400">
                    <b>Correlated signals:</b>{" "}
                    {i.signals.map((s) => `${s.metric}=${s.value} (Z=${s.z_score})`).join(" \u00b7 ")}{" "}
                    {"\u00b7"} {i.raw_alert_count} raw alerts correlated
                  </p>
                  {i.status === "OPEN" && (
                    <button
                      onClick={() => approve(i.incident_id)}
                      disabled={busy === i.incident_id}
                      className="mt-2 flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
                    >
                      <CheckCircle2 size={14} /> Approve remediation (simulated)
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Card>

          {/* Zero-Trust SaaS Metadata Proof (12-Field Allow-List) */}
          <Card
            title="Zero-Trust SaaS Ingest Proof (Latest Allow-Listed Metadata Received)"
            icon={ShieldCheck}
            right={
              <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-300">
                12 / 12 fields allowed {"\u00b7"} 0 raw logs / 0 secrets
              </span>
            }
          >
            {data.latest_payload ? (
              <div className="grid gap-3 md:grid-cols-2">
                <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-xs text-emerald-300">
                  {JSON.stringify(data.latest_payload, null, 2)}
                </pre>
                <div className="space-y-2 text-xs text-slate-300">
                  <p className="font-semibold text-slate-200">What this proves for Tenant {data.tenant_id}:</p>
                  <ul className="list-disc space-y-1 pl-4 text-slate-400">
                    <li><b>Pseudonymous host ID:</b> <span className="font-mono text-slate-200">{data.latest_payload.source_hash}</span> (HMAC-SHA256 hashed inside Tier 1).</li>
                    <li><b>Strict schema gate:</b> Both Tier 1 and Tier 2 reject any extra or missing key with <span className="font-mono text-amber-300">HTTP 422</span>.</li>
                    <li><b>Regional isolation:</b> Token pinned to <span className="font-mono text-slate-200">{data.region}</span> (<span className="font-mono text-amber-300">HTTP 403</span> on cross-region/tenant attempts).</li>
                    <li>
                      <b>Compare live:</b> Open{" "}
                      <a
                        href={COLLECTOR_URL}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 font-semibold text-emerald-300 underline hover:text-emerald-200"
                      >
                        Tier 1 Egress Proof (127.0.0.1:8001) <ExternalLink size={12} />
                      </a>{" "}
                      side-by-side to see raw secrets scrubbed before leaving the customer network.
                    </li>
                  </ul>
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-400">Waiting for first metadata payload...</p>
            )}
          </Card>

          {/* Collector Fleet & UTC Health History */}
          <div className="grid gap-4 md:grid-cols-2">
            <Card
              title="Collector Fleet & Stale Detection (>60s = Offline)"
              icon={Server}
            >
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-slate-400">
                  <tr><th>Collector</th><th>Host hash</th><th>Last seen</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {data.collectors.map((c) => (
                    <tr key={c.collector_id + c.source_hash} className="border-t border-slate-800">
                      <td>{c.collector_id}</td>
                      <td className="font-mono text-xs">{c.source_hash}</td>
                      <td>{c.seconds_ago}s ago</td>
                      <td className={c.status === "ONLINE" ? "text-emerald-400" : "font-semibold text-red-400"}>{c.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card title="Health Transition History (UTC)">
              {data.health_history.length === 0 && <p className="text-sm text-slate-400">No transitions yet.</p>}
              <ul className="space-y-1 text-sm">
                {data.health_history.map((h, idx) => (
                  <li key={idx}>
                    <span className="font-mono text-xs text-slate-500">{fmtUtcTime(h.ts_utc)}</span>{" "}
                    {h.previous_state} {"\u2192"} <b>{h.new_state}</b>
                    <span className="text-slate-400"> {"\u00b7"} {h.reason}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          {/* Remediation Audit Log */}
          <Card title="Remediation Audit Log (UTC)">
            {data.audit_logs.length === 0 && <p className="text-sm text-slate-400">No approvals yet.</p>}
            <ul className="space-y-2 text-sm">
              {data.audit_logs.map((a) => (
                <li key={a.id} className="rounded border border-slate-800 p-2">
                  <span className="font-mono text-xs text-slate-500">{fmtUtcTime(a.ts_utc)}</span>{" "}
                  {"\u00b7"} {a.incident_id} {"\u00b7"} approved by <b>{a.approved_by}</b> {"\u00b7"} {a.policy_check}
                  <div className="text-slate-400">Action: {a.action_taken}</div>
                  <div className="text-slate-400">Rollback: {a.rollback_plan} ({a.rollback_status})</div>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  );
}