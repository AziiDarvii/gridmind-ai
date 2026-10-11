import { useEffect, useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type DataCenterInfo, type WhatIf } from "../api";
import { CIRCUIT_ID, useRunStates, useStore } from "../store";
import { fmt, signed } from "../status";

const COLORS = ["#0b6bcb", "#7c3aed", "#1f9d55", "#e46c0a", "#d62839", "#8a94a0"];
const TIP = { contentStyle: { background: "var(--panel)", border: "1px solid var(--line)" } };

export default function DataCenterPage() {
  const { run, scenarioId, setError } = useStore();
  const states = useRunStates();
  const [info, setInfo] = useState<DataCenterInfo | null>(null);
  const [scale, setScale] = useState(1.0);
  const [training, setTraining] = useState(1.0);
  const [whatif, setWhatif] = useState<WhatIf | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.datacenter(CIRCUIT_ID).then(setInfo).catch((e) => setError(e.message)); }, [setError]);

  const hourly = useMemo(() => {
    if (!info) return [];
    const temp = info.measurements.find((m) => m.category === "temperature")?.values ?? [];
    const price = info.measurements.find((m) => m.category === "energy_price")?.values ?? [];
    return info.hourly.total.map((t, h) => ({
      hour: h, total: t, pue: info.hourly.pue[h], temp: temp[h], price: price[h],
      ...Object.fromEntries(Object.entries(info.hourly.components).map(([k, v]) => [k, v[h]])),
    }));
  }, [info]);
  const ts = run?.context?.dc_analysis?.timeseries ?? [];
  const schedule = states.iteration?.dc_schedule;
  const schedRows = schedule?.success && schedule.optimized_schedule
    ? schedule.baseline_schedule.map((b, h) => ({ hour: h, baseline: b, optimized: schedule.optimized_schedule![h], price: hourly[h]?.price,
        before: schedule.timeseries_before?.[h]?.max_loading_percent, after: schedule.timeseries_after?.[h]?.max_loading_percent }))
    : [];

  const runWhatIf = async () => {
    setBusy(true);
    try {
      setWhatif(await api.whatif(CIRCUIT_ID, { scenario_id: scenarioId, dc_scale: scale, training_scale: training }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!info) return <p className="muted">Loading data-center profile…</p>;
  const comps = Object.keys(info.hourly.components);
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h1>{info.profile.name}</h1>
        <p className="muted small">Connected at <b className="mono">{info.profile.connection_bus}</b> through the DC service cable and two 20/0.4 kV
          transformers; contracted connection limit {info.profile.connection_limit_mw} MW. Location {info.profile.location.city}.</p>
      </div>
      <div className="grid c2">
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Hourly demand by component (MW)</h2>
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <AreaChart data={hourly} margin={{ left: -10, right: 8 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                <XAxis dataKey="hour" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <YAxis tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <Tooltip {...TIP} formatter={(v: number) => fmt(v, 3)} />
                <Legend />
                {comps.map((c, i) => <Area key={c} dataKey={c} stackId="1" stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.55} />)}
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="small muted">Cooling = IT × (0.18 + 0.012 × max(0, T − 15 °C)) from Open-Meteo temperature; UPS losses from 95.5 % efficiency.</p>
        </div>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>PUE vs outdoor temperature</h2>
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <LineChart data={hourly} margin={{ left: -10, right: 8 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                <XAxis dataKey="hour" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <YAxis yAxisId="l" domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 10, fill: "var(--ink-3)" }} unit="°C" />
                <Tooltip {...TIP} formatter={(v: number) => fmt(v, 3)} />
                <Legend />
                <Line yAxisId="l" dataKey="pue" name="PUE" stroke="var(--accent)" dot={false} />
                <Line yAxisId="r" dataKey="temp" name="Temperature" stroke="var(--moderate)" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {ts.length ? (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>24-hour AC time series of the network (original configuration)</h2>
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <ComposedChart data={ts} margin={{ left: -10, right: 8 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                <XAxis dataKey="hour" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <YAxis yAxisId="v" domain={[0.85, 1.06]} tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <YAxis yAxisId="l" orientation="right" tick={{ fontSize: 10, fill: "var(--ink-3)" }} unit="%" />
                <Tooltip {...TIP} formatter={(v: number) => fmt(v, 3)} />
                <Legend />
                <ReferenceLine yAxisId="v" y={0.95} stroke="var(--severe)" strokeDasharray="4 3" />
                <Bar yAxisId="l" dataKey="violations" name="# violations" fill="var(--severe)" fillOpacity={0.35} isAnimationActive={false} />
                <Line yAxisId="v" dataKey="min_vm_pu" name="Min voltage (pu)" stroke="var(--accent)" dot={false} isAnimationActive={false} />
                <Line yAxisId="l" dataKey="max_loading_percent" name="Max loading (%)" stroke="var(--moderate)" dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="small muted">Residential and commercial loads follow SimBench profiles; data-center components follow the hourly model above.</p>
        </div>
      ) : null}

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>What-if: change data-center demand</h2>
        <div style={{ display: "flex", gap: 18, alignItems: "center", flexWrap: "wrap" }}>
          <label>All DC loads × <b className="mono">{scale.toFixed(2)}</b><br />
            <input type="range" min={0} max={2} step={0.05} value={scale} onChange={(e) => setScale(Number(e.target.value))} /></label>
          <label>AI training × <b className="mono">{training.toFixed(2)}</b><br />
            <input type="range" min={0} max={3} step={0.1} value={training} onChange={(e) => setTraining(Number(e.target.value))} /></label>
          <button className="primary" onClick={runWhatIf} disabled={busy}>{busy ? "Solving…" : "Run AC power flow"}</button>
        </div>
        {whatif ? (
          <table style={{ marginTop: 12 }}>
            <thead><tr><th>Quantity</th><th className="num">Scenario</th><th className="num">What-if</th><th className="num">Change</th></tr></thead>
            <tbody>
              {(["datacenter_p_mw", "min_vm_pu", "max_loading_percent", "losses_p_mw", "grid_p_mw", "grid_pf"] as const).map((k) => (
                <tr key={k}><td className="mono">{k}</td><td className="num">{fmt(whatif.base_totals?.[k], 4)}</td>
                  <td className="num">{fmt(whatif.whatif_totals?.[k], 4)}</td>
                  <td className="num">{signed((whatif.whatif_totals?.[k] ?? 0) - (whatif.base_totals?.[k] ?? 0), 4)}</td></tr>
              ))}
              <tr><td>Violations (hard)</td><td className="num">—</td>
                <td className="num">{whatif.violations.filter((v) => v.severity === "moderate" || v.severity === "severe").length}</td><td /></tr>
            </tbody>
          </table>
        ) : <p className="small muted">Scales the DC component loads in the selected scenario and re-solves the full network (stored as a what-if result).</p>}
      </div>

      {schedRows.length ? (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Coordinated AI-training schedule (Optimization Agent)</h2>
          <p className="small">{schedule?.method}. Shifted {fmt(schedule?.shifted_mwh, 2)} MWh; energy cost {fmt(schedule?.energy_cost_before_usd, 0)} →{" "}
            {fmt(schedule?.energy_cost_after_usd, 0)} USD/day; violation hours {schedule?.violation_hours_before} → {schedule?.violation_hours_after}.
            <span className="muted"> {schedule?.optimality_claim}.</span></p>
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <ComposedChart data={schedRows} margin={{ left: -10, right: 8 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                <XAxis dataKey="hour" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                <YAxis yAxisId="mw" tick={{ fontSize: 10, fill: "var(--ink-3)" }} unit=" MW" />
                <YAxis yAxisId="p" orientation="right" tick={{ fontSize: 10, fill: "var(--ink-3)" }} unit=" $" />
                <Tooltip {...TIP} formatter={(v: number) => fmt(v, 3)} />
                <Legend />
                <Bar yAxisId="p" dataKey="price" name="TOU price $/MWh (assumed)" fill="var(--warn)" fillOpacity={0.3} />
                <Line yAxisId="mw" dataKey="baseline" name="Training (baseline)" stroke="var(--ink-3)" strokeDasharray="5 4" dot={false} />
                <Line yAxisId="mw" dataKey="optimized" name="Training (optimized)" stroke="var(--proposed)" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Profile, SLA and assumptions</h2>
        <table>
          <thead><tr><th>Component</th><th>Kind</th><th>Flexible</th><th>Constraints / model</th></tr></thead>
          <tbody>{info.profile.components.map((c) => (
            <tr key={c.eq_id}><td className="mono">{c.eq_id}</td><td>{c.kind}</td><td>{c.flexible ? "yes" : "no"}</td>
              <td className="small">{c.flexible ? `${c.min_mw}–${c.max_mw} MW; ` : ""}{c.notes ?? (c.model ? JSON.stringify(c.model) : "")}</td></tr>
          ))}</tbody>
        </table>
        <ul className="small">{Object.entries(info.profile.sla).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}
          {info.profile.assumptions.map((a) => <li key={a} className="muted">{a}</li>)}</ul>
        <h3>Public data used</h3>
        <ul className="small">{info.measurements.map((m) => (
          <li key={m._id}>{m.category}: {m.source} {m.reference_date ? `(${m.reference_date})` : ""}{m.fallback ? " — fallback values" : ""}</li>
        ))}</ul>
      </div>
    </div>
  );
}
