import { useEffect, useMemo } from "react";
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { BranchResult, BusResult, Circuit, Equipment, Intervention, SimResult, Status, Thresholds } from "../types";
import { STATUS_COLOR, fmt, loadingStatus, voltageStatus, worst } from "../status";

export type Layer = "voltage" | "loading" | "current" | "p" | "q" | "losses" | "faults" | "existing" | "proposed" | "datacenter";
export type Mode = "original" | "proposed" | "verified";

export const LAYERS: { key: Layer; label: string }[] = [
  { key: "voltage", label: "Voltage" }, { key: "loading", label: "Loading" }, { key: "current", label: "Current" },
  { key: "p", label: "Active P" }, { key: "q", label: "Reactive Q" }, { key: "losses", label: "Losses" },
  { key: "faults", label: "Faults" }, { key: "existing", label: "Existing" }, { key: "proposed", label: "Proposed" },
  { key: "datacenter", label: "Data center" },
];

interface NodeData extends Record<string, unknown> {
  kind: string; label: string; sub?: string; color: string; dim: boolean; proposed: boolean; badge?: string;
  selected: boolean; focus: boolean; labelPos?: "top" | "bottom";
}

const NEUTRAL = "var(--ink-3)";
const SYMBOL = 34;

function Label({ d, offset = 40 }: { d: NodeData; offset?: number }) {
  return (
    <div className="node-label" style={d.labelPos === "bottom" ? { top: offset, left: -20 } : { bottom: offset, left: -20 }}>
      <div>{d.label}{d.badge ? <span className="tag proposed" style={{ marginLeft: 4 }}>{d.badge}</span> : null}</div>
      {d.sub ? <div className="node-value" style={{ color: d.color === NEUTRAL ? "var(--ink-2)" : d.color }}>{d.sub}</div> : null}
    </div>
  );
}

function ringClass(d: NodeData): string {
  return [d.selected ? "selected-ring" : "", d.focus ? "focus-ring" : "", d.proposed ? "proposed-node" : "", d.dim ? "dimmed" : ""].join(" ");
}

function BusNode({ data }: NodeProps<Node<NodeData>>) {
  return (
    <div className={ringClass(data)} title={data.label}>
      <div className="bus-node" style={{ background: data.color === NEUTRAL ? "var(--bus)" : data.color }}>
        <Handle type="target" position={Position.Left} id="l" style={{ top: 37 }} />
        <Handle type="source" position={Position.Left} id="ls" style={{ top: 37 }} />
        <Handle type="source" position={Position.Right} id="r" style={{ top: 37 }} />
        <Handle type="target" position={Position.Right} id="rt" style={{ top: 37 }} />
        <Handle type="source" position={Position.Bottom} id="b" />
        <Handle type="target" position={Position.Top} id="t" />
      </div>
      <div className="node-label" style={{ bottom: 80, left: -18 }}>
        <div>{data.label}</div>
        {data.sub ? <div className="node-value" style={{ color: data.color === NEUTRAL ? "var(--ink-2)" : data.color }}>{data.sub}</div> : null}
      </div>
    </div>
  );
}

function SymbolNode({ data }: NodeProps<Node<NodeData>>) {
  const c = data.color === NEUTRAL ? "var(--ink)" : data.color;
  return (
    <div className={ringClass(data)} style={{ width: SYMBOL, height: SYMBOL, position: "relative" }}>
      <Handle type="target" position={Position.Left} id="l" />
      <Handle type="source" position={Position.Left} id="ls" />
      <Handle type="source" position={Position.Right} id="r" />
      <Handle type="target" position={Position.Right} id="rt" />
      <Handle type="target" position={Position.Top} id="t" />
      <svg width={SYMBOL} height={SYMBOL} viewBox="0 0 34 34" className="sym">{symbolPath(data.kind, c)}</svg>
      <Label d={data} offset={data.labelPos === "bottom" ? 38 : 38} />
    </div>
  );
}

