import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type ComponentDetail } from "../api";
import { STATUS_COLOR, STATUS_LABEL, fmt, loadingStatus, signed, voltageStatus } from "../status";
import type { Circuit, SimResult, Status } from "../types";

const VALUE_LABELS: Record<string, [string, string, number]> = {
  vm_pu: ["Voltage", "pu", 4], v_kv: ["Voltage", "kV", 3], vn_kv: ["Nominal voltage", "kV", 1],
  deviation_percent: ["Deviation from nominal", "%", 2], va_degree: ["Voltage angle", "°", 2],
  p_mw: ["Active power", "MW", 3], q_mvar: ["Reactive power", "Mvar", 3], s_mva: ["Apparent power", "MVA", 3],
  pf: ["Power factor", "", 3], i_ka: ["Current", "kA", 4], i_hv_ka: ["HV current", "kA", 4],
  i_rated_ka: ["Rated current", "kA", 4], loading_percent: ["Loading", "%", 1], sn_mva: ["Rating", "MVA", 2],
  p_from_mw: ["P (from end)", "MW", 3], q_from_mvar: ["Q (from end)", "Mvar", 3], p_to_mw: ["P (to end)", "MW", 3],
  q_to_mvar: ["Q (to end)", "Mvar", 3], pl_mw: ["Active losses", "MW", 5], ql_mvar: ["Reactive losses", "Mvar", 5],
  v_from_pu: ["Voltage at from end", "pu", 4], v_to_pu: ["Voltage at to end", "pu", 4],
  vdrop_percent: ["Voltage drop", "%", 3], tap_pos: ["Tap position", "", 0], q_injected_mvar: ["Reactive output", "Mvar", 3],
};

