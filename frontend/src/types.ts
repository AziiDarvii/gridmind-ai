export type Status = "ok" | "warning" | "moderate" | "severe" | "unknown";

export interface Equipment {
  _id: string;
  eq_id: string;
  type: string;
  name: string;
  params: Record<string, unknown>;
  data_quality?: Record<string, "input" | "assumed" | "missing">;
  source?: string;
  group?: string | null;
  proposed?: boolean;
  layout?: { x: number; y: number } | null;
}

export interface Thresholds {
  voltage: { lower_pu: number; upper_pu: number; warning_margin_pu: number; severe_lower_pu: number; severe_upper_pu: number };
  loading: Record<"line" | "transformer", { warning: number; limit: number; severe: number }>;
  power_factor: { min: number };
  losses: { max_percent_of_load: number };
  datacenter: { connection_limit_mw: number };
}

export interface Circuit {
  _id: string;
  name: string;
  description: string;
  sources: { name: string; url: string | null }[];
  equipment: Equipment[];
  effective_thresholds: Thresholds;
}

export interface BusResult {
  id: string; vn_kv: number; vm_pu: number; v_kv: number; va_degree: number; deviation_percent: number;
  p_mw: number; q_mvar: number;
}

export interface BranchResult {
  id: string; kind: "line" | "transformer" | "regulator"; from: string; to: string; in_service: boolean;
  i_ka: number; i_rated_ka: number; loading_percent: number; p_from_mw: number; q_from_mvar: number;
  p_to_mw: number; q_to_mvar: number; pl_mw: number; ql_mvar: number; v_from_pu: number; v_to_pu: number;
  vdrop_percent: number; tap_pos?: number | null; sn_mva?: number; s_mva?: number;
}

export interface LoadResult { id: string; bus: string; p_mw: number; q_mvar: number; s_mva: number; pf: number }

export interface Violation {
  type: string; equipment_id: string; equipment_type: string; value: number; limit: number; unit: string;
  deviation: number; severity: Status; severity_basis: string; condition: string;
  _id?: string; status?: string; root_cause?: string; key?: string; run_id?: string; result_id?: string;
  i_ka?: number; i_rated_ka?: number; value_kv?: number;
}

export interface Totals {
  load_p_mw: number; load_q_mvar: number; losses_p_mw: number; losses_q_mvar: number; losses_percent: number;
  grid_p_mw: number; grid_q_mvar: number; grid_pf: number; datacenter_p_mw: number; min_vm_pu: number;
  max_vm_pu: number; max_loading_percent: number;
}

export interface SimResult {
  _id: string; scenario_id: string; kind: string; label: string; converged: boolean;
  buses: BusResult[]; branches: BranchResult[]; loads: LoadResult[];
  capacitors: { id: string; bus: string; q_injected_mvar: number; in_service: boolean }[];
  sgens: { id: string; p_mw: number; q_mvar: number }[];
  storage: { id: string; p_mw: number }[];
  ext_grid: { id: string; p_mw: number; q_mvar: number }[];
  totals: Totals; violations: Violation[]; interventions: Intervention[];
  proposed_equipment: { eq_id: string; type: string; params: Record<string, unknown> }[];
  diagnosis?: { likely_cause: string };
  created_at: string;
}

export interface Intervention { type: string; [key: string]: unknown }

export interface Scenario { _id: string; name: string; description: string; kind: string; conditions: Record<string, unknown> }

export interface AgentTask {
  _id: string; run_id: string; agent: string; agent_label: string; step: string; iteration: number; task: string;
  status: string; started_at: string; finished_at: string | null; duration_s?: number; output_summary?: string;
  details?: Record<string, unknown>; verification?: string; error?: string;
}

export interface Candidate {
  id: string; category: string; title: string; interventions: Intervention[]; sizing: Record<string, unknown>;
  capex_usd: number; opex_usd_per_year: number; rationale: string; notes: string[]; targets: string[];
  historically_verified?: boolean;
}

export interface Objective {
  capex_usd: number; capex_annualized_usd: number; opex_usd_per_year: number; losses_cost_usd_per_year: number;
  violation_penalty: number; total: number;
}

export interface PlanSummary {
  plan_id: string; candidate_ids: string[]; interventions: Intervention[]; objective: Objective; feasible: boolean;
  metrics: { hard_count: number; hard_keys: string[]; min_vm_pu: number; max_loading_percent: number;
             losses_p_mw: number; grid_pf: number; light_max_vm_pu: number | null; datacenter_p_mw: number };
}