function symbolPath(kind: string, c: string) {
  const s = { stroke: c, strokeWidth: 2, fill: "none" } as const;
  switch (kind) {
    case "transformer":
    case "regulator":
      return (<g><circle cx="12" cy="17" r="8" {...s} /><circle cx="22" cy="17" r="8" {...s} />
        {kind === "regulator" ? <path d="M5 28 L29 6" {...s} markerEnd="" /> : null}</g>);
    case "ext_grid":
      return (<g><rect x="3" y="3" width="28" height="28" {...s} />
        <path d="M3 3 L31 31 M3 17 L17 31 M17 3 L31 17 M3 31 L31 3" stroke={c} strokeWidth="1" /></g>);
    case "load":
      return <path d="M17 2 L17 18 M8 18 L26 18 L17 32 Z" {...s} fill={c} fillOpacity={0.15} />;
    case "capacitor":
      return <g><path d="M17 2 L17 13 M6 13 L28 13 M6 19 L28 19 M17 19 L17 32" {...s} /></g>;
    case "sgen":
      return (<g><rect x="4" y="6" width="26" height="22" {...s} />
        <path d="M4 17 L30 17 M13 6 L13 28 M21 6 L21 28" stroke={c} strokeWidth="1" /></g>);
    case "storage":
      return (<g><rect x="4" y="9" width="24" height="18" {...s} /><rect x="28" y="14" width="3" height="8" fill={c} />
        <path d="M10 18 L16 18 M13 15 L13 21 M19 18 L24 18" {...s} /></g>);
    case "generator":
      return (<g><circle cx="17" cy="17" r="13" {...s} />
        <text x="17" y="22" textAnchor="middle" fontSize="14" fill={c} fontFamily="IBM Plex Mono">G</text></g>);
    default:
      return <circle cx="17" cy="17" r="10" {...s} />;
  }
}

const nodeTypes = { bus: BusNode, symbol: SymbolNode };

interface Props {
  circuit: Circuit;
  result: SimResult | null;
  layer: Layer;
  mode: Mode;
  selected?: string | null;
  focus?: string | null;
  onSelect?: (id: string) => void;
  showMiniMap?: boolean;
}

interface Index {
  bus: Map<string, BusResult>;
  branch: Map<string, BranchResult>;
  load: Map<string, { p_mw: number; q_mvar: number }>;
}

function indexResult(r: SimResult | null): Index {
  return {
    bus: new Map((r?.buses ?? []).map((b) => [b.id, b])),
    branch: new Map((r?.branches ?? []).map((b) => [b.id, b])),
    load: new Map<string, { p_mw: number; q_mvar: number }>([
      ...(r?.loads ?? []).map((l): [string, { p_mw: number; q_mvar: number }] => [l.id, { p_mw: l.p_mw, q_mvar: l.q_mvar }]),
      ...(r?.sgens ?? []).map((s): [string, { p_mw: number; q_mvar: number }] => [s.id, { p_mw: s.p_mw, q_mvar: s.q_mvar }]),
      ...(r?.storage ?? []).map((s): [string, { p_mw: number; q_mvar: number }] => [s.id, { p_mw: s.p_mw, q_mvar: 0 }]),
      ...(r?.capacitors ?? []).map((c): [string, { p_mw: number; q_mvar: number }] => [c.id, { p_mw: 0, q_mvar: -c.q_injected_mvar }]),
    ]),
  };
}

function interventionTargets(interventions: Intervention[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const i of interventions) {
    if (i.type === "tap") out.set(String(i.transformer), `tap ${i.tap_pos}`);
    if (i.type === "transformer_upgrade") out.set(String(i.transformer), `${i.sn_mva} MVA`);
    if (i.type === "line_upgrade") out.set(String(i.line), "upgrade");
    if (i.type === "switch") out.set(String(i.switch), i.closed ? "close" : "open");
    if (i.type === "load_adjust") out.set(String(i.load), `${Number(i.delta_mw).toFixed(2)} MW`);
    if (i.type === "storage_dispatch") out.set(String(i.storage), `${Number(i.p_mw).toFixed(2)} MW`);
  }
  return out;
}

