import { useEffect, useState } from "react";
import { api, type MemoryLesson } from "../api";
import { fmt } from "../status";
import { useStore } from "../store";

export default function HistoryPage() {
  const { runs, run, selectRun, scenarios, setScenarioId, setError } = useStore();
  const [memory, setMemory] = useState<MemoryLesson[]>([]);
  const [audit, setAudit] = useState<{ _id: string; timestamp: string; action: string; entity_id: string; actor: string }[]>([]);
  useEffect(() => {
    api.memory().then(setMemory).catch((e) => setError(e.message));
    api.audit().then(setAudit).catch((e) => setError(e.message));
  }, [runs.length, setError]);
  const scenarioName = (id: string) => scenarios.find((s) => s._id === id)?.name ?? id;
  return (
    <div className="grid" style={{ gap: 16 }}>
      <h1>History & agent memory</h1>
      <div className="panel table-wrap">
        <h2 style={{ marginTop: 0 }}>Workflow runs (optimization_runs)</h2>
        <table>
          <thead><tr><th>Run</th><th>Scenario</th><th>Status</th><th>Result</th><th className="num">Iterations</th><th>Started</th><th /></tr></thead>
          <tbody>{runs.map((r) => (
            <tr key={r._id} style={r._id === run?._id ? { background: "var(--panel-2)" } : undefined}>
              <td className="mono">{r._id}</td><td>{scenarioName(r.scenario_id)}</td><td>{r.status}</td>
              <td>{r.status === "completed" ? (r.verified ? "verified" : r.stop_reason) : r.stop_reason ?? "—"}</td>
              <td className="num">{r.iteration}/{r.max_iterations}</td><td className="small">{new Date(r.created_at).toLocaleString()}</td>
              <td><button onClick={() => { setScenarioId(r.scenario_id); selectRun(r._id); }}>Open</button></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="panel table-wrap">
        <h2 style={{ marginTop: 0 }}>Recursive learning memory (agent_memory)</h2>
        <p className="small muted">Structured lessons written by the Learning Agent after verification. Only verified lessons are retrieved to
          seed future runs (e.g. a capacitor's measured dV/dQ sensitivity replaces the trial injection, and previously verified plan
          types are evaluated first).</p>
        <table>
          <thead><tr><th>Signature</th><th>Intervention</th><th>Parameters</th><th>Verified</th><th>Scenario</th><th>Lesson</th></tr></thead>
          <tbody>{memory.map((m) => (
            <tr key={m._id}><td className="mono small">{m.signature}</td><td>{m.intervention_type}</td>
              <td className="mono small">{Object.entries(m.parameters).map(([k, v]) => `${k}=${fmt(v, 4)}`).join(" · ")}</td>
              <td style={{ color: m.verified ? "var(--ok)" : "var(--ink-3)" }}>{m.verified ? "yes" : "no"}</td>
              <td className="small">{m.scenario_name}</td><td className="small">{m.lesson}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <div className="panel table-wrap">
        <h2 style={{ marginTop: 0 }}>Audit log</h2>
        <table>
          <thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Actor</th></tr></thead>
          <tbody>{audit.map((a) => (
            <tr key={a._id}><td className="small">{new Date(a.timestamp).toLocaleString()}</td><td className="mono">{a.action}</td>
              <td className="mono small">{a.entity_id}</td><td>{a.actor}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
