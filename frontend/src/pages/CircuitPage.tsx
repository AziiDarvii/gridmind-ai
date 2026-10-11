import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ReactFlowProvider } from "@xyflow/react";
import { api } from "../api";
import CircuitDiagram, { LAYERS, Legend, type Layer, type Mode } from "../components/CircuitDiagram";
import DetailPanel from "../components/DetailPanel";
import { STATUS_COLOR, fmt } from "../status";
import { useResult, useRunStates, useStore } from "../store";
import type { SimResult } from "../types";

/** Original-state result for the selected scenario: the active run's base result, else a fresh solve. */
export function useScenarioBaseResult(): SimResult | null {
  const { scenarioId, setError } = useStore();
  const states = useRunStates();
  const fromRun = useResult(states.original);
  const [fallback, setFallback] = useState<SimResult | null>(null);
  useEffect(() => {
    if (states.original) return;
    let alive = true;
    setFallback(null);
    api.latestResult(scenarioId, "base")
      .catch(() => api.simulate(scenarioId, [], "Original circuit (direct solve)"))
      .then((r) => alive && setFallback(r))
      .catch((e) => setError(e.message));
    return () => {
      alive = false;
    };
  }, [scenarioId, states.original, setError]);
  return states.original ? fromRun : fallback;
}

export default function CircuitPage() {
  const { circuit } = useStore();
  const states = useRunStates();
  const [params, setParams] = useSearchParams();
  const [mode, setMode] = useState<Mode>((params.get("state") as Mode) || "original");
  const [layer, setLayer] = useState<Layer>((params.get("layer") as Layer) || "faults");
  const [selected, setSelected] = useState<string | null>(params.get("focus"));
  const [focus, setFocus] = useState<string | null>(params.get("focus"));
  const [query, setQuery] = useState("");
  const base = useScenarioBaseResult();
  const proposed = useResult(states.proposed);
  const verified = useResult(states.verified);
  const result = mode === "original" ? base : mode === "proposed" ? proposed : verified;

  useEffect(() => {
    const f = params.get("focus");
    if (f) {
      setFocus(f);
      setSelected(f);
    }
  }, [params]);

  const options = useMemo(() => (circuit?.equipment ?? []).map((e) => ({ id: e.eq_id, name: e.name })), [circuit]);
  if (!circuit) return <div className="content">Loading circuit model from MongoDB…</div>;

  const violations = (result?.violations ?? []).filter((v) => v.severity === "moderate" || v.severity === "severe");
  const select = (id: string) => {
    setSelected(id);
    setFocus(id);
    setParams((p) => {
      p.set("focus", id);
      return p;
    }, { replace: true });
  };
  const search = (q: string) => {
    setQuery(q);
    const hit = options.find((o) => o.id.toLowerCase() === q.toLowerCase() || o.name.toLowerCase() === q.toLowerCase());
    if (hit) select(hit.id);
  };
  const modeAvailable = { original: true, proposed: Boolean(states.proposed), verified: Boolean(states.verified) };

  return (
    <div className="circuit-wrap">
      <div className="diagram">
        <div className="diagram-toolbar">
          <div className="seg" role="tablist" aria-label="Circuit state">
            {(["original", "proposed", "verified"] as Mode[]).map((m, i) => (
              <button key={m} className={mode === m ? "on" : ""} disabled={!modeAvailable[m]} onClick={() => setMode(m)}
                      title={modeAvailable[m] ? "" : "Run the multi-agent analysis first"}>
                {["A · Original", "B · Proposed", "C · Verified"][i]}
              </button>
            ))}
          </div>
          <div className="seg" aria-label="Visual layer">
            {LAYERS.map((l) => (
              <button key={l.key} className={layer === l.key ? "on" : ""} onClick={() => setLayer(l.key)}>{l.label}</button>
            ))}
          </div>
          <input type="search" list="eq-list" placeholder="Find bus, line, transformer, load…" value={query}
                 onChange={(e) => search(e.target.value)} style={{ minWidth: 240 }} aria-label="Search equipment" />
          <datalist id="eq-list">{options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</datalist>
        </div>
        {result && !result.converged ? (
          <div className="banner err" style={{ position: "absolute", top: 104, left: 10, zIndex: 6 }}>
            Power flow did not converge: {result.diagnosis?.likely_cause}
          </div>
        ) : null}
        {mode === "verified" && states.iteration ? (
          <div className={`banner ${states.iteration.verified ? "ok" : "err"}`} style={{ position: "absolute", top: 104, left: 10, zIndex: 6 }}>
            {states.iteration.verified ? "Independently verified: no violations at peak or light load."
              : `Not verified: ${violations.length} violation(s) remain — the circuit is not labelled as fixed.`}
          </div>
        ) : null}
        <ReactFlowProvider>
          {result ? (
            <CircuitDiagram circuit={circuit} result={result} layer={layer} mode={mode} selected={selected} focus={focus}
                            onSelect={select} />
          ) : <div style={{ padding: 80 }} className="muted">Solving the network…</div>}
        </ReactFlowProvider>
        <Legend />
      </div>
      {selected ? (
        <DetailPanel circuit={circuit} eqId={selected} result={result} baseline={mode === "original" ? null : base}
                     onSelect={select} onClose={() => setSelected(null)} />
      ) : (
        <aside className="side">
          <h1>{circuit.name}</h1>
          <p className="muted small">{circuit.description}</p>
          {result ? (
            <div className="kv" style={{ marginBottom: 12 }}>
              <div className="k">State</div><div className="v">{result.label}</div><div />
              <div className="k">Min voltage</div><div className="v">{fmt(result.totals?.min_vm_pu, 4)} pu</div><div />
              <div className="k">Max loading</div><div className="v">{fmt(result.totals?.max_loading_percent, 1)} %</div><div />
              <div className="k">Losses</div><div className="v">{fmt(result.totals?.losses_p_mw, 3)} MW</div><div />
              <div className="k">Grid PF</div><div className="v">{fmt(result.totals?.grid_pf, 3)}</div><div />
              <div className="k">Data center</div><div className="v">{fmt(result.totals?.datacenter_p_mw, 2)} MW</div><div />
            </div>
          ) : null}
          <h3>Violations in this state ({violations.length})</h3>
          {violations.length === 0 ? <div className="muted small">None at this operating point.</div> : null}
          <table>
            <tbody>
              {violations.map((v) => (
                <tr key={`${v.type}-${v.equipment_id}-${v.condition}`} className="clickable" onClick={() => select(v.equipment_id)}>
                  <td><span className="dot" style={{ background: STATUS_COLOR[v.severity] }} /></td>
                  <td className="mono">{v.equipment_id}</td>
                  <td>{v.type.replace("_", " ")}</td>
                  <td className="num">{fmt(v.value, v.unit === "pu" ? 4 : 1)} {v.unit}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 12 }}>
            Click any symbol or line to inspect every modelled parameter and solver value. Values are read from the
            persisted simulation result <span className="mono">{result?._id}</span>.
          </p>
        </aside>
      )}
    </div>
  );
}