export function buildGraph(circuit: Circuit, result: SimResult | null, layer: Layer, mode: Mode,
                           selected?: string | null, focus?: string | null): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const th: Thresholds = circuit.effective_thresholds;
  const idx = indexResult(result);
  const showProposed = mode !== "original" && layer !== "existing";
  const changes = showProposed ? interventionTargets(result?.interventions ?? []) : new Map<string, string>();
  const eqs = circuit.equipment;
  const byId = new Map(eqs.map((e) => [e.eq_id, e]));
  const pos = new Map<string, { x: number; y: number }>();
  eqs.forEach((e) => e.layout && pos.set(e.eq_id, e.layout));
  const openLines = new Set(eqs.filter((e) => e.type === "switch" && !e.params.closed).map((e) => String(e.params.element)));
  if (showProposed) {
    for (const i of result?.interventions ?? []) {
      if (i.type === "switch" && i.closed) {
        const sw = byId.get(String(i.switch));
        if (sw) openLines.delete(String(sw.params.element));
      }
    }
  }
  const dcGroup = (id: string) => byId.get(id)?.group === "datacenter";
  const nodes: Node<NodeData>[] = [];
  const edges: Edge[] = [];

  const busStatus = (id: string): Status => voltageStatus(idx.bus.get(id)?.vm_pu, th);
  const branchStatus = (b?: BranchResult): Status => (b && b.in_service ? loadingStatus(b.loading_percent, b.kind, th) : "unknown");

  const colorFor = (st: Status, relevant: boolean) => (relevant ? STATUS_COLOR[st] : NEUTRAL);
  const dimFor = (id: string, isChanged: boolean) =>
    (layer === "proposed" && !isChanged) || (layer === "datacenter" && !dcGroup(id));

  for (const e of eqs) {
    if (e.type === "line" || e.type === "switch" || !pos.has(e.eq_id)) continue;
    const p = pos.get(e.eq_id)!;
    const changed = changes.has(e.eq_id);
    let color = NEUTRAL;
    let sub: string | undefined;
    if (e.type === "bus") {
      const b = idx.bus.get(e.eq_id);
      const st = busStatus(e.eq_id);
      color = colorFor(st, ["voltage", "faults", "existing", "proposed", "datacenter"].includes(layer) && (layer !== "faults" || st === "moderate" || st === "severe" || st === "warning"));
      if (b) sub = layer === "p" ? `${fmt(b.p_mw, 2)} MW` : layer === "q" ? `${fmt(b.q_mvar, 2)} Mvar` : `${fmt(b.vm_pu, 4)} pu · ${fmt(b.v_kv, 2)} kV`;
    } else if (e.type === "transformer") {
      const b = idx.branch.get(e.eq_id);
      const st = branchStatus(b);
      color = colorFor(st, ["loading", "current", "faults", "existing", "proposed", "datacenter"].includes(layer) && (layer !== "faults" || st !== "ok"));
      if (b) sub = layer === "current" ? `${fmt(b.i_ka * 1000, 0)} / ${fmt(b.i_rated_ka * 1000, 0)} A`
        : layer === "losses" ? `${fmt(b.pl_mw * 1000, 1)} kW` : layer === "p" ? `${fmt(b.p_from_mw, 2)} MW`
        : layer === "q" ? `${fmt(b.q_from_mvar, 2)} Mvar` : `${fmt(b.loading_percent, 1)} %${b.tap_pos !== null && b.tap_pos !== undefined ? ` · tap ${b.tap_pos}` : ""}`;
    } else {
      const v = idx.load.get(e.eq_id);
      if (v) sub = e.type === "capacitor" ? `${fmt(-v.q_mvar, 2)} Mvar` : `${fmt(v.p_mw, 2)} MW`;
      if (e.type === "ext_grid" && result) {
        const g = result.ext_grid[0];
        sub = g ? `${fmt(g.p_mw, 2)} MW · ${fmt(g.q_mvar, 2)} Mvar` : undefined;
      }
      if (e.type === "generator" && !e.params.in_service) sub = "standby";
      const supply = String(e.params.bus ?? "");
      color = layer === "faults" || layer === "voltage" ? colorFor(busStatus(supply), busStatus(supply) !== "ok") : NEUTRAL;
      if (layer === "datacenter" && dcGroup(e.eq_id)) color = "var(--accent)";
    }
    nodes.push({
      id: e.eq_id, type: e.type === "bus" ? "bus" : "symbol", position: p, draggable: false,
      data: { kind: e.type, label: e.eq_id, sub, color, dim: dimFor(e.eq_id, changed), proposed: changed,
              badge: changed ? changes.get(e.eq_id) : undefined, selected: selected === e.eq_id, focus: focus === e.eq_id,
              labelPos: ["load", "capacitor", "sgen", "storage", "generator"].includes(e.type) ? "bottom" : "top" },
    });
  }

  // Proposed equipment added by interventions (only in proposed/verified states)
  const alias = new Map<string, string>();
  if (showProposed) {
    const slots = new Map<string, number>();
    eqs.filter((e) => ["load", "capacitor", "sgen", "storage", "generator"].includes(e.type))
      .forEach((e) => slots.set(String(e.params.bus), (slots.get(String(e.params.bus)) ?? 0) + 1));
    for (const pe of result?.proposed_equipment ?? []) {
      if (pe.type === "capacitor") {
        const bus = String(pe.params.bus);
        const bp = pos.get(bus);
        if (!bp) continue;
        const k = slots.get(bus) ?? 0;
        slots.set(bus, k + 1);
        const cap = result?.capacitors.find((c) => c.id === pe.eq_id);
        nodes.push({ id: pe.eq_id, type: "symbol", position: { x: bp.x - 60 + k * 95, y: bp.y + 150 }, draggable: false,
          data: { kind: "capacitor", label: pe.eq_id, sub: cap ? `${fmt(cap.q_injected_mvar, 2)} Mvar` : `${pe.params.q_mvar_rated} Mvar`,
                  color: "var(--proposed)", dim: false, proposed: true, badge: "NEW", selected: selected === pe.eq_id,
                  focus: focus === pe.eq_id, labelPos: "bottom" } });
        edges.push({ id: `dev-${pe.eq_id}`, source: bus, sourceHandle: "b", target: pe.eq_id, targetHandle: "t",
                     style: { stroke: "var(--proposed)", strokeDasharray: "5 4", strokeWidth: 2 } });
      }
      if (pe.type === "regulator") {
        const from = String(pe.params.from_bus);
        const line = byId.get(String(pe.params.line));
        const to = line ? String(line.params.to_bus) : "";
        const a = pos.get(from), b = pos.get(to);
        if (!a || !b) continue;
        const br = idx.branch.get(pe.eq_id);
        nodes.push({ id: pe.eq_id, type: "symbol", position: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + 20 }, draggable: false,
          data: { kind: "regulator", label: pe.eq_id, sub: br ? `tap ${br.tap_pos} · ${fmt(br.loading_percent, 1)} %` : undefined,
                  color: "var(--proposed)", dim: false, proposed: true, badge: "NEW", selected: selected === pe.eq_id,
                  focus: focus === pe.eq_id } });
        alias.set(String(pe.params.to_bus), pe.eq_id);
        edges.push({ id: `reg-${pe.eq_id}`, source: from, sourceHandle: "r", target: pe.eq_id, targetHandle: "l",
                     style: { stroke: "var(--proposed)", strokeWidth: 2.5 } });
      }
    }
  }

  const xOf = (id: string) => nodes.find((n) => n.id === id)?.position.x ?? 0;
  const handles = (s: string, t: string) => (xOf(s) <= xOf(t) ? { sourceHandle: "r", targetHandle: "l" } : { sourceHandle: "ls", targetHandle: "rt" });

  for (const e of eqs) {
    if (e.type === "line") {
      const r = idx.branch.get(e.eq_id);
      const source = alias.get(String(r?.from ?? "")) ?? String(r?.from ?? e.params.from_bus);
      const target = String(r?.to ?? e.params.to_bus);
      const isOpen = openLines.has(e.eq_id);
      const st = isOpen ? "unknown" : branchStatus(r);
      const changed = changes.has(e.eq_id);
      const relevant = ["loading", "current", "faults", "existing", "proposed", "datacenter", "losses"].includes(layer) && (layer !== "faults" || st !== "ok");
      let stroke = isOpen ? "var(--unknown)" : relevant ? STATUS_COLOR[st] : "var(--ink-2)";
      if (changed) stroke = "var(--proposed)";
      let label = e.eq_id;
      if (r && !isOpen) {
        label = layer === "current" ? `${fmt(r.i_ka * 1000, 0)}/${fmt(r.i_rated_ka * 1000, 0)} A`
          : layer === "p" ? `${fmt(Math.abs(r.p_from_mw), 2)} MW` : layer === "q" ? `${fmt(Math.abs(r.q_from_mvar), 2)} Mvar`
          : layer === "losses" ? `${fmt(r.pl_mw * 1000, 1)} kW` : layer === "voltage" ? `ΔV ${fmt(r.vdrop_percent, 2)} %`
          : `${e.eq_id} · ${fmt(r.loading_percent, 1)} %`;
      }
      if (isOpen) label = `${e.eq_id} · OPEN tie`;
      if (changed) label = `${label} · ${changes.get(e.eq_id)}`;
      const flow = layer === "q" ? r?.q_from_mvar ?? 0 : r?.p_from_mw ?? 0;
      const width = layer === "p" && r ? Math.min(7, 1.5 + Math.abs(r.p_from_mw)) : st === "moderate" || st === "severe" ? 3.5 : 2;
      const arrow = { type: MarkerType.ArrowClosed, color: stroke, width: 14, height: 14 };
      const dim = dimFor(e.eq_id, changed) && !(layer === "datacenter" && (dcGroup(source) || dcGroup(target)));
      edges.push({
        id: e.eq_id, source, target, ...handles(source, target), label, type: "smoothstep",
        labelBgPadding: [4, 2], labelBgBorderRadius: 3,
        style: { stroke, strokeWidth: selected === e.eq_id ? width + 2.5 : width, strokeDasharray: isOpen ? "6 5" : changed ? "8 3" : undefined,
                 opacity: dim ? 0.25 : 1 },
        markerEnd: !isOpen && flow >= 0 && ["p", "q", "loading", "current"].includes(layer) ? arrow : undefined,
        markerStart: !isOpen && flow < 0 && ["p", "q", "loading", "current"].includes(layer) ? arrow : undefined,
        animated: focus === e.eq_id,
      });
    }
    if (e.type === "transformer") {
      const r = idx.branch.get(e.eq_id);
      const st = branchStatus(r);
      const relevant = ["loading", "current", "faults", "existing", "proposed", "datacenter"].includes(layer) && (layer !== "faults" || st !== "ok");
      const stroke = changes.has(e.eq_id) ? "var(--proposed)" : relevant ? STATUS_COLOR[st] : "var(--ink-2)";
      const hv = String(e.params.hv_bus), lv = String(e.params.lv_bus);
      const dim = dimFor(e.eq_id, changes.has(e.eq_id));
      edges.push({ id: `${e.eq_id}-hv`, source: hv, ...handles(hv, e.eq_id), target: e.eq_id, style: { stroke, strokeWidth: 2.5, opacity: dim ? 0.25 : 1 } });
      edges.push({ id: `${e.eq_id}-lv`, source: e.eq_id, ...handles(e.eq_id, lv), target: lv, style: { stroke, strokeWidth: 2.5, opacity: dim ? 0.25 : 1 } });
    }
    if (["load", "capacitor", "sgen", "storage", "generator"].includes(e.type)) {
      const bus = String(e.params.bus);
      const changed = changes.has(e.eq_id);
      edges.push({ id: `dev-${e.eq_id}`, source: bus, sourceHandle: "b", target: e.eq_id, targetHandle: "t",
                   style: { stroke: changed ? "var(--proposed)" : "var(--ink-3)", strokeWidth: 1.4,
                            strokeDasharray: e.type === "generator" && !e.params.in_service ? "3 3" : undefined,
                            opacity: dimFor(e.eq_id, changed) ? 0.25 : 1 } });
    }
    if (e.type === "ext_grid") {
      edges.push({ id: `dev-${e.eq_id}`, source: e.eq_id, sourceHandle: "r", target: String(e.params.bus), targetHandle: "l",
                   style: { stroke: "var(--ink-2)", strokeWidth: 2.5 } });
    }
  }
  return { nodes, edges };
}

