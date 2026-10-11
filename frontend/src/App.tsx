import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useStore } from "./store";
import OverviewPage from "./pages/OverviewPage";
import CircuitPage from "./pages/CircuitPage";
import ComponentsPage from "./pages/ComponentsPage";
import ViolationsPage from "./pages/ViolationsPage";
import RootCausePage from "./pages/RootCausePage";
import AgentsPage from "./pages/AgentsPage";
import ActionsPage from "./pages/ActionsPage";
import ComparePage from "./pages/ComparePage";
import DataCenterPage from "./pages/DataCenterPage";
import HistoryPage from "./pages/HistoryPage";
import OptimizationPage from "./pages/OptimizationPage";
import ReportPage from "./pages/ReportPage";

const NAV = [
  ["/", "Overview"], ["/circuit", "Single-line diagram"], ["/components", "Component explorer"],
  ["/violations", "Violations"], ["/root-cause", "Root cause"], ["/agents", "Agents"],
  ["/actions", "Corrective actions"], ["/compare", "Before / after"], ["/datacenter", "Data center"],
  ["/history", "History & memory"], ["/optimization", "Optimization"], ["/report", "Engineering report"],
] as const;

const RUN_COLOR: Record<string, string> = {
  completed: "var(--ok)", running: "var(--accent)", queued: "var(--accent)", failed: "var(--severe)",
  stopped: "var(--moderate)", interrupted: "var(--warn)",
};

export default function App() {
  const { scenarios, scenarioId, setScenarioId, run, startRun, resumeRun, error, setError, llm } = useStore();
  const location = useLocation();
  const busy = run?.status === "running" || run?.status === "queued";
  const fullBleed = location.pathname === "/circuit" || location.pathname === "/compare";
  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand"><span className="brand-mark">GM</span>GridMind AI</div>
        <div className="nav">
          {NAV.map(([to, label], i) => (
            <NavLink key={to} to={to} end={to === "/"}><span className="num">{String(i + 1).padStart(2, "0")}</span>{label}</NavLink>
          ))}
        </div>
        <div className="small muted" style={{ padding: "16px 10px" }}>
          Data: MongoDB Atlas<br />Solver: pandapower AC NR<br />
          Agents: {llm.enabled ? `Claude (${llm.model})` : "deterministic (no API key)"}
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <label className="small muted" htmlFor="scenario">Scenario</label>
          <select id="scenario" value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}>
            {scenarios.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select>
          <button className="primary" disabled={busy} onClick={() => startRun()}>
            {busy ? "Agents running…" : "Run multi-agent analysis"}
          </button>
          {run?.status === "interrupted" ? <button onClick={resumeRun}>Resume interrupted run</button> : null}
          {run ? (
            <span className="pill" style={{ color: RUN_COLOR[run.status] ?? "var(--ink-2)" }}>
              <span className="dot" style={{ background: RUN_COLOR[run.status] ?? "var(--ink-2)" }} />
              {run.status}{run.current_step ? ` · ${run.current_step}` : ""}{run.status === "completed" ? (run.verified ? " · verified" : " · not verified") : ""}
            </span>
          ) : <span className="muted small">No run yet for this scenario</span>}
          <span className="mono small muted" style={{ marginLeft: "auto" }}>{run?._id}</span>
        </header>
        {error ? <div className="banner err" style={{ margin: 12 }} onClick={() => setError(null)}>{error} (click to dismiss)</div> : null}
        <div className={fullBleed ? "content full" : "content"}>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/circuit" element={<CircuitPage />} />
            <Route path="/components" element={<ComponentsPage />} />
            <Route path="/violations" element={<ViolationsPage />} />
            <Route path="/root-cause" element={<RootCausePage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/actions" element={<ActionsPage />} />
            <Route path="/compare" element={<ComparePage />} />
            <Route path="/datacenter" element={<DataCenterPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/optimization" element={<OptimizationPage />} />
            <Route path="/report" element={<ReportPage />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}
