"""Corrective-action generation and sizing.

Each candidate is sized from the solved network (sensitivities, tap sweeps,
rating arithmetic) and checked by simulation. Candidate types depend on the
diagnosed cause: capacitors only for undervoltage / reactive-flow problems,
never for overvoltage.
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field

from app.config import (COST_ASSUMPTIONS, STANDARD_CAPACITOR_STEP_MVAR, STANDARD_TRANSFORMER_SIZES_MVA,
                        UPGRADE_CABLE)
from app.engine.simulate import simulate, simulate_conditions
from app.engine.topology import open_branches, path_from_source
from app.engine.violations import hard_violations, merged_thresholds, violation_key

LV_CAPACITOR_STEP_MVAR = 0.05
MAX_CAP_MV_MVAR = 6.0
MAX_CAP_LV_MVAR = 0.6
CAP_REFINEMENT_STEPS = 4
VOLTAGE_TARGET_MARGIN_PU = 0.005
LOADING_TARGET_FRACTION = 0.97
LV_TRANSFORMER_SIZES_MVA = (3.15, 4.0, 5.0)
BESS_MIN_SOC = 0.10
PEAK_DURATION_H = 3.0


@dataclass
class RemediationContext:
    equipment: list[dict]
    conditions: dict
    thresholds: dict
    base_interventions: list[dict] = field(default_factory=list)
    memory_hints: list[dict] = field(default_factory=list)
    cache: dict = field(default_factory=dict)
    evaluations: int = 0
    power_flows: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def th(self) -> dict:
        return merged_thresholds(self.thresholds)

    def by_id(self) -> dict[str, dict]:
        return {e["eq_id"]: e for e in self.equipment}

    def peak(self, interventions: list[dict]) -> dict:
        return simulate(self.equipment, self.base_interventions + interventions, self.conditions, self.thresholds)

    def evaluate(self, interventions: list[dict]) -> dict:
        """Peak + light-load verification of base interventions + the given ones (cached)."""
        key = json.dumps(interventions, sort_keys=True)
        with self._lock:
            if key in self.cache:
                return self.cache[key]
        full = self.base_interventions + interventions
        peak = simulate(self.equipment, full, self.conditions, self.thresholds, "peak")
        if hard_violations(peak["violations"]) or not peak["result"].get("converged"):
            # Light-load check only matters for plans that pass at peak (saves half the solves).
            out = {"peak": peak, "light": None, "violations": peak["violations"]}
            flows = 1
        else:
            out = simulate_conditions(self.equipment, full, self.conditions, self.thresholds)
            flows = 2
        metrics = plan_metrics(out)
        with self._lock:
            self.cache[key] = metrics
            self.evaluations += 1
            self.power_flows += flows
        return metrics


def plan_metrics(out: dict) -> dict:
    peak = out["peak"]["result"]
    light = out["light"]["result"] if out.get("light") else {}
    hard = hard_violations(out["violations"])
    magnitude = 0.0
    for v in hard:
        magnitude += abs(v["deviation"]) / (0.01 if v["unit"] == "pu" else 10.0)
    converged = peak.get("converged") and (light.get("converged") if light else True)
    totals = peak.get("totals", {})
    return {
        "converged": bool(converged), "hard_count": len(hard), "hard_keys": sorted(violation_key(v) for v in hard),
        "violations": out["violations"], "violation_magnitude": round(magnitude, 4),
        "min_vm_pu": totals.get("min_vm_pu"), "max_vm_pu": totals.get("max_vm_pu"),
        "light_max_vm_pu": light.get("totals", {}).get("max_vm_pu"),
        "max_loading_percent": totals.get("max_loading_percent"), "losses_p_mw": totals.get("losses_p_mw"),
        "grid_pf": totals.get("grid_pf"), "datacenter_p_mw": totals.get("datacenter_p_mw"),
        "light_checked": bool(light), "result": peak, "light_result": light or None,
    }


def _zone_of(equipment: list[dict], bus_id: str) -> str:
    path = path_from_source(equipment, bus_id)
    trafos = [s["eq_id"] for s in path if s["kind"] == "transformer"]
    return trafos[-1] if trafos else "source"


def _bus_v(result: dict, bus_id: str) -> float:
    return next(b["vm_pu"] for b in result["buses"] if b["id"] == bus_id)


def _candidate(cid: str, category: str, conflict_key: str, title: str, interventions: list[dict], *,
               targets: list[str], sizing: dict, capex: float, opex: float = 0.0, rationale: str = "",
               notes: list[str] | None = None) -> dict:
    return {"id": cid, "category": category, "conflict_key": conflict_key, "title": title,
            "interventions": interventions, "targets": targets, "sizing": sizing, "capex_usd": round(capex, 0),
            "opex_usd_per_year": round(opex, 0), "rationale": rationale, "notes": notes or []}


def voltage_zones(ctx: RemediationContext, violations: list[dict]) -> dict[str, list[dict]]:
    zones: dict[str, list[dict]] = {}
    for v in violations:
        if v["type"] == "undervoltage":
            zones.setdefault(_zone_of(ctx.equipment, v["equipment_id"]), []).append(v)
    return zones


def _historical_sensitivity(ctx: RemediationContext, bus_id: str) -> float | None:
    for hint in ctx.memory_hints:
        sens = hint.get("parameters", {}).get("dv_dq_pu_per_mvar")
        if hint.get("intervention_type") == "capacitor" and hint.get("parameters", {}).get("bus") == bus_id and sens:
            return float(sens)
    return None


def capacitor_for_voltage(ctx: RemediationContext, base: dict, zone: str, zone_violations: list[dict]) -> dict | None:
    worst = min(zone_violations, key=lambda v: v["value"])
    bus_id = worst["equipment_id"]
    vn = ctx.by_id()[bus_id]["params"]["vn_kv"]
    lv = vn < 1.0
    step = LV_CAPACITOR_STEP_MVAR if lv else STANDARD_CAPACITOR_STEP_MVAR
    q_max = MAX_CAP_LV_MVAR if lv else MAX_CAP_MV_MVAR
    v_base = _bus_v(base["result"], bus_id)
    sens = _historical_sensitivity(ctx, bus_id)
    sens_source = "historical lesson (agent memory)" if sens else "trial injection"
    if not sens:
        trial = 0.1 if lv else 1.0
        trial_out = ctx.peak([{"type": "capacitor", "bus": bus_id, "q_mvar": trial, "id": f"CAP-TRIAL-{bus_id}"}])
        if not trial_out["result"].get("converged"):
            return None
        sens = (_bus_v(trial_out["result"], bus_id) - v_base) / trial
    if sens <= 0:
        return None
    target = ctx.th["voltage"]["lower_pu"] + VOLTAGE_TARGET_MARGIN_PU
    q_required = (target - v_base) / sens
    q = min(q_max, max(step, math.ceil(q_required / step - 1e-9) * step))
    history = []
    for _ in range(CAP_REFINEMENT_STEPS):
        out = ctx.peak([{"type": "capacitor", "bus": bus_id, "q_mvar": round(q, 3)}])
        v_new = _bus_v(out["result"], bus_id) if out["result"].get("converged") else None
        history.append({"q_mvar": round(q, 3), "vm_pu": v_new})
        if v_new is None or v_new >= ctx.th["voltage"]["lower_pu"] or q >= q_max:
            break
        q = min(q_max, q + step)
    cost = COST_ASSUMPTIONS["capacitor_fixed_usd"] + COST_ASSUMPTIONS["capacitor_per_kvar_usd"] * q * 1000
    return _candidate(
        f"cap-{bus_id}-{q:.2f}", "capacitor", f"capacitor:{bus_id}",
        f"Install {q * 1000:.0f} kvar capacitor bank at {bus_id}",
        [{"type": "capacitor", "bus": bus_id, "q_mvar": round(q, 3), "id": f"CAP-NEW-{bus_id}"}],
        targets=[violation_key(v) for v in zone_violations],
        sizing={"method": "dV/dQ sensitivity at the worst bus, rounded up to standard steps, verified by AC power flow",
                "bus": bus_id, "zone": zone, "v_before_pu": v_base, "target_pu": target,
                "dv_dq_pu_per_mvar": round(sens, 6), "sensitivity_source": sens_source,
                "q_required_mvar": round(q_required, 4), "step_mvar": step, "q_selected_mvar": round(q, 3),
                "refinement": history},
        capex=cost, rationale="Local reactive support reduces the X*Q component of the feeder voltage drop.",
        notes=["Fixed bank: overvoltage risk at light load is checked in verification.",
               "Harmonic resonance not assessed (requires harmonic study)."])


def capacitor_for_overload(ctx: RemediationContext, base: dict, violation: dict) -> dict | None:
    br = next(b for b in base["result"]["branches"] if b["id"] == violation["equipment_id"])
    if br["kind"] != "transformer":
        return None
    p, q = abs(br["p_to_mw"]), abs(br["q_to_mvar"])
    s_target = LOADING_TARGET_FRACTION * ctx.th["loading"]["transformer"]["limit"] / 100 * br["sn_mva"]
    if p >= s_target or q <= 0:
        return None
    q_reduce = q - math.sqrt(s_target ** 2 - p ** 2)
    bus_id = br["to"]
    lv = ctx.by_id()[bus_id]["params"]["vn_kv"] < 1.0
    step = LV_CAPACITOR_STEP_MVAR if lv else STANDARD_CAPACITOR_STEP_MVAR
    q_sel = min(MAX_CAP_LV_MVAR if lv else MAX_CAP_MV_MVAR, math.ceil(q_reduce / step - 1e-9) * step)
    if q_sel <= 0:
        return None
    cost = COST_ASSUMPTIONS["capacitor_fixed_usd"] + COST_ASSUMPTIONS["capacitor_per_kvar_usd"] * q_sel * 1000
    return _candidate(
        f"cap-pf-{bus_id}-{q_sel:.2f}", "capacitor", f"capacitor:{bus_id}",
        f"Power-factor correction: {q_sel * 1000:.0f} kvar at {bus_id} to unload {br['id']}",
        [{"type": "capacitor", "bus": bus_id, "q_mvar": round(q_sel, 3), "id": f"CAP-NEW-{bus_id}"}],
        targets=[violation_key(violation)],
        sizing={"method": "S_target = 0.97 x rating; Q_reduce = Q - sqrt(S_target^2 - P^2)", "p_mw": p, "q_mvar": q,
                "s_target_mva": round(s_target, 4), "q_reduce_mvar": round(q_reduce, 4), "q_selected_mvar": q_sel},
        capex=cost, rationale="Reducing reactive flow lowers apparent power through the overloaded transformer.")


def tap_candidates(ctx: RemediationContext, base: dict, violations: list[dict]) -> list[dict]:
    by_id = ctx.by_id()
    voltage_viol = [v for v in violations if v["type"] in ("undervoltage", "overvoltage")]
    trafos: dict[str, list[dict]] = {}
    for v in voltage_viol:
        for seg in path_from_source(ctx.equipment, v["equipment_id"]):
            eq = by_id[seg["eq_id"]]
            if eq["type"] == "transformer" and eq["params"].get("tap_step_percent"):
                trafos.setdefault(seg["eq_id"], []).append(v)
    out = []
    for t_id, targets in trafos.items():
        p = by_id[t_id]["params"]
        current = int(p.get("tap_pos", 0))
        target_buses = {v["equipment_id"] for v in targets}
        best = None
        sweep = []
        for pos in range(int(p["tap_min"]), int(p["tap_max"]) + 1):
            res = ctx.peak([{"type": "tap", "transformer": t_id, "tap_pos": pos}])
            if not res["result"].get("converged"):
                continue
            hard = hard_violations(res["violations"])
            in_zone = [v for v in hard if v["equipment_id"] in target_buses]
            new_ov = [v for v in hard if v["type"] == "overvoltage"]
            vmin = min(_bus_v(res["result"], b) for b in target_buses)
            score = (len(in_zone) + 2 * len(new_ov), abs(pos - current))
            sweep.append({"tap_pos": pos, "zone_violations": len(in_zone), "overvoltages": len(new_ov),
                          "zone_min_vm_pu": vmin})
            if best is None or score < best[0]:
                best = (score, pos, vmin)
        if best is None or best[1] == current:
            continue
        pos = best[1]
        oltc = bool(p.get("on_load_tap_changer"))
        out.append(_candidate(
            f"tap-{t_id}-{pos}", "tap", f"tap:{t_id}",
            f"{'Adjust OLTC' if oltc else 'Change off-circuit tap of'} {t_id}: tap {current} -> {pos} "
            f"({-(pos - current) * p['tap_step_percent']:+.1f} % LV voltage)",
            [{"type": "tap", "transformer": t_id, "tap_pos": pos}], targets=[violation_key(v) for v in targets],
            sizing={"method": "full tap sweep with AC power flow at each position", "tap_range": [p["tap_min"], p["tap_max"]],
                    "step_percent": p["tap_step_percent"], "selected": pos, "current": current, "sweep": sweep},
            capex=COST_ASSUMPTIONS["tap_change_usd"],
            rationale="Raising the transformer secondary voltage lifts every downstream bus.",
            notes=[] if oltc else ["Off-circuit tap changer: change requires a planned outage."]))
    return out


def regulator_candidates(ctx: RemediationContext, base: dict, zones: dict[str, list[dict]]) -> list[dict]:
    out = []
    lower = ctx.th["voltage"]["lower_pu"]
    for zone, viol in zones.items():
        mv = [v for v in viol if ctx.by_id()[v["equipment_id"]]["params"]["vn_kv"] >= 1.0]
        if len(mv) < 3:
            continue
        worst = min(mv, key=lambda v: v["value"])
        path = path_from_source(ctx.equipment, worst["equipment_id"])
        site = next((s for s in path if s["kind"] == "line" and _bus_v(base["result"], s["to"]) < lower + 0.01), None)
        if not site:
            continue
        targets = {v["equipment_id"] for v in mv}
        best = None
        sweep = []
        for pos in range(0, 17):
            res = ctx.peak([{"type": "regulator", "line": site["eq_id"], "tap_pos": pos}])
            if not res["result"].get("converged"):
                continue
            hard = [v for v in hard_violations(res["violations"]) if v["equipment_id"] in targets or v["type"] == "overvoltage"]
            sweep.append({"tap_pos": pos, "remaining": len(hard)})
            if best is None or (len(hard), pos) < best[0]:
                best = ((len(hard), pos), pos)
        if best is None or best[1] == 0:
            continue
        pos = best[1]
        out.append(_candidate(
            f"reg-{site['eq_id']}-{pos}", "regulator", f"regulator:{zone}",
            f"Install 32-step voltage regulator at the head of {site['eq_id']} (boost {pos * 0.625:.2f} %)",
            [{"type": "regulator", "line": site["eq_id"], "tap_pos": pos, "id": f"REG-NEW-{site['eq_id']}"}],
            targets=[violation_key(v) for v in mv],
            sizing={"method": "site = first line feeding a bus below the limit; tap sweep 0..+16 (0.625 %/step)",
                    "site_line": site["eq_id"], "selected_tap": pos, "boost_percent": pos * 0.625, "sweep": sweep},
            capex=COST_ASSUMPTIONS["regulator_usd"], rationale="In-line regulation restores downstream voltage."))
    return out


def line_upgrade_candidates(ctx: RemediationContext, base: dict, violations: list[dict],
                            zones: dict[str, list[dict]]) -> list[dict]:
    by_id = ctx.by_id()
    out = []
    for v in violations:
        if v["type"] == "line_overload":
            length = by_id[v["equipment_id"]]["params"]["length_km"]
            out.append(_candidate(
                f"line-{v['equipment_id']}", "line_upgrade", f"line:{v['equipment_id']}",
                f"Reinforce {v['equipment_id']} with {UPGRADE_CABLE['name']} ({UPGRADE_CABLE['max_i_ka'] * 1000:.0f} A)",
                [{"type": "line_upgrade", "line": v["equipment_id"]}], targets=[violation_key(v)],
                sizing={"method": "replace conductor with higher-ampacity standard type", "length_km": length,
                        "old_rating_ka": v["i_rated_ka"], "new_rating_ka": UPGRADE_CABLE["max_i_ka"]},
                capex=length * COST_ASSUMPTIONS["line_upgrade_per_km_usd"]))
    for zone, viol in zones.items():
        worst = min(viol, key=lambda x: x["value"])
        branches = {b["id"]: b for b in base["result"]["branches"]}
        lines = [s for s in path_from_source(ctx.equipment, worst["equipment_id"]) if s["kind"] == "line"]
        lines = sorted(lines, key=lambda s: -(branches[s["eq_id"]]["vdrop_percent"] or 0))[:2]
        lines = [s for s in lines if by_id[s["eq_id"]]["params"]["max_i_ka"] < UPGRADE_CABLE["max_i_ka"]]
        if not lines:
            continue
        length = sum(by_id[s["eq_id"]]["params"]["length_km"] for s in lines)
        ids = [s["eq_id"] for s in lines]
        out.append(_candidate(
            f"reinforce-{'-'.join(ids)}", "line_upgrade", f"line:{','.join(ids)}",
            f"Reinforce feeder segments {', '.join(ids)} (largest voltage-drop contributors)",
            [{"type": "line_upgrade", "line": i} for i in ids], targets=[violation_key(x) for x in viol],
            sizing={"method": "upgrade the two segments with the largest voltage drop on the worst bus path",
                    "segments": ids, "length_km": round(length, 3), "conductor": UPGRADE_CABLE["name"]},
            capex=length * COST_ASSUMPTIONS["line_upgrade_per_km_usd"],
            rationale="Lower feeder impedance reduces both R*P and X*Q voltage-drop terms."))
    return out


def transformer_upgrade_candidates(ctx: RemediationContext, base: dict, violations: list[dict]) -> list[dict]:
    out = []
    for v in violations:
        if v["type"] != "transformer_overload":
            continue
        br = next(b for b in base["result"]["branches"] if b["id"] == v["equipment_id"])
        required = br["s_mva"] / (LOADING_TARGET_FRACTION * ctx.th["loading"]["transformer"]["limit"] / 100)
        sizes = LV_TRANSFORMER_SIZES_MVA if ctx.by_id()[br["to"]]["params"]["vn_kv"] < 1.0 else STANDARD_TRANSFORMER_SIZES_MVA
        size = next((s for s in sizes if s >= required), None)
        if size is None:
            continue
        out.append(_candidate(
            f"xfmr-{v['equipment_id']}-{size}", "transformer_upgrade", f"xfmr:{v['equipment_id']}",
            f"Replace {v['equipment_id']} ({br['sn_mva']} MVA) with {size} MVA unit",
            [{"type": "transformer_upgrade", "transformer": v["equipment_id"], "sn_mva": size}],
            targets=[violation_key(v)],
            sizing={"method": "required = S / (0.97 x limit); next standard size", "s_mva": br["s_mva"],
                    "required_mva": round(required, 3), "selected_mva": size},
            capex=size * COST_ASSUMPTIONS["transformer_upgrade_usd_per_mva"],
            rationale="Adds thermal capacity; also reduces transformer voltage drop."))
    return out


def reconfiguration_candidates(ctx: RemediationContext, base: dict, violations: list[dict]) -> list[dict]:
    if not hard_violations(violations):
        return []
    out = []
    for eq in ctx.equipment:
        if eq["type"] == "switch" and not eq["params"]["closed"] and eq["params"]["element"] in open_branches(ctx.equipment):
            out.append(_candidate(
                f"close-{eq['eq_id']}", "reconfiguration", f"switch:{eq['eq_id']}",
                f"Close normally-open tie {eq['eq_id']} ({eq['params']['element']}) to transfer load",
                [{"type": "switch", "switch": eq["eq_id"], "closed": True}],
                targets=[violation_key(v) for v in violations],
                sizing={"method": "switching study: AC power flow with the tie closed", "tie_line": eq["params"]["element"]},
                capex=COST_ASSUMPTIONS["switching_usd"],
                rationale="Shares load between feeders through an existing tie.",
                notes=["Closed-loop operation must be confirmed against protection settings."]))
    return out


def dc_flex_candidates(ctx: RemediationContext, dc_analysis: dict | None, violations: list[dict]) -> list[dict]:
    if not dc_analysis:
        return []
    out = []
    flex = dc_analysis.get("flexibility", {})
    for item in flex.get("flexible_loads", []):
        max_red = item["max_reduction_mw"]
        if max_red <= 0:
            continue
        for frac in (0.5, 1.0):
            delta = round(max_red * frac, 3)
            shifted_mwh = delta * PEAK_DURATION_H
            out.append(_candidate(
                f"dcflex-{item['id']}-{delta}", "dc_flex", f"dcflex:{item['id']}",
                f"Shift {delta:.2f} MW of {item['name']} out of the evening peak",
                [{"type": "load_adjust", "load": item["id"], "delta_mw": -delta}],
                targets=[violation_key(v) for v in violations],
                sizing={"method": "flexible workload deferral within daily deadline (LP-checked by Data Center Agent)",
                        "reduction_mw": delta, "max_reduction_mw": max_red, "shifted_mwh_per_day": round(shifted_mwh, 3),
                        "schedule_feasible": flex.get("schedule_feasible")},
                capex=0.0, opex=shifted_mwh * 365 * COST_ASSUMPTIONS["dc_shift_usd_per_mwh"],
                rationale="Coordinated scheduling of deferrable AI training reduces peak demand.",
                notes=["Requires operator agreement with the data-center workload scheduler (SLA-respecting)."]))
    for bess in flex.get("storage", []):
        usable = max(0.0, bess["max_e_mwh"] * (bess["soc_percent"] / 100 - BESS_MIN_SOC))
        p = round(min(bess["max_p_mw"], usable / PEAK_DURATION_H), 3)
        if p > 0:
            out.append(_candidate(
                f"bess-{bess['id']}-{p}", "storage", f"storage:{bess['id']}",
                f"Discharge {bess['id']} at {p:.2f} MW during the {PEAK_DURATION_H:.0f} h evening peak",
                [{"type": "storage_dispatch", "storage": bess["id"], "p_mw": -p}],
                targets=[violation_key(v) for v in violations],
                sizing={"method": "usable energy / peak duration", "usable_mwh": round(usable, 3), "p_mw": p},
                capex=0.0, rationale="Peak shaving with existing on-site storage."))
    return out


def generate_candidates(ctx: RemediationContext, base: dict, violations: list[dict], dc_analysis: dict | None) -> list[dict]:
    hard = hard_violations(violations)
    zones = voltage_zones(ctx, hard)
    out: list[dict] = []
    for zone, zv in zones.items():
        cand = capacitor_for_voltage(ctx, base, zone, zv)
        if cand:
            out.append(cand)
    for v in hard:
        if v["type"] == "transformer_overload":
            cand = capacitor_for_overload(ctx, base, v)
            if cand and all(c["id"] != cand["id"] for c in out):
                out.append(cand)
    out += tap_candidates(ctx, base, hard)
    out += regulator_candidates(ctx, base, zones)
    out += line_upgrade_candidates(ctx, base, hard, zones)
    out += transformer_upgrade_candidates(ctx, base, hard)
    out += reconfiguration_candidates(ctx, base, hard)
    out += dc_flex_candidates(ctx, dc_analysis, hard)
    out = list({c["id"]: c for c in out}.values())
    preferred = {h["intervention_type"] for h in ctx.memory_hints if h.get("verified")}
    for cand in out:
        cand["historically_verified"] = cand["category"] in preferred
    return out