function FocusController({ focus, edges }: { focus?: string | null; edges: Edge[] }) {
  const flow = useReactFlow();
  useEffect(() => {
    if (!focus) return;
    const edge = edges.find((e) => e.id === focus);
    const ids = edge ? [edge.source, edge.target] : [focus];
    const t = window.setTimeout(() => flow.fitView({ nodes: ids.map((id) => ({ id })), duration: 600, padding: 1.2, maxZoom: 1.3 }), 60);
    return () => window.clearTimeout(t);
  }, [focus, edges, flow]);
  return null;
}

export default function CircuitDiagram({ circuit, result, layer, mode, selected, focus, onSelect, showMiniMap = true }: Props) {
  const { nodes, edges } = useMemo(() => buildGraph(circuit, result, layer, mode, selected, focus),
    [circuit, result, layer, mode, selected, focus]);
  return (
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} defaultEdgeOptions={{ type: "smoothstep" }} fitView minZoom={0.15} maxZoom={2.5}
      nodesConnectable={false} elementsSelectable onlyRenderVisibleElements={false}
      onNodeClick={(_, n) => onSelect?.(n.id)}
      onEdgeClick={(_, e) => onSelect?.(e.id.replace(/-(hv|lv)$/, "").replace(/^(dev|reg)-/, ""))}
      proOptions={{ hideAttribution: true }}>
      <Background gap={24} size={1} color="var(--line)" />
      <Controls showInteractive={false} />
      {showMiniMap ? <MiniMap pannable zoomable nodeColor={(n) => String((n.data as NodeData).color).startsWith("var(--ink") ? "#8894a0" : getComputedColor((n.data as NodeData).color)} /> : null}
      <FocusController focus={focus} edges={edges} />
    </ReactFlow>
  );
}

