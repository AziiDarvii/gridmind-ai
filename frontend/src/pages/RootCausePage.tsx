import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmt } from "../status";
import { useStore } from "../store";

export default function RootCausePage() {
  const { run } = useStore();
  const causes = run?.context?.rootcauses ?? [];
  const narrative = run?.context?.rootcause_narrative;
  const [open, setOpen] = useState<string | null>(causes[0]?.violation_key ?? null);
  if (!run) return <p className="muted">Run the multi-agent analysis to produce the root-cause diagnosis.</p>;
  const current = causes.find((c) => c.violation_key === open) ?? causes[0];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h1>Root-cause analysis</h1>
        <p className="muted small">Each diagnosis is computed from the solved network: supply-path voltage-drop decomposition
          (ΔV ≈ (R·P + X·Q)/V²), transformer loading and tap headroom, and a counterfactual solve without the data center.</p>
      </div>
      {narrative ? (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Root Cause Agent diagnosis {narrative.used_llm ? <span className="tag calc">Claude + solver tools</span> : <span className="tag">deterministic</span>}</h2>
          <div style={{ whiteSpace: "pre-wrap" }}>{narrative.text}</div>
          {narrative.tool_calls?.length ? (
            <div className="small muted" style={{ marginTop: 8 }}>Tool calls: {narrative.tool_calls.map((t) => `${t.tool}(${JSON.stringify(t.input)})`).join(" · ")}</div>
          ) : null}
          {narrative.note ? <div className="small muted">{narrative.note}</div> : null}
        </div>
      ) : null}
      <div className="grid c2">
        <div className="panel table-wrap">
          <table>
            <thead><tr><th>Violation</th><th>Top cause</th><th className="num">Score</th></tr></thead>
            <tbody>
              {causes.map((c) => (
                <tr key={c.violation_key} className="clickable" onClick={() => setOpen(c.violation_key)}
                    style={c.violation_key === current?.violation_key ? { background: "var(--panel-2)" } : undefined}>
                  <td className="mono">{c.violation_key}</td><td>{c.causes[0]?.title}</td><td className="num">{fmt(c.causes[0]?.score, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {current ? (
          <div className="panel">
            <h2 style={{ marginTop: 0 }} className="mono">{current.violation_key}</h2>
            <Link to={`/circuit?focus=${current.equipment_id}&layer=voltage`}>Show on diagram →</Link>
            <h3>Ranked causes</h3>
            <ol>
              {current.causes.map((c) => (<li key={c.code} style={{ marginBottom: 6 }}><b>{c.title}</b> <span className="muted">(score {fmt(c.score, 2)})</span><div className="small">{c.evidence}</div></li>))}
            </ol>
            <div className="small">Downstream load {fmt(current.downstream_load_mw, 3)} MW, of which data center {fmt(current.downstream_dc_mw, 3)} MW.
              {current.value_without_dc !== null && current.value_without_dc !== undefined ? ` Without the data center: ${fmt(current.value_without_dc, 4)}.` : ""}</div>
            {current.path ? (
              <>
                <h3>Supply-path voltage drop ({fmt(current.path.total_drop_percent, 2)} % total; reactive share {fmt(100 * current.path.reactive_share, 0)} %)</h3>
                <div style={{ height: 220 }}>
                  <ResponsiveContainer>
                    <BarChart data={current.path.segments} margin={{ left: -10, right: 8 }}>
                      <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                      <XAxis dataKey="eq_id" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                      <YAxis tick={{ fontSize: 10, fill: "var(--ink-3)" }} unit="%" />
                      <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} formatter={(v: number) => fmt(v, 3)} />
                      <Legend />
                      <Bar dataKey="dv_p_percent" name="R·P term" stackId="a" fill="var(--accent)" />
                      <Bar dataKey="dv_q_percent" name="X·Q term" stackId="a" fill="var(--moderate)" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <table>
                  <thead><tr><th>Segment</th><th className="num">R Ω</th><th className="num">X Ω</th><th className="num">P MW</th><th className="num">Q Mvar</th><th className="num">ΔV %</th><th className="num">Load %</th></tr></thead>
                  <tbody>{current.path.segments.map((s) => (
                    <tr key={s.eq_id}><td className="mono">{s.eq_id}</td><td className="num">{fmt(s.r_ohm, 3)}</td><td className="num">{fmt(s.x_ohm, 3)}</td>
                      <td className="num">{fmt(s.p_mw, 3)}</td><td className="num">{fmt(s.q_mvar, 3)}</td><td className="num">{fmt(s.vdrop_percent, 3)}</td><td className="num">{fmt(s.loading_percent, 1)}</td></tr>
                  ))}</tbody>
                </table>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
