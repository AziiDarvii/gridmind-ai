import { Fragment, useState } from "react";
import { fmt, signed, usd } from "../status";
import { useStore } from "../store";
import type { Candidate } from "../types";

const SIZING_KEYS = ["q_required_mvar", "q_selected_mvar", "dv_dq_pu_per_mvar", "sensitivity_source", "selected", "current",
  "boost_percent", "length_km", "required_mva", "selected_mva", "reduction_mw", "shifted_mwh_per_day", "usable_mwh", "p_mw", "tie_line"];

function SizingSummary({ c }: { c: Candidate }) {
  const entries = SIZING_KEYS.filter((k) => c.sizing[k] !== undefined).map((k) => `${k}=${fmt(c.sizing[k], 4)}`);
  return <span className="mono small">{entries.join(" · ")}</span>;
}

export default function ActionsPage() {
  const { run, recommendations } = useStore();
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!run) return <p className="muted">Run the multi-agent analysis to generate corrective actions.</p>;
  const iterations = run.context?.iterations ?? [];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h1>Corrective-action comparison</h1>
        <p className="muted small">Candidates are generated from the diagnosed cause and sized by simulation (sensitivities, tap sweeps,
          rating arithmetic). Plans combine candidates and are ranked by an explicit objective. Every plan needs engineer approval;
          nothing is applied to real equipment.</p>
      </div>
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Ranked plans</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>Plan</th><th>Feasible</th><th className="num">Residual</th><th className="num">ΔVmin pu</th>
              <th className="num">ΔMax load %</th><th className="num">ΔLosses MW</th><th className="num">ΔPF</th><th className="num">Light-load Vmax</th>
              <th className="num">Capex</th><th className="num">Opex/yr</th><th>Verification</th></tr></thead>
            <tbody>
              {recommendations.map((r) => (
                <tr key={r._id}>
                  <td>{r.rank}</td><td>{r.title}</td>
                  <td style={{ color: r.feasible ? "var(--ok)" : "var(--severe)" }}>{r.feasible ? "yes" : "no"}</td>
                  <td className="num">{String(r.tradeoffs.residual_violations)}</td>
                  <td className="num">{signed(r.tradeoffs.min_vm_change_pu as number, 4)}</td>
                  <td className="num">{signed(r.tradeoffs.max_loading_change_percent as number, 1)}</td>
                  <td className="num">{signed(r.tradeoffs.losses_change_mw as number, 4)}</td>
                  <td className="num">{signed(r.tradeoffs.grid_pf_change as number, 3)}</td>
                  <td className="num">{fmt(r.tradeoffs.light_load_max_vm_pu, 4)}</td>
                  <td className="num">{usd(r.objective.capex_usd)}</td><td className="num">{usd(r.objective.opex_usd_per_year)}</td>
                  <td>{r.verified === true ? <span className="pill" style={{ color: "var(--ok)" }}>verified</span>
                    : r.verified === false ? <span className="pill" style={{ color: "var(--severe)" }}>failed</span> : <span className="muted">not run</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">Light-load Vmax is reported only for plans that pass at peak (the light-load solve catches capacitor or tap
          overvoltage). Costs are planning assumptions.</p>
      </div>
      {iterations.map((it) => (
        <div className="panel" key={it.iteration}>
          <h2 style={{ marginTop: 0 }}>Iteration {it.iteration}: {it.candidates?.length ?? 0} sized candidates</h2>
          <table>
            <thead><tr><th>Category</th><th>Candidate</th><th>Sizing (calculated)</th><th className="num">Capex</th><th className="num">Opex/yr</th><th>Memory</th></tr></thead>
            <tbody>
              {(it.candidates ?? []).map((c) => (
                <Fragment key={c.id}>
                  <tr className="clickable" onClick={() => setExpanded(expanded === c.id ? null : c.id)}>
                    <td><span className="tag">{c.category}</span></td>
                    <td>{c.title}<div className="small muted">{c.rationale}</div></td>
                    <td><SizingSummary c={c} /><div className="small muted">{String(c.sizing.method ?? "")}</div></td>
                    <td className="num">{usd(c.capex_usd)}</td><td className="num">{usd(c.opex_usd_per_year)}</td>
                    <td>{c.historically_verified ? <span className="tag input">verified before</span> : null}</td>
                  </tr>
                  {expanded === c.id ? (
                    <tr><td colSpan={6}>
                      <pre className="mono small" style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>{JSON.stringify({ interventions: c.interventions, sizing: c.sizing, notes: c.notes }, null, 2)}</pre>
                    </td></tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