function getComputedColor(cssVar: string): string {
  const m = /var\((--[a-z-]+)\)/.exec(cssVar);
  if (!m) return cssVar;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || "#888";
}

export function Legend() {
  const items: Status[] = ["ok", "warning", "moderate", "severe", "unknown"];
  const label: Record<Status, string> = { ok: "Within limits", warning: "Approaching limit", moderate: "Violation",
    severe: "Severe / rating exceeded", unknown: "No data · disabled · open" };
  return (
    <div className="legend">
      {items.map((s) => (<div key={s}><span className="dot" style={{ background: STATUS_COLOR[s] }} /> {label[s]}</div>))}
      <div><span className="dot" style={{ background: "var(--proposed)" }} /> Proposed change</div>
    </div>
  );
}

export function statusOf(circuit: Circuit, result: SimResult | null, eq: Equipment): Status {
  const th = circuit.effective_thresholds;
  if (!result) return "unknown";
  if (eq.type === "bus") return voltageStatus(result.buses.find((b) => b.id === eq.eq_id)?.vm_pu, th);
  const br = result.branches.find((b) => b.id === eq.eq_id);
  if (br) return loadingStatus(br.loading_percent, br.kind, th);
  const bus = String(eq.params.bus ?? "");
  return bus ? worst(voltageStatus(result.buses.find((b) => b.id === bus)?.vm_pu, th)) : "unknown";
}
