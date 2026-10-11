import { Link } from "react-router-dom";
import { fmt, usd } from "../status";
import { useRunStates, useStore, useResult } from "../store";
import { useScenarioBaseResult } from "./CircuitPage";

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="panel kpi">
      <div className="label">{label}</div>
      <div className="value" style={color ? { color } : undefined}>{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

export default function OverviewPage() {
  const { circuit, run, recommendations, scenarios, scenarioId } = useStore();
  const base = useScenarioBaseResult();
  const states = useRunStates();
  const verified = useResult(states.verified);
  const scenario = scenarios.find((s) => s._id === scenarioId);
  const hard = (base?.violations ?? []).filter((v) => v.severity === "moderate" || v.severity === "severe");
  const best = recommendations[0];
  const t = base?.totals;
  const tv = verified?.totals;
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h1>{circuit?.name ?? "GridMind AI"}</h1>
        <div className="muted">{scenario?.name} — {scenario?.description}</div>
      </div>
      <div className="grid k4">
        <Kpi label="Violations (original)" value={String(hard.length)} color={hard.length ? "var(--severe)" : "var(--ok)"}
             sub={`${hard.filter((v) => v.type === "undervoltage").length} undervoltage · ${hard.filter((v) => v.type.endsWith("overload")).length} overload`} />
        <Kpi label="Min voltage" value={`${fmt(t?.min_vm_pu, 4)} pu`} sub={tv ? `verified: ${fmt(tv.min_vm_pu, 4)} pu` : "limit 0.95 pu"} />
        <Kpi label="Max loading" value={`${fmt(t?.max_loading_percent, 1)} %`} sub={tv ? `verified: ${fmt(tv.max_loading_percent, 1)} %` : "limit 100 %"} />
        <Kpi label="Network losses" value={`${fmt(t?.losses_p_mw, 3)} MW`} sub={tv ? `verified: ${fmt(tv.losses_p_mw, 3)} MW` : `${fmt(t?.losses_percent, 2)} % of load`} />
        <Kpi label="Grid power factor" value={fmt(t?.grid_pf, 3)} sub={tv ? `verified: ${fmt(tv.grid_pf, 3)}` : "at 110 kV supply"} />
        <Kpi label="Data-center demand" value={`${fmt(t?.datacenter_p_mw, 2)} MW`} sub={tv ? `verified: ${fmt(tv.datacenter_p_mw, 2)} MW` : "connected at Bus 14"} />
      </div>

      <div className="grid c2">
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Multi-agent result</h2>
          {!run ? <p className="muted">No agent run for this scenario yet. Press <b>Run multi-agent analysis</b>.</p> : (
            <>
              <p>Status <b>{run.status}</b>{run.stop_reason ? ` — ${run.stop_reason}` : ""}. Iterations used: {run.iteration} / {run.max_iterations}.</p>
              {best ? (
                <div className={`banner ${best.verified ? "ok" : best.verified === false ? "err" : "info"}`}>
                  <b>{best.verified ? "Verified plan" : best.verified === false ? "Plan failed verification" : "Proposed plan"}:</b> {best.title}
                  <div className="small" style={{ marginTop: 4 }}>
                    Capex {usd(best.objective.capex_usd)} · opex {usd(best.objective.opex_usd_per_year)}/yr ·
                    residual violations {best.metrics.hard_count}
                    {best.verification ? ` · resolved ${best.verification.resolved}, new ${best.verification.new}` : ""}
                  </div>
                </div>
              ) : null}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Link to="/circuit?state=verified&layer=faults"><button>View corrected circuit</button></Link>
                <Link to="/compare"><button>Before / after</button></Link>
                <Link to="/agents"><button>Agent activity</button></Link>
                <Link to="/report"><button>Engineering report</button></Link>
              </div>
            </>
          )}
        </div>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Data provenance</h2>
          <ul className="small">
            {(circuit?.sources ?? []).map((s) => (
              <li key={s.name}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</li>
            ))}
            <li>Load shapes: <a href="https://simbench.de" target="_blank" rel="noreferrer">SimBench</a> standard profiles (CC BY 4.0)</li>
            <li>Weather: <a href="https://open-meteo.com" target="_blank" rel="noreferrer">Open-Meteo</a> hourly data, San Jose, CA (CC BY 4.0)</li>
            <li>Costs and the TOU tariff are labelled planning assumptions.</li>
          </ul>
          <p className="small muted">All numbers on every page are read from AC power-flow results persisted in MongoDB; nothing is hard-coded.</p>
        </div>
      </div>
    </div>
  );
}
