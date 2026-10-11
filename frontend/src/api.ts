import type {
  AgentTask, Circuit, Comparison, Intervention, Recommendation, Run, Scenario, SimResult, Violation,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<{ status: string; llm_enabled: boolean; llm_model: string | null; database: string }>("/api/health"),
  config: () => request<Record<string, unknown>>("/api/config"),
  circuits: () => request<{ _id: string; name: string }[]>("/api/circuits"),
  circuit: (id: string) => request<Circuit>(`/api/circuits/${id}`),
  scenarios: (cid: string) => request<Scenario[]>(`/api/circuits/${cid}/scenarios`),
  simulate: (sid: string, interventions: Intervention[] = [], label?: string) =>
    post<SimResult>(`/api/scenarios/${sid}/simulate`, { interventions, label }),
  latestResult: (sid: string, kind?: string) =>
    request<SimResult>(`/api/scenarios/${sid}/latest-result${kind ? `?kind=${kind}` : ""}`),
  result: (id: string) => request<SimResult>(`/api/results/${id}`),
  compare: (before: string, after: string) => request<Comparison>(`/api/compare?before=${before}&after=${after}`),
  component: (cid: string, eq: string, resultId?: string) =>
    request<ComponentDetail>(`/api/circuits/${cid}/components/${encodeURIComponent(eq)}${resultId ? `?result_id=${resultId}` : ""}`),
  faults: (params: Record<string, string>) =>
    request<{ items: Violation[]; total: number }>(`/api/faults?${new URLSearchParams(params)}`),
  startRun: (circuit_id: string, scenario_id: string, max_iterations?: number) =>
    post<Run>("/api/runs", { circuit_id, scenario_id, max_iterations }),
  runs: (cid: string) => request<Run[]>(`/api/runs?circuit_id=${cid}`),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  tasks: (id: string) => request<AgentTask[]>(`/api/runs/${id}/tasks`),
  recommendations: (id: string) => request<Recommendation[]>(`/api/runs/${id}/recommendations`),
  resume: (id: string) => post<Run>(`/api/runs/${id}/resume`, {}),
  datacenter: (cid: string) => request<DataCenterInfo>(`/api/circuits/${cid}/datacenter`),
  whatif: (cid: string, body: { scenario_id: string; dc_scale: number; training_scale?: number; interventions?: Intervention[] }) =>
    post<WhatIf>(`/api/circuits/${cid}/datacenter/whatif`, body),
  memory: () => request<MemoryLesson[]>("/api/memory"),
  audit: () => request<{ _id: string; timestamp: string; action: string; entity_id: string; actor: string }[]>("/api/audit"),
};

export interface ComponentDetail {
  equipment: import("./types").Equipment;
  values: Record<string, unknown> | null;
  history: { result_id: string; kind: string; label: string; created_at: string; values: Record<string, number> }[];
  faults: Violation[];
  recommendations: { _id: string; title: string; rank: number; verified: boolean | null; status: string }[];
  connected: string[];
  supply_path: { eq_id: string; kind: string; from: string; to: string }[];
}

export interface DataCenterInfo {
  profile: { name: string; connection_bus: string; connection_limit_mw: number; location: { city: string };
             components: { eq_id: string; kind: string; flexible: boolean; min_mw?: number; max_mw?: number; notes?: string;
                           model?: Record<string, unknown> }[]; sla: Record<string, string>; assumptions: string[] };
  measurements: { _id: string; category: string; values: number[]; unit: string; source: string; source_url: string | null;
                  reference_date: string | null; fallback: boolean }[];
  hourly: { components: Record<string, number[]>; total: number[]; pue: (number | null)[]; it_total: number[] };
}

export interface WhatIf {
  result_id: string;
  base_totals: import("./types").Totals;
  whatif_totals: import("./types").Totals;
  violations: Violation[];
  comparison: Comparison;
}

export interface MemoryLesson {
  _id: string; signature: string; violation_type: string; intervention_type: string; parameters: Record<string, unknown>;
  verified: boolean; outcome: Record<string, unknown>; scenario_name: string; run_id: string; lesson: string; created_at: string;
}
