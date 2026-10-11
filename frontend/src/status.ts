import type { Status, Thresholds } from "./types";

export const STATUS_LABEL: Record<Status, string> = {
  ok: "Within limits",
  warning: "Approaching limit",
  moderate: "Violation",
  severe: "Severe violation",
  unknown: "No data / disabled",
};

export const STATUS_COLOR: Record<Status, string> = {
  ok: "var(--ok)",
  warning: "var(--warn)",
  moderate: "var(--moderate)",
  severe: "var(--severe)",
  unknown: "var(--unknown)",
};

export function voltageStatus(vm: number | undefined | null, th: Thresholds): Status {
  if (vm === undefined || vm === null) return "unknown";
  const v = th.voltage;
  if (vm < v.severe_lower_pu || vm > v.severe_upper_pu) return "severe";
  if (vm < v.lower_pu || vm > v.upper_pu) return "moderate";
  if (vm < v.lower_pu + v.warning_margin_pu || vm > v.upper_pu - v.warning_margin_pu) return "warning";
  return "ok";
}

export function loadingStatus(loading: number | undefined | null, kind: string, th: Thresholds): Status {
  if (loading === undefined || loading === null) return "unknown";
  const band = th.loading[kind === "line" ? "line" : "transformer"];
  if (loading > band.severe) return "severe";
  if (loading > band.limit) return "moderate";
  if (loading > band.warning) return "warning";
  return "ok";
}

export const RANK: Record<Status, number> = { unknown: -1, ok: 0, warning: 1, moderate: 2, severe: 3 };

export function worst(...statuses: Status[]): Status {
  return statuses.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), "unknown" as Status);
}

export const fmt = (v: unknown, digits = 3): string =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : v === null || v === undefined ? "—" : String(v);

export const usd = (v: number | undefined | null): string =>
  typeof v === "number" ? `$${Math.round(v).toLocaleString("en-US")}` : "—";

export const signed = (v: number | null | undefined, digits = 3): string =>
  typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(digits)}` : "—";
