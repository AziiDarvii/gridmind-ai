import { useMemo, useState } from "react";
import DetailPanel from "../components/DetailPanel";
import { statusOf } from "../components/CircuitDiagram";
import { STATUS_COLOR, fmt } from "../status";
import { useStore } from "../store";
import { useScenarioBaseResult } from "./CircuitPage";
import type { SimResult } from "../types";

const TYPES = ["all", "bus", "line", "transformer", "load", "capacitor", "storage", "sgen", "generator", "switch", "ext_grid"];

function keyValue(r: SimResult | null, id: string): string {
  if (!r) return "—";
  const b = r.buses.find((x) => x.id === id);
  if (b) return `${fmt(b.vm_pu, 4)} pu · ${fmt(b.v_kv, 2)} kV`;
  const br = r.branches.find((x) => x.id === id);
  if (br) return `${fmt(br.loading_percent, 1)} % · ${fmt(br.i_ka * 1000, 0)} A`;
  const l = r.loads.find((x) => x.id === id);
  if (l) return `${fmt(l.p_mw, 3)} MW · ${fmt(l.q_mvar, 3)} Mvar · pf ${fmt(l.pf, 3)}`;
  return "—";
}

export default function ComponentsPage() {
  const { circuit } = useStore();
  const result = useScenarioBaseResult();
  const [type, setType] = useState("all");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const rows = useMemo(() => (circuit?.equipment ?? []).filter((e) =>
    (type === "all" || e.type === type) && (`${e.eq_id} ${e.name}`.toLowerCase().includes(q.toLowerCase()))), [circuit, type, q]);
  if (!circuit) return null;
  return (
    <div style={{ display: "flex", gap: 0, height: "100%" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1>Component explorer</h1>
        <p className="muted small">{circuit.equipment.length} modelled components. Values from result <span className="mono">{result?._id}</span> ({result?.label}).</p>
        <div style={{ display: "flex", gap: 8, margin: "10px 0", flexWrap: "wrap" }}>
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type filter">
            {TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          <input type="search" placeholder="Search id or name" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="panel table-wrap">
          <table>
            <thead><tr><th /><th>ID</th><th>Type</th><th>Name</th><th>Key values</th><th>Data quality</th></tr></thead>
            <tbody>
              {rows.map((e) => {
                const st = statusOf(circuit, result, e);
                const assumed = Object.values(e.data_quality ?? {}).filter((x) => x === "assumed").length;
                return (
                  <tr key={e.eq_id} className="clickable" onClick={() => setSelected(e.eq_id)}>
                    <td><span className="dot" style={{ background: STATUS_COLOR[st] }} /></td>
                    <td className="mono">{e.eq_id}</td><td>{e.type}</td><td>{e.name}</td>
                    <td className="mono small">{keyValue(result, e.eq_id)}</td>
                    <td>{assumed ? <span className="tag assumed">{assumed} assumed</span> : <span className="tag input">source data</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      {selected ? <DetailPanel circuit={circuit} eqId={selected} result={result} onSelect={setSelected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}
