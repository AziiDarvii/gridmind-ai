import { fmt } from "../status";
import { useStore } from "../store";
import type { AgentTask } from "../types";

const STAGES: { title: string; agents: [string, string][] }[] = [
  { title: "Model & solve", agents: [["circuit_modeling", "1 · Circuit Modeling"], ["power_flow", "2 · Power Flow"], ["violation_detection", "3 · Violation Detection"]] },
  { title: "Diagnose (parallel)", agents: [["root_cause", "4 · Root Cause"], ["data_center", "6 · Data Center"], ["learning_retrieve", "9 · Learning (retrieve)"]] },
  { title: "Correct · loop until verified", agents: [["corrective_action", "5 · Corrective Action"], ["optimization", "7 · Optimization"], ["verification", "8 · Independent Verification"]] },
  { title: "Learn & report", agents: [["learning_store", "9 · Learning (store)"], ["engineering_report", "10 · Engineering Report"]] },
];

const TONE: Record<string, string> = { completed: "var(--ok)", running: "var(--accent)", failed: "var(--severe)", stopped: "var(--moderate)" };

export default function AgentsPage() {
  const { run, tasks, llm } = useStore();
  if (!run) return <p className="muted">No agent run for this scenario yet. Start one with <b>Run multi-agent analysis</b>.</p>;
  const latest = (agent: string): AgentTask | undefined => tasks.filter((t) => t.agent === agent).slice(-1)[0];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h1>Multi-agent activity</h1>
        <p className="muted small">Live state from the <span className="mono">agent_runs</span> collection. Each agent calls engine tools
          (power flow, violation checks, sizing, optimization) and writes its results to MongoDB; the loop stops at verification
          or after {run.max_iterations} iterations. Reasoning model: {llm.enabled ? llm.model : "none configured (deterministic explanations)"}.</p>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
        {STAGES.map((stage) => (
          <div key={stage.title}>
            <h3>{stage.title}</h3>
            <div className="grid" style={{ gap: 8 }}>
              {stage.agents.map(([key, label]) => {
                const t = latest(key);
                return (
                  <div key={key} className={`agent-card ${t?.status ?? ""}`}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <b>{label}</b>
                      <span className="small" style={{ color: TONE[t?.status ?? ""] ?? "var(--ink-3)" }}>{t?.status ?? "pending"}</span>
                    </div>
                    <div className="small muted">{t ? `${fmt(t.duration_s, 1)} s${t.iteration > 1 ? ` · iteration ${t.iteration}` : ""}` : "waiting for dependencies"}</div>
                    {t?.output_summary ? <div className="small" style={{ marginTop: 4 }}>{t.output_summary}</div> : null}
                    {t?.error ? <div className="small" style={{ color: "var(--severe)" }}>{t.error}</div> : null}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Task log</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Step</th><th>Agent</th><th>Task</th><th>Status</th><th className="num">Duration</th><th>Output</th><th>Verification</th></tr></thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t._id}>
                  <td className="mono">{t.step}</td><td>{t.agent_label}</td><td className="small">{t.task}</td>
                  <td style={{ color: TONE[t.status] }}>{t.status}</td><td className="num">{fmt(t.duration_s, 2)} s</td>
                  <td className="small">{t.output_summary}{t.error ? <div style={{ color: "var(--severe)" }}>{t.error}</div> : null}</td>
                  <td className="small">{t.verification ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {run.context?.memory_hints?.length ? (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Lessons retrieved from MongoDB agent memory</h2>
          <ul className="small">{run.context.memory_hints.map((h) => <li key={h._id}><span className="mono">{h.signature}</span> — {h.lesson}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}