interface Props {
  circuit: Circuit;
  eqId: string;
  result: SimResult | null;
  baseline?: SimResult | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

export default function DetailPanel({ circuit, eqId, result, baseline, onSelect, onClose }: Props) {
  const [detail, setDetail] = useState<ComponentDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setDetail(null);
    setErr(null);
    api.component(circuit._id, eqId, result?._id).then((d) => alive && setDetail(d)).catch((e) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [circuit._id, eqId, result?._id]);

  if (err) return <div className="side"><button onClick={onClose}>Close</button><div className="banner err">{err}</div></div>;
  if (!detail) return <div className="side"><span className="muted">Loading {eqId}…</span></div>;
  const eq = detail.equipment;
  const v = detail.values as Record<string, number | string | boolean | null> | null;
  const th = circuit.effective_thresholds;
  let status: Status = "unknown";
  if (v && typeof v.vm_pu === "number") status = voltageStatus(v.vm_pu, th);
  else if (v && typeof v.loading_percent === "number") status = loadingStatus(v.loading_percent, String(v.kind), th);
  const before = baseline ? findValues(baseline, eqId) : null;
  const metric = v && "vm_pu" in v ? "vm_pu" : v && "loading_percent" in v ? "loading_percent" : v && "p_mw" in v ? "p_mw" : null;
  const history = metric ? detail.history.slice().reverse().map((h, i) => ({ i, label: h.label, value: h.values[metric] })) : [];
  const limitLine = metric === "vm_pu" ? th.voltage.lower_pu : metric === "loading_percent" ? 100 : undefined;

  return (
    <aside className="side">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
        <div>
          <div className="mono muted small">{eq.type}{eq.proposed ? " · PROPOSED" : ""}</div>
          <h1 className="mono">{eq.eq_id}</h1>
          <div className="muted">{eq.name}</div>
        </div>
        <button onClick={onClose} aria-label="Close panel">✕</button>
      </div>
      <div style={{ margin: "10px 0" }}>
        <span className="pill" style={{ color: STATUS_COLOR[status] }}><span className="dot" style={{ background: STATUS_COLOR[status] }} />{STATUS_LABEL[status]}</span>
        {eq.source ? <div className="small muted" style={{ marginTop: 6 }}>Source: {eq.source}</div> : null}
      </div>

      <h3>Calculated values {result ? <span className="tag calc">{result.label}</span> : null}</h3>
      {v ? (
        <div className="kv">
          {Object.entries(v).filter(([k]) => k in VALUE_LABELS).map(([k, val]) => {
            const [label, unit, digits] = VALUE_LABELS[k];
            const old = before ? (before as Record<string, unknown>)[k] : undefined;
            return (
              <Row key={k} label={label} value={`${fmt(val, digits)} ${unit}`}
                   extra={typeof old === "number" && typeof val === "number" ? <span className="muted">{signed(val - old, digits)}</span> : <span className="tag calc">calc</span>} />
            );
          })}
        </div>
      ) : <div className="muted small">No solver values for this component in the selected result.</div>}

      <h3>Model parameters</h3>
      <div className="kv">
        {Object.entries(eq.params).map(([k, val]) => {
          const q = eq.data_quality?.[k] ?? (val === null || val === undefined ? "missing" : eq.proposed ? "proposed" : "input");
          return <Row key={k} label={k} value={fmt(val, 4)} extra={<span className={`tag ${q}`}>{q}</span>} />;
        })}
      </div>
      {eq.type === "bus" ? (
        <div className="small muted" style={{ marginTop: 6 }}>
          Limits: {th.voltage.lower_pu}–{th.voltage.upper_pu} pu (warning margin {th.voltage.warning_margin_pu} pu). Phase
          voltages / imbalance: not modelled (balanced positive-sequence study).
        </div>
      ) : null}

      {detail.faults.length ? (<>
        <h3>Detected problems</h3>
        {detail.faults.slice(0, 8).map((f) => (
          <div key={f._id} className="small" style={{ marginBottom: 6 }}>
            <span className="pill" style={{ color: STATUS_COLOR[f.severity] }}>{f.type}</span>{" "}
            {fmt(f.value, 4)} {f.unit} vs {f.limit} · <span className="muted">{f.status}</span>
            {f.root_cause ? <div className="muted">Cause: {f.root_cause}</div> : null}
          </div>
        ))}
      </>) : null}

      {detail.supply_path.length ? (<>
        <h3>Supply path</h3>
        <div className="small mono">{detail.supply_path.map((s) => (
          <a key={s.eq_id} onClick={() => onSelect(s.eq_id)} style={{ cursor: "pointer", marginRight: 6 }}>{s.eq_id} →</a>
        ))}{eq.eq_id}</div>
      </>) : null}

      <h3>Connected equipment</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {detail.connected.filter((c) => c !== eq.eq_id).map((c) => (
          <button key={c} className="mono small" onClick={() => onSelect(c)} style={{ padding: "2px 6px" }}>{c}</button>
        ))}
      </div>

      {history.length > 1 ? (<>
        <h3>History across stored simulations</h3>
        <div style={{ height: 150 }}>
          <ResponsiveContainer>
            <LineChart data={history} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis dataKey="i" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
              <Tooltip formatter={(val: number) => fmt(val, 4)} labelFormatter={(i) => history[Number(i)]?.label ?? ""}
                       contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} />
              {limitLine ? <ReferenceLine y={limitLine} stroke="var(--severe)" strokeDasharray="4 3" /> : null}
              <Line dataKey="value" stroke="var(--accent)" dot={{ r: 2 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </>) : null}

      {detail.recommendations.length ? (<>
        <h3>Recommendations involving {eq.eq_id}</h3>
        {detail.recommendations.map((r) => (
          <div key={r._id} className="small" style={{ marginBottom: 4 }}>#{r.rank} {r.title} · <span className="muted">{r.status}</span></div>
        ))}
      </>) : null}
    </aside>
  );
}

function Row({ label, value, extra }: { label: string; value: string; extra?: React.ReactNode }) {
  return (<><div className="k">{label}</div><div className="v">{value}</div><div>{extra}</div></>);
}

function findValues(r: SimResult, id: string): Record<string, unknown> | null {
  for (const table of [r.buses, r.branches, r.loads, r.capacitors] as unknown as { id: string }[][]) {
    const row = table?.find((x) => x.id === id);
    if (row) return row as unknown as Record<string, unknown>;
  }
  return null;
}
