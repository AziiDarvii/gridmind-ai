import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmt, usd } from "../status";
import { useStore } from "../store";

export default function OptimizationPage() {
  const { run } = useStore();
  if (!run) return <p className="muted">Run the multi-agent analysis to see optimization results.</p>;
  const iterations = run.context?.iterations ?? [];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h1>Optimization results</h1>
        <p className="muted small">Objective (USD/year) = annuity × capex + annual loss cost + opex + 1,000,000 × hard violations +
          50,000 × normalized violation magnitude. Annuity 0.1175 (10 %, 20 years); losses valued at 95 USD/MWh × 1,800 loss-equivalent hours.
          All cost inputs are labelled planning assumptions.</p>
      </div>
      {iterations.map((it) => {
        const ranked = (it.ranked ?? []).slice(0, 10);
        const chart = ranked.map((p, i) => ({
          name: `#${i + 1}`, capex: p.objective.capex_annualized_usd, losses: p.objective.losses_cost_usd_per_year,
          opex: p.objective.opex_usd_per_year, penalty: Math.min(p.objective.violation_penalty, 400000),
        }));
        return (
          <div className="panel" key={it.iteration}>
            <h2 style={{ marginTop: 0 }}>Iteration {it.iteration}</h2>
            {it.search ? (
              <p className="small">{it.search.method}. <b>{it.search.plans_evaluated}</b> plans evaluated with <b>{it.search.power_flows_run}</b> AC
                power flows. Claim: <i>{it.search.optimality_claim}</i>.
                {it.search.seeded_from_memory?.length ? ` Seeded from memory: ${it.search.seeded_from_memory.join(", ")}.` : ""}</p>
            ) : <p className="muted">No search in this iteration.</p>}
            {chart.length ? (
              <div style={{ height: 260 }}>
                <ResponsiveContainer>
                  <BarChart data={chart} margin={{ left: 10, right: 8 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                    <YAxis tick={{ fontSize: 10, fill: "var(--ink-3)" }} />
                    <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} formatter={(v: number) => usd(v)} />
                    <Legend />
                    <Bar dataKey="capex" name="Annualized capex" stackId="a" fill="var(--accent)" />
                    <Bar dataKey="opex" name="Opex" stackId="a" fill="var(--proposed)" />
                    <Bar dataKey="losses" name="Loss cost" stackId="a" fill="var(--warn)" />
                    <Bar dataKey="penalty" name="Violation penalty (capped in chart)" stackId="a" fill="var(--severe)" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : null}
            <table>
              <thead><tr><th>#</th><th>Plan (candidate ids)</th><th>Feasible</th><th className="num">Residual</th><th className="num">Vmin pu</th>
                <th className="num">Max load %</th><th className="num">Losses MW</th><th className="num">Objective</th></tr></thead>
              <tbody>{ranked.map((p, i) => (
                <tr key={p.plan_id}><td>{i + 1}</td><td className="mono small">{p.plan_id}</td>
                  <td style={{ color: p.feasible ? "var(--ok)" : "var(--severe)" }}>{p.feasible ? "yes" : "no"}</td>
                  <td className="num">{p.metrics.hard_count}</td><td className="num">{fmt(p.metrics.min_vm_pu, 4)}</td>
                  <td className="num">{fmt(p.metrics.max_loading_percent, 1)}</td><td className="num">{fmt(p.metrics.losses_p_mw, 4)}</td>
                  <td className="num">{usd(p.objective.total)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}
