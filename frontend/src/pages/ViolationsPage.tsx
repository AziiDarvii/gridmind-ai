import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { STATUS_COLOR, fmt } from "../status";
import { useStore } from "../store";
import { useScenarioBaseResult } from "./CircuitPage";
import type { Violation } from "../types";

const STATUS_TONE: Record<string, string> = {
  open: "var(--moderate)", resolved: "var(--ok)", remaining: "var(--severe)", new: "var(--proposed)",
};

export default function ViolationsPage() {
  const { run, setError } = useStore();
  const base = useScenarioBaseResult();
  const navigate = useNavigate();
  const [faults, setFaults] = useState<Violation[] | null>(null);
  const [type, setType] = useState("all");
  const [severity, setSeverity] = useState("all");

  useEffect(() => {
    if (!run) {
      setFaults(null);
      return;
    }
    api.faults({ run_id: run._id, page_size: "500" }).then((r) => setFaults(r.items)).catch((e) => setError(e.message));
  }, [run, run?.status, setError]);

  const rows = useMemo(() => (faults ?? base?.violations ?? [])
    .filter((v) => (type === "all" || v.type === type) && (severity === "all" || v.severity === severity))
    .sort((a, b) => (a.status === "new" ? -1 : 0) - (b.status === "new" ? -1 : 0) || a.equipment_id.localeCompare(b.equipment_id, undefined, { numeric: true })),
  [faults, base, type, severity]);
  const types = Array.from(new Set((faults ?? base?.violations ?? []).map((v) => v.type)));

  return (
    <div>
      <h1>Electrical violations</h1>
      <p className="muted small">
        {faults ? `${faults.length} records from detected_faults for run ${run?._id}` : "Violations of the original state (no agent run yet)"}.
        Select a row to highlight the equipment on the single-line diagram.
      </p>
      <div style={{ display: "flex", gap: 8, margin: "10px 0" }}>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
          <option value="all">All types</option>{types.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
          {["all", "warning", "moderate", "severe"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      <div className="panel table-wrap">
        <table>
          <thead><tr><th>Severity</th><th>Type</th><th>Equipment</th><th className="num">Value</th><th className="num">Limit</th>
            <th>Severity basis</th><th>Condition</th><th>Root cause</th><th>Status</th></tr></thead>
          <tbody>
            {rows.map((v, i) => (
              <tr key={v._id ?? i} className="clickable" onClick={() => navigate(`/circuit?focus=${encodeURIComponent(v.equipment_id)}&layer=faults${v.status === "new" ? "&state=verified" : ""}`)}>
                <td><span className="pill" style={{ color: STATUS_COLOR[v.severity] }}>{v.severity}</span></td>
                <td>{v.type.replace(/_/g, " ")}</td>
                <td className="mono">{v.equipment_id}</td>
                <td className="num">{fmt(v.value, v.unit === "pu" ? 4 : 2)} {v.unit}{v.value_kv ? <div className="muted">{fmt(v.value_kv, 3)} kV</div> : null}
                  {v.i_ka ? <div className="muted">{fmt(v.i_ka * 1000, 0)} / {fmt((v.i_rated_ka ?? 0) * 1000, 0)} A</div> : null}</td>
                <td className="num">{v.limit} {v.unit}</td>
                <td className="small">{v.severity_basis}</td>
                <td className="small">{v.condition}</td>
                <td className="small">{v.root_cause ?? "—"}</td>
                <td>{v.status ? <span className="pill" style={{ color: STATUS_TONE[v.status] ?? "var(--ink-2)" }}>{v.status}</span> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Studies outside this engine's scope</h2>
      <ul className="small">
        {(run?.context?.unsupported_studies ?? []).map((u) => <li key={u.study}><b>{u.study}</b> — {u.status}: {u.reason}</li>)}
      </ul>
    </div>
  );
}