export interface Iteration {
  iteration: number; candidates?: Candidate[]; ranked?: PlanSummary[];
  search?: { plans_evaluated: number; power_flows_run: number; method: string; optimality_claim: string; seeded_from_memory: string[] } | null;
  recommendation_ids?: string[]; proposed_result_id?: string; verified?: boolean; verified_result_id?: string;
  verified_light_result_id?: string; verified_recommendation_id?: string;
  comparison_counts?: { resolved: number; remaining: number; new: number };
  dc_schedule?: DcSchedule | null;
}

export interface DcSchedule {
  success: boolean; method?: string; optimality_claim?: string; baseline_schedule: number[]; optimized_schedule?: number[];
  shifted_mwh?: number; energy_cost_before_usd?: number; energy_cost_after_usd?: number; violation_hours_before?: number;
  violation_hours_after?: number; dc_total_before?: number[]; dc_total_after?: number[]; message?: string;
  timeseries_before?: TimeRow[]; timeseries_after?: TimeRow[];
}

export interface TimeRow {
  hour: number; converged: boolean; dc_mw: number; min_vm_pu?: number; worst_bus?: string;
  max_loading_percent?: number; losses_mw?: number; violations?: number; violation_keys?: string[];
  oltc_taps?: Record<string, number>;
}

export interface RootCause {
  violation_key: string; equipment_id: string; type: string;
  causes: { code: string; title: string; score: number; evidence: string }[];
  path?: { segments: { eq_id: string; kind: string; from: string; to: string; vdrop_percent: number; dv_p_percent: number;
                       dv_q_percent: number; loading_percent: number; r_ohm: number; x_ohm: number; p_mw: number; q_mvar: number }[];
           total_drop_percent: number; line_drop_percent: number; transformer_drop_percent: number; reactive_share: number };
  downstream_load_mw?: number; downstream_dc_mw?: number; value_without_dc?: number | null;
}

export interface Run {
  _id: string; circuit_id: string; scenario_id: string; status: string; iteration: number; max_iterations: number;
  completed_steps: string[]; current_step: string | null; stop_reason: string | null; verified?: boolean;
  created_at: string; updated_at: string; agent_graph?: Record<string, string[]>;
  context?: {
    base_result_id?: string; base_violations?: Violation[]; rootcauses?: RootCause[];
    rootcause_narrative?: { text: string; used_llm: boolean; tool_calls?: { tool: string; input: unknown }[]; note?: string };
    dc_analysis?: { daily_energy_mwh: number; daily_cost_usd: number; peak_mw: number; avg_pue: number; violation_hours: number;
                    timeseries: TimeRow[]; hourly: { components: Record<string, number[]>; total: number[]; pue: (number | null)[]; it_total: number[] };
                    flexibility: { flexible_loads: { id: string; name: string; base_mw: number; max_reduction_mw: number }[] } } | null;
    memory_hints?: { _id: string; signature: string; intervention_type: string; lesson: string; scenario_name: string }[];
    iterations?: Iteration[]; final_recommendation_id?: string; stop_reason?: string;
    report?: { markdown: string; executive_summary: string; llm_used: boolean; generated_at: string };
    unsupported_studies?: { study: string; status: string; reason: string }[];
    model?: { equipment_count: number; by_type: Record<string, number>; assumed_parameter_equipment: string[] };
  };
}

export interface Recommendation {
  _id: string; run_id: string; rank: number; plan_id: string; title: string; candidates: Candidate[];
  interventions: Intervention[]; objective: Objective; feasible: boolean; status: string; verified: boolean | null;
  tradeoffs: Record<string, number | boolean | null>; metrics: PlanSummary["metrics"];
  verification?: { resolved: number; remaining: number; new: number; hard_after: number };
  verified_result_id?: string;
}

export interface Comparison {
  comparable: boolean; reason?: string;
  buses: { id: string; vn_kv: number; before_pu: number | null; after_pu: number; change_pu: number | null; limit: string;
           before_status: Status; after_status: Status; added: boolean }[];
  branches: { id: string; kind: string; from: string; to: string; before_loading: number | null; after_loading: number;
              change_loading: number | null; before_i_ka: number | null; after_i_ka: number; limit: string;
              before_status: Status; after_status: Status; added: boolean; rating_changed: boolean;
              before_losses_mw: number | null; after_losses_mw: number }[];
  totals: Record<string, { before: number | null; after: number | null; change: number | null }>;
  violations: { resolved: Violation[]; remaining: Violation[]; new: Violation[]; before_count: number; after_count: number };
  interventions?: Intervention[];
}
