"""Violation engine: checks every modelled component against configurable limits.

Severity is never an opaque score: each violation carries the numeric basis
(value, limit, band) that produced its classification.
"""
from __future__ import annotations

from app.config import DEFAULT_THRESHOLDS

STATUS_ORDER = {"unknown": -1, "ok": 0, "warning": 1, "moderate": 2, "severe": 3}

UNSUPPORTED_STUDIES = [
    {"study": "short_circuit", "status": "requires further study",
     "reason": "Fault-level study needs protection device data and source impedances not in this model."},
    {"study": "protection_coordination", "status": "unsupported",
     "reason": "Relay/fuse curves and settings are not modelled."},
    {"study": "harmonics", "status": "unsupported",
     "reason": "No harmonic source spectra; capacitor resonance needs a harmonic study."},
    {"study": "transient_and_dynamic_stability", "status": "unsupported",
     "reason": "Steady-state power-flow model only; no machine or converter dynamic models."},
]


def merged_thresholds(custom: dict | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_THRESHOLDS.items()}
    for key, value in (custom or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def classify_voltage(vm_pu: float, th: dict) -> tuple[str, str]:
    v = th["voltage"]
    if vm_pu < v["severe_lower_pu"]:
        return "severe", f"{vm_pu:.4f} pu < severe band {v['severe_lower_pu']} pu"
    if vm_pu < v["lower_pu"]:
        return "moderate", f"{v['severe_lower_pu']} pu <= {vm_pu:.4f} pu < lower limit {v['lower_pu']} pu"
    if vm_pu > v["severe_upper_pu"]:
        return "severe", f"{vm_pu:.4f} pu > severe band {v['severe_upper_pu']} pu"
    if vm_pu > v["upper_pu"]:
        return "moderate", f"upper limit {v['upper_pu']} pu < {vm_pu:.4f} pu <= {v['severe_upper_pu']} pu"
    margin = v["warning_margin_pu"]
    if vm_pu < v["lower_pu"] + margin or vm_pu > v["upper_pu"] - margin:
        return "warning", f"within limits but inside the {margin} pu warning margin"
    return "ok", "within limits"


def classify_loading(loading: float, band: dict) -> tuple[str, str]:
    if loading > band["severe"]:
        return "severe", f"{loading:.1f} % > severe band {band['severe']} %"
    if loading > band["limit"]:
        return "moderate", f"{band['limit']} % < {loading:.1f} % <= {band['severe']} %"
    if loading > band["warning"]:
        return "warning", f"{loading:.1f} % is within the {band['warning']}-{band['limit']} % warning band"
    return "ok", "within rating"


def _violation(vtype: str, eq_id: str, eq_type: str, value: float, limit: float, unit: str, severity: str,
               basis: str, condition: str, **extra) -> dict:
    return {"type": vtype, "equipment_id": eq_id, "equipment_type": eq_type, "value": round(value, 5),
            "limit": limit, "unit": unit, "deviation": round(value - limit, 5), "severity": severity,
            "severity_basis": basis, "condition": condition, **extra}


def detect_violations(result: dict, meta: dict[str, dict], thresholds: dict | None = None,
                      condition: str = "peak") -> tuple[list[dict], dict[str, dict]]:
    """Return (violations, component_status) for one power-flow result."""
    th = merged_thresholds(thresholds)
    status: dict[str, dict] = {}
    if not result.get("converged"):
        diag = result.get("diagnosis", {})
        return ([_violation("convergence_failure", "network", "network", 0, 0, "", "severe",
                            diag.get("likely_cause", "power flow did not converge"), condition, diagnosis=diag)],
                {eq_id: {"status": "unknown", "reason": "no converged solution"} for eq_id in meta})
    violations: list[dict] = []
    vth = th["voltage"]
    for bus in result["buses"]:
        sev, basis = classify_voltage(bus["vm_pu"], th)
        status[bus["id"]] = {"status": sev, "reason": basis, "value": bus["vm_pu"], "metric": "vm_pu"}
        if sev in ("moderate", "severe"):
            under = bus["vm_pu"] < vth["lower_pu"]
            violations.append(_violation(
                "undervoltage" if under else "overvoltage", bus["id"], "bus", bus["vm_pu"],
                vth["lower_pu"] if under else vth["upper_pu"], "pu", sev, basis, condition,
                value_kv=bus["v_kv"], vn_kv=bus["vn_kv"]))
    for br in result["branches"]:
        if not br["in_service"]:
            status[br["id"]] = {"status": "unknown", "reason": "out of service / open"}
            continue
        kind = "line" if br["kind"] == "line" else "transformer"
        band = th["loading"][kind]
        sev, basis = classify_loading(br["loading_percent"] or 0.0, band)
        status[br["id"]] = {"status": sev, "reason": basis, "value": br["loading_percent"], "metric": "loading_percent"}
        if sev in ("moderate", "severe"):
            violations.append(_violation(
                "line_overload" if kind == "line" else "transformer_overload", br["id"], br["kind"],
                br["loading_percent"], band["limit"], "%", sev, basis, condition,
                i_ka=br["i_ka"], i_rated_ka=br["i_rated_ka"], from_bus=br["from"], to_bus=br["to"]))
    for load in result["loads"]:
        status[load["id"]] = {"status": status.get(load["bus"], {}).get("status", "ok"),
                              "reason": "inherits supply-bus voltage status"}

    totals = result["totals"]
    pf_min = th["power_factor"]["min"]
    if totals["grid_pf"] < pf_min:
        violations.append(_violation("poor_power_factor", result["ext_grid"][0]["id"], "ext_grid", totals["grid_pf"],
                                     pf_min, "pf", "warning", f"grid PF {totals['grid_pf']:.3f} < {pf_min}", condition))
    loss_lim = th["losses"]["max_percent_of_load"]
    if totals["losses_percent"] > loss_lim:
        violations.append(_violation("excessive_losses", "network", "network", totals["losses_percent"], loss_lim, "%",
                                     "warning", f"losses {totals['losses_percent']:.2f} % of load > {loss_lim} %", condition))
    dc_lim = th["datacenter"]["connection_limit_mw"]
    if totals["datacenter_p_mw"] > dc_lim:
        violations.append(_violation("datacenter_capacity", "datacenter", "datacenter", totals["datacenter_p_mw"], dc_lim,
                                     "MW", "moderate", f"DC demand {totals['datacenter_p_mw']:.2f} MW > contracted "
                                     f"connection capacity {dc_lim} MW", condition))
    for eq_id in meta:
        status.setdefault(eq_id, {"status": "ok", "reason": "no limit applicable"})
    return violations, status


def violation_key(v: dict) -> str:
    return f"{v['type']}|{v['equipment_id']}|{v.get('condition', 'peak')}"


def summarize(violations: list[dict]) -> dict:
    by_type: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for v in violations:
        by_type[v["type"]] = by_type.get(v["type"], 0) + 1
        by_sev[v["severity"]] = by_sev.get(v["severity"], 0) + 1
    return {"count": len(violations), "by_type": by_type, "by_severity": by_sev}


def hard_violations(violations: list[dict]) -> list[dict]:
    """Violations that must be resolved for a corrective plan to be accepted."""
    return [v for v in violations if v["severity"] in ("moderate", "severe")]
