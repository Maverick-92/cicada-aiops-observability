import { useEffect, useState } from "react";

const STYLE = {
  Excellent: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  Good: "border-sky-500/40 bg-sky-500/10 text-sky-300",
  Fair: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  Poor: "border-red-500/40 bg-red-500/10 text-red-300",
};
const LABEL = { cpu_pct: "CPU", mem_pct: "Memory", disk_pct: "Disk", bandwidth_mbps: "Bandwidth", latency_ms: "Latency", packet_loss_pct: "Packet loss" };
const UNIT = { cpu_pct: "%", mem_pct: "%", disk_pct: "%", bandwidth_mbps: " Mbps", latency_ms: " ms", packet_loss_pct: "%" };
const COLS = ["cpu_pct", "mem_pct", "disk_pct", "bandwidth_mbps", "latency_ms", "packet_loss_pct"];

function Pill({ state }) {
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold ${STYLE[state] || "border-slate-600 text-slate-400"}`}>
      {state}
    </span>
  );
}

export default function FleetPanel({ api, token }) {
  const [sum, setSum] = useState(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState({});
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const [saveMsg, setSaveMsg] = useState("");

  async function load() {
    try {
      const r = await fetch(`${api}/api/fleet/summary`, { headers: { Authorization: `Bearer ${token}` } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setSum(j);
      setErr("");
    } catch (e) {
      setErr(String(e.message || e));
    }
  }

  useEffect(() => {
    setSum(null);
    setEditing(false);
    setSaveMsg("");
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [api, token]);

  const startEdit = () => {
    if (!editing && sum?.thresholds) {
      const d = {};
      for (const m of COLS) {
        if (sum.thresholds[m]) d[m] = { fair: String(sum.thresholds[m].fair), poor: String(sum.thresholds[m].poor) };
      }
      setDraft(d);
    }
    setSaveMsg("");
    setEditing((v) => !v);
  };

  const saveMetric = async (metric) => {
    const fair = Number(draft[metric]?.fair);
    const poor = Number(draft[metric]?.poor);
    if (!Number.isFinite(fair) || !Number.isFinite(poor) || fair < 0 || fair >= poor) {
      setSaveMsg(`Invalid ${LABEL[metric]} limits: require 0 <= fair < poor`);
      return;
    }
    try {
      const r = await fetch(`${api}/api/thresholds`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ metric, fair, poor }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setSaveMsg(`Saved ${LABEL[metric]} (${fair} / ${poor}) — live state updated`);
      await load();
    } catch (e) {
      setSaveMsg(`Save failed: ${String(e.message || e)}`);
    }
  };

  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const box = "rounded-xl border border-slate-800 bg-slate-900/60 p-4";

  if (!sum) return <div className={`${box} text-sm text-slate-400`}>{err ? `Fleet summary unavailable: ${err}` : "Loading fleet summary..."}</div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Live Fleet Telemetry & Policy Thresholds
        </span>
        <div className="flex items-center gap-3">
          {saveMsg && <span className="text-xs text-emerald-300">{saveMsg}</span>}
          <button
            onClick={startEdit}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-1 text-xs font-semibold text-sky-300 hover:bg-slate-700"
          >
            {editing ? "Close threshold editor" : "Edit thresholds (live)"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {sum.metrics.map((m) => (
          <div key={m.metric} className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">{LABEL[m.metric] || m.metric}</span>
              <Pill state={m.state} />
            </div>
            <p className="mt-1 text-xl font-bold">{m.max === null ? "-" : `${m.max}${UNIT[m.metric] || ""}`}</p>
            <p className="text-xs text-slate-400">worst server | avg {m.avg === null ? "-" : m.avg}</p>
            <p className="text-xs text-slate-500">limits fair {m.fair} / poor {m.poor}</p>
            <p className="text-xs text-slate-500">{m.servers_over_fair} of {m.servers_reporting} servers over fair</p>

            {editing && draft[m.metric] && (
              <div className="mt-2 space-y-1.5 border-t border-slate-800 pt-2 text-xs">
                <div className="flex items-center gap-1">
                  <label className="w-9 text-slate-400">Fair</label>
                  <input
                    type="number"
                    value={draft[m.metric].fair}
                    onChange={(e) => setDraft((d) => ({ ...d, [m.metric]: { ...d[m.metric], fair: e.target.value } }))}
                    className="w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-slate-100"
                  />
                </div>
                <div className="flex items-center gap-1">
                  <label className="w-9 text-slate-400">Poor</label>
                  <input
                    type="number"
                    value={draft[m.metric].poor}
                    onChange={(e) => setDraft((d) => ({ ...d, [m.metric]: { ...d[m.metric], poor: e.target.value } }))}
                    className="w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-slate-100"
                  />
                </div>
                <button
                  onClick={() => saveMetric(m.metric)}
                  className="w-full rounded bg-sky-600 py-1 text-xs font-semibold text-white hover:bg-sky-500"
                >
                  Apply
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className={box}>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Fleet by location</h3>
          <span className="text-xs text-slate-400">{sum.servers_registered} registered, {sum.servers_seen} reporting, stale after {sum.stale_after_s} s</span>
        </div>
        {sum.rollup.length === 0 && <p className="text-sm text-slate-400">No servers reporting yet.</p>}
        {sum.rollup.map((c) => (
          <div key={c.country} className="border-t border-slate-800 py-2">
            <button className="flex w-full items-center justify-between text-left" onClick={() => toggle(c.country)}>
              <span className="font-medium">{open[c.country] ? "[-]" : "[+]"} {c.country}</span>
              <span className="flex items-center gap-3 text-xs text-slate-400">
                {c.servers} servers
                {c.stale > 0 && <span className="font-semibold text-red-400">{c.stale} stale</span>}
                <Pill state={c.state} />
              </span>
            </button>
            {open[c.country] && c.cities.map((ci) => {
              const ck = `${c.country}/${ci.city}`;
              return (
                <div key={ck} className="ml-5 mt-1 border-l border-slate-800 pl-3">
                  <button className="flex w-full items-center justify-between py-1 text-left text-sm" onClick={() => toggle(ck)}>
                    <span>{open[ck] ? "[-]" : "[+]"} {ci.city}</span>
                    <span className="flex items-center gap-3 text-xs text-slate-400">
                      {ci.servers} servers
                      {ci.stale > 0 && <span className="font-semibold text-red-400">{ci.stale} stale</span>}
                      <Pill state={ci.state} />
                    </span>
                  </button>
                  {open[ck] && ci.buildings.map((b) => {
                    const bk = `${ck}/${b.building}`;
                    return (
                      <div key={bk} className={`ml-5 border-l pl-3 ${b.stale > 0 ? "border-red-500/60" : "border-slate-800"}`}>
                        <button className="flex w-full items-center justify-between py-1 text-left text-sm" onClick={() => toggle(bk)}>
                          <span>{open[bk] ? "[-]" : "[+]"} {b.site_id}</span>
                          <span className="flex items-center gap-3 text-xs text-slate-400">
                            {b.servers} servers
                            {b.stale > 0 && <span className="font-semibold text-red-400">{b.stale} stale</span>}
                            <Pill state={b.state} />
                          </span>
                        </button>
                        <p className={`pb-1 text-xs ${b.stale > 0 ? "text-red-300" : "text-slate-500"}`}>{b.reason}</p>
                        {open[bk] && (
                          <div className="overflow-x-auto pb-2">
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="text-left text-slate-400">
                                  <th className="pr-3">Server</th><th className="pr-3">State</th>
                                  {COLS.map((k) => <th key={k} className="pr-3">{LABEL[k]}</th>)}
                                </tr>
                              </thead>
                              <tbody>
                                {b.server_list.map((s) => (
                                  <tr key={s.source_hash} className="border-t border-slate-800">
                                    <td className="pr-3 font-mono">{s.source_hash}</td>
                                    <td className="pr-3"><Pill state={s.state} /></td>
                                    {COLS.map((k) => <td key={k} className="pr-3">{s.vals[k] === undefined ? "-" : s.vals[k]}</td>)}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}