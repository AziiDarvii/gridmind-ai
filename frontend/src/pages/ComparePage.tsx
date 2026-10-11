import { useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { api } from "../api";
import CircuitDiagram, { LAYERS, Legend, type Layer } from "../components/CircuitDiagram";
import { STATUS_COLOR, fmt, signed } from "../status";
import { useResult, useRunStates, useStore } from "../store";
import type { Comparison, Status } from "../types";

function StatusCell({ before, after }: { before: Status; after: Status }) {
  return (
    <td className="small" style={{ whiteSpace: "nowrap" }}>
      <span style={{ color: STATUS_COLOR[before] }}>{before}</span> → <b style={{ color: STATUS_COLOR[after] }}>{after}</b>
    </td>
  );
}

export default function ComparePage() {
  const { circuit, setError } = useStore();
  const states = useRunStates();
  const [target, setTarget] = useState<"verified" | "proposed">("verified");
  const [layer, setLayer] = useState<Layer>("faults");
  const [comp, setComp] = useState<Comparison | null>(null);
  const afterId = target === "verified" ? states.verified : states.proposed;
  const before = useResult(states.original);
  const after = useResult(afterId);

  useEffect(() => {
    if (!states.original || !afterId) return;
    setComp(null);
    api.compare(states.original, afterId).then(setComp).catch((e) => setError(e.message));
  }, [states.original, afterId, setError]);

  if (!circuit) return null;
  if (!states.original || !afterId) {
    return <div className="content"><p className="muted">Run the multi-agent analysis to compare the original and corrected circuits.</p></div>;
  }
  const v = comp?.violations;
  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div style={{ display: "flex", gap: 8, padding: "10px 14px", alignItems: "center", flexWrap: "wrap", borderBottom: "1px solid var(--line)" }}>
        <b>Compare original with</b>
        <div className="seg">
          <button className={target === "proposed" ? "on" : ""} onClick={() => setTarget("proposed")}>B · Proposed</button>
          <button className={target === "verified" ? "on" : ""} onClick={() => setTarget("verified")}>C · Verified</button>
        </div>
        <div className="seg">{LAYERS.filter((l) => !["existing", "proposed"].includes(l.key)).map((l) => (
          <button key={l.key} className={layer === l.key ? "on" : ""} onClick={() => setLayer(l.key)}>{l.label}</button>))}</div>
        {v ? <span className="small">Resolved <b style={{ color: "var(--ok)" }}>{v.resolved.length}</b> · remaining <b style={{ color: "var(--severe)" }}>{v.remaining.length}</b> · new <b style={{ color: "var(--proposed)" }}>{v.new.length}</b></span> : null}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", height: "52vh", minHeight: 320, borderBottom: "1px solid var(--line)" }}>
        <div style={{ position: "relative", borderRight: "1px solid var(--line)" }}>
          <div className="tag" style={{ position: "absolute", top: 8, left: 8, zIndex: 5 }}>A · Original</div>
          <ReactFlowProvider><CircuitDiagram circuit={circuit} result={before} layer={layer} mode="original" showMiniMap={false} /></ReactFlowProvider>
        </div>
        <div style={{ position: "relative" }}>
          <div className="tag proposed" style={{ position: "absolute", top: 8, left: 8, zIndex: 5 }}>{target === "verified" ? "C · Verified" : "B · Proposed"}</div>
          <ReactFlowProvider><CircuitDiagram circuit={circuit} result={after} layer={layer} mode={target} showMiniMap={false} /></ReactFlowProvider>
          <Legend />
        </div>
      </div>
      <div style={{ overflow: "auto", padding: 14, flex: 1 }}>
        {!comp ? <span className="muted">Comparing persisted results…</span> : !comp.comparable ? <div className="banner err">{comp.reason}</div> : (
          <div className="grid" style={{ gap: 14 }}>
            <div className="panel">
              <h2 style={{ marginTop: 0 }}>System totals</h2>
              <table>
                <thead><tr><th>Quantity</th><th className="num">Original</th><th className="num">{target}</th><th className="num">Change</th></tr></thead>
                <tbody>{Object.entries(comp.totals).map(([k, t]) => (
                  <tr key={k}><td className="mono">{k}</td><td className="num">{fmt(t.before, 4)}</td><td className="num">{fmt(t.after, 4)}</td><td className="num">{signed(t.change, 4)}</td></tr>
                ))}</tbody>
              </table>
              <h3>Changes applied</h3>
              <ul className="small mono">{(comp.interventions ?? []).map((i, n) => <li key={n}>{JSON.stringify(i)}</li>)}</ul>
            </div>
            <div className="grid c2">
              <div className="panel table-wrap">
                <h2 style={{ marginTop: 0 }}>Bus voltages</h2>
                <table>
                  <thead><tr><th>Bus</th><th className="num">Original pu</th><th className="num">New pu</th><th className="num">Change</th><th>Limit</th><th>Status</th></tr></thead>
                  <tbody>{comp.buses.map((b) => (
                    <tr key={b.id}><td className="mono">{b.id}{b.added ? " (new)" : ""}</td><td className="num">{fmt(b.before_pu, 4)}</td><td className="num">{fmt(b.after_pu, 4)}</td>
                      <td className="num" style={{ color: (b.change_pu ?? 0) > 0 ? "var(--ok)" : undefined }}>{signed(b.change_pu, 4)}</td><td className="small">{b.limit}</td>
                      <StatusCell before={b.before_status} after={b.after_status} /></tr>
                  ))}</tbody>
                </table>
              </div>
              <div className="panel table-wrap">
                <h2 style={{ marginTop: 0 }}>Branch & transformer loading</h2>
                <table>
                  <thead><tr><th>Equipment</th><th className="num">Original %</th><th className="num">New %</th><th className="num">Change</th><th className="num">Losses kW</th><th>Status</th></tr></thead>
                  <tbody>{comp.branches.map((b) => (
                    <tr key={b.id}><td className="mono">{b.id}{b.added ? " (new)" : ""}{b.rating_changed ? " ⇧rating" : ""}</td>
                      <td className="num">{fmt(b.before_loading, 1)}</td><td className="num">{fmt(b.after_loading, 1)}</td>
                      <td className="num" style={{ color: (b.change_loading ?? 0) < 0 ? "var(--ok)" : undefined }}>{signed(b.change_loading, 1)}</td>
                      <td className="num">{fmt((b.before_losses_mw ?? 0) * 1000, 1)} → {fmt(b.after_losses_mw * 1000, 1)}</td>
                      <StatusCell before={b.before_status} after={b.after_status} /></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
            <div className="panel">
              <h2 style={{ marginTop: 0 }}>Violation resolution</h2>
              <table>
                <thead><tr><th>Outcome</th><th>Type</th><th>Equipment</th><th className="num">Original</th><th className="num">New</th></tr></thead>
                <tbody>
                  {[...(v?.resolved ?? []).map((x) => ({ ...x, outcome: "resolved" })), ...(v?.remaining ?? []).map((x) => ({ ...x, outcome: "remaining" })),
                    ...(v?.new ?? []).map((x) => ({ ...x, outcome: "new" }))].map((x, n) => (
                    <tr key={n}><td style={{ color: x.outcome === "resolved" ? "var(--ok)" : x.outcome === "new" ? "var(--proposed)" : "var(--severe)" }}>{x.outcome}</td>
                      <td>{x.type}</td><td className="mono">{x.equipment_id}</td>
                      <td className="num">{fmt((x as { before_value?: number }).before_value ?? (x.outcome === "new" ? null : x.value), 4)}</td>
                      <td className="num">{fmt((x as { after_value?: number }).after_value ?? (x.outcome === "resolved" ? null : x.value), 4)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
