import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Activity, CheckCircle2, ExternalLink, Server, ShieldCheck, TriangleAlert } from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const COLLECTOR_URL = "http://localhost:8001";

// DEMO ONLY: in production, tokens never live in browser code.
const TENANTS = [
  { id: "tenant_acme", name: "Acme India", region: "ap-south-1", token: "token-acme-ap-south" },
  { id: "tenant_globex", name: "Globex EU", region: "eu-central-1", token: "token-globex-eu-central" },
  { id: "tenant_initech", name: "Initech US", region: "us-east-1", token: "token-initech-us-east" },
];

const HEALTH_STYLE = {
  Excellent: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  Good: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  Fair: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  Poor: "bg-red-500/15 text-red-300 border-red-500/40",
};

const fmtTime = (ts) => {
  const d = new Date(ts);
  return isNaN(d) ? "" : d.toLocaleTimeString();
};

function Card({ title, icon: Icon, children, right }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-3 flex items-center justify-between">
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

  const chart = useMemo(() => {
    if (!data) return [];
    return data.metrics
      .filter((m) => m.metric === "cpu_pct" || m.metric === "latency_ms")
      .map((m) => ({
        t: fmtTime(m.ts_utc),
        cpu: m.metric === "cpu_pct" ? m.value : undefined,
        latency: m.metric === "latency_ms" ? m.value : undefined,
      }));
  }, [data]);

  const health = data?.health;

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="text-emerald-400" />
          <h1 className="text-lg font-bold">Zero-Trust AIOps Console</h1>
        </div>
        <div className="flex items-center gap-3">
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
            className="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800"
          >
            Egress Proof Panel <ExternalLink size={14} />
          </a>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">{error}</div>
      )}

      {!data && !error && <p className="text-slate-400">Loading...</p>}

      {data && (
        <>
          <div className="grid gap-4 md:grid-cols-4">
            <Card title="Health" icon={Activity}>
              <span className={`inline-block rounded-full border px-3 py-1 text-sm font-semibold ${HEALTH_STYLE[health.state]}`}>
                {health.state}
              </span>
              <p className="mt-2 text-xs text-slate-400">{health.reason}</p>
            </Card>
            <Card title="Latest CPU / Latency">
              <p className="text-2xl font-bold">{health.latest_cpu}% <span className="text-slate-500">/</span> {health.latest_latency}ms</p>
            </Card>
            <Card title="Noise reduction">
              <p className="text-2xl font-bold">
                {data.noise_reduction.raw_anomalies} <span className="text-slate-500">alerts →</span> {data.noise_reduction.correlated_incidents}
              </p>
              <p className="text-xs text-slate-400">raw anomalies vs correlated incidents</p>
            </Card>
            <Card title="Events stored">
              <p className="text-2xl font-bold">{data.total_events_stored}</p>
              <p className="text-xs text-slate-400">metadata only, region {data.region}</p>
            </Card>
          </div>

          <Card title="Metrics (event-time order)">
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chart}>
                  <CartesianGrid stroke="#1e293b" />
                  <XAxis dataKey="t" stroke="#64748b" fontSize={11} />
                  <YAxis yAxisId="l" stroke="#38bdf8" fontSize={11} />
                  <YAxis yAxisId="r" orientation="right" stroke="#f59e0b" fontSize={11} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} />
                  <Legend />
                  <Line yAxisId="l" dataKey="cpu" name="CPU %" stroke="#38bdf8" dot={false} connectNulls isAnimationActive={false} />
                  <Line yAxisId="r" dataKey="latency" name="Latency ms" stroke="#f59e0b" dot={false} connectNulls isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Incidents" icon={TriangleAlert}>
            {data.incidents.length === 0 && <p className="text-sm text-slate-400">No incidents.</p>}
            <div className="space-y-3">
              {data.incidents.map((i) => (
                <div key={i.incident_id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-semibold">
                      {i.incident_id} <span className="text-slate-400">· {i.title}</span>
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="rounded bg-slate-800 px-2 py-0.5">{i.severity}</span>
                      <span className="rounded bg-slate-800 px-2 py-0.5">confidence {Math.round(i.confidence * 100)}%</span>
                      <span className="rounded bg-slate-800 px-2 py-0.5">{i.status}</span>
                    </div>
                  </div>
                  <p className="mt-2 text-sm"><b>Likely cause:</b> {i.likely_cause}</p>
                  <p className="text-sm"><b>Recommended:</b> {i.recommended_action}</p>
                  <p className="text-sm text-slate-400"><b>Rollback:</b> {i.rollback_plan}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    Signals: {i.signals.map((s) => `${s.metric}=${s.value} (Z ${s.z_score})`).join(" · ")} · {i.raw_alert_count} raw alerts
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

          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Collector fleet" icon={Server}>
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
                      <td className={c.status === "ONLINE" ? "text-emerald-400" : "text-red-400"}>{c.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card title="Health history">
              {data.health_history.length === 0 && <p className="text-sm text-slate-400">No transitions yet.</p>}
              <ul className="space-y-1 text-sm">
                {data.health_history.map((h, idx) => (
                  <li key={idx}>
                    <span className="text-slate-500">{fmtTime(h.ts_utc)}</span> {h.previous_state} → <b>{h.new_state}</b>
                    <span className="text-slate-400"> · {h.reason}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          <Card title="Remediation audit log">
            {data.audit_logs.length === 0 && <p className="text-sm text-slate-400">No approvals yet.</p>}
            <ul className="space-y-2 text-sm">
              {data.audit_logs.map((a) => (
                <li key={a.id} className="rounded border border-slate-800 p-2">
                  <span className="text-slate-500">{fmtTime(a.ts_utc)}</span> · {a.incident_id} · approved by <b>{a.approved_by}</b> · {a.policy_check}
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