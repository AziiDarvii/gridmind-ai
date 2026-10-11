"""Data-center demand model, 24-hour network time series and training-load scheduling.

Demand components follow the persisted data-center profile. Cooling is a
function of IT load and outdoor temperature (public Open-Meteo data), UPS
losses follow the UPS efficiency, and residential/commercial loads follow
the public SimBench standard profiles. All network effects come from AC
power-flow runs, hour by hour.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import linprog

from app.engine.simulate import simulate
from app.engine.violations import hard_violations, merged_thresholds

HOURS = 24
FD_STEP_MW = 0.3
LOADING_MARGIN_PERCENT = 2.0
VOLTAGE_MARGIN_PU = 0.002
SLACK_PENALTY = 1e4


def _component(profile: dict, kind: str) -> dict | None:
    return next((c for c in profile["components"] if c["kind"] == kind), None)


def dc_hourly(profile: dict, temperature_c: list[float], training_schedule: list[float] | None = None) -> dict:
    """Per-component hourly demand (MW) for the data center."""
    comps: dict[str, list[float]] = {}
    it_total = np.zeros(HOURS)
    for c in profile["components"]:
        if c["kind"] in ("it", "ai_inference", "auxiliary"):
            comps[c["eq_id"]] = [round(c["base_mw"] * m, 4) for m in c["shape"]]
            if c["kind"] != "auxiliary":
                it_total += np.array(comps[c["eq_id"]])
    train = _component(profile, "ai_training")
    if train:
        sched = training_schedule or [train["base_mw"] * m for m in train["shape"]]
        comps[train["eq_id"]] = [round(x, 4) for x in sched]
        it_total += np.array(sched)
    cool = _component(profile, "cooling")
    if cool:
        m = cool["model"]
        ratio = [m["base_ratio"] + m["per_degC"] * max(0.0, t - m["ref_temp_c"]) for t in temperature_c]
        comps[cool["eq_id"]] = [round(float(it_total[h] * ratio[h]), 4) for h in range(HOURS)]
    ups = _component(profile, "ups_losses")
    if ups:
        eff = ups["model"]["efficiency"]
        comps[ups["eq_id"]] = [round(float(it_total[h] * (1 / eff - 1)), 4) for h in range(HOURS)]
    total = [round(sum(v[h] for v in comps.values()), 4) for h in range(HOURS)]
    pue = [round(total[h] / it_total[h], 4) if it_total[h] else None for h in range(HOURS)]
    return {"components": comps, "it_total": [round(float(x), 4) for x in it_total], "total": total, "pue": pue}


def hourly_conditions(equipment: list[dict], base_conditions: dict, profiles: dict, dc: dict, hour: int) -> dict:
    """Operating conditions for one hour: category scales relative to the study-point equipment values."""
    by_id = {e["eq_id"]: e for e in equipment}
    g = float(base_conditions.get("global_scale", 1.0))
    base_cat = base_conditions.get("category_scale", {})
    # Profile shapes are multiplied by the scenario's own scales, so stressed scenarios stay stressed.
    scale = {**base_cat,
             "residential": profiles["residential"][hour] * base_cat.get("residential", 1.0),
             "commercial": profiles["commercial"][hour] * base_cat.get("commercial", 1.0)}
    overrides = {}
    for eq_id, series in dc["components"].items():
        p = by_id[eq_id]["params"]
        ratio = p["q_mvar"] / p["p_mw"] if p["p_mw"] else 0.0
        mw = series[hour] * g * base_cat.get(p.get("category", ""), 1.0)
        overrides[eq_id] = {"p_mw": mw, "q_mvar": mw * ratio}
    pv = profiles.get("pv", [0.0] * HOURS)[hour]
    return {**base_conditions, "global_scale": g, "category_scale": scale, "sgen_scale": pv,
            "load_overrides": overrides}


def network_timeseries(equipment: list[dict], base_conditions: dict, profiles: dict, dc: dict,
                       interventions: list[dict] | None = None, thresholds: dict | None = None,
                       monitored: list[str] | None = None) -> list[dict]:
    rows = []
    for h in range(HOURS):
        conditions = hourly_conditions(equipment, base_conditions, profiles, dc, h)
        hour_interventions, taps = avr_interventions(equipment, interventions or [], conditions, thresholds)
        out = simulate(equipment, hour_interventions, conditions, thresholds, condition=f"hour_{h:02d}")
        res = out["result"]
        if not res.get("converged"):
            rows.append({"hour": h, "converged": False, "dc_mw": dc["total"][h]})
            continue
        branches = {b["id"]: b["loading_percent"] for b in res["branches"]}
        buses = {b["id"]: b["vm_pu"] for b in res["buses"]}
        worst_bus = min(buses, key=buses.get)
        hard = hard_violations(out["violations"])
        rows.append({"hour": h, "converged": True, "dc_mw": dc["total"][h], "min_vm_pu": buses[worst_bus],
                     "worst_bus": worst_bus, "max_loading_percent": res["totals"]["max_loading_percent"],
                     "losses_mw": res["totals"]["losses_p_mw"], "grid_p_mw": res["totals"]["grid_p_mw"],
                     "violations": len(hard), "violation_keys": sorted({f"{v['type']}|{v['equipment_id']}" for v in hard}),
                     "monitored": {k: branches.get(k, buses.get(k)) for k in (monitored or [])}, "buses": buses,
                     "branches": branches, "oltc_taps": taps})
    return rows


def avr_interventions(equipment: list[dict], interventions: list[dict], conditions: dict,
                      thresholds: dict | None) -> tuple[list[dict], dict[str, int]]:
    """Emulate OLTC automatic voltage regulation for one operating hour.

    For each on-load tap changer set by a plan, step from neutral towards the
    planned peak tap and keep the first position with no undervoltage, without
    creating overvoltage. Off-circuit taps and other interventions are fixed.
    """
    oltc = {e["eq_id"] for e in equipment if e["type"] == "transformer" and e["params"].get("on_load_tap_changer")}
    fixed = [i for i in interventions if not (i["type"] == "tap" and i["transformer"] in oltc)]
    chosen: dict[str, int] = {}
    for item in interventions:
        if item["type"] != "tap" or item["transformer"] not in oltc:
            continue
        peak_tap = int(item["tap_pos"])
        direction = -1 if peak_tap < 0 else 1
        selected = peak_tap
        for pos in range(0, peak_tap + direction, direction):
            trial = fixed + [{"type": "tap", "transformer": t, "tap_pos": p} for t, p in chosen.items()] \
                + [{"type": "tap", "transformer": item["transformer"], "tap_pos": pos}]
            out = simulate(equipment, trial, conditions, thresholds)
            if not out["result"].get("converged"):
                continue
            kinds = {v["type"] for v in hard_violations(out["violations"])}
            if "overvoltage" in kinds:
                selected = pos - direction if pos != 0 else 0
                break
            if "undervoltage" not in kinds:
                selected = pos
                break
        chosen[item["transformer"]] = selected
    return fixed + [{"type": "tap", "transformer": t, "tap_pos": p} for t, p in chosen.items()], chosen


def schedule_training(equipment: list[dict], base_conditions: dict, profiles: dict, profile: dict,
                      temperature_c: list[float], prices: list[float], thresholds: dict | None = None,
                      interventions: list[dict] | None = None) -> dict:
    """LP: re-schedule deferrable AI training to minimize energy cost subject to network limits.

    The network constraints are linearized with finite-difference sensitivities
    (loading %/MW and pu/MW) and the LP schedule is then verified hour by hour
    with full AC power flow. Optimality holds for the linearized problem only.
    """
    th = merged_thresholds(thresholds)
    profile, base_conditions = _fold_training_scale(profile, base_conditions)
    train = _component(profile, "ai_training")
    base_dc = dc_hourly(profile, temperature_c)
    x0 = np.array(base_dc["components"][train["eq_id"]])
    energy = float(train.get("daily_energy_mwh", x0.sum()))
    base_ts = network_timeseries(equipment, base_conditions, profiles, base_dc, interventions, thresholds)
    stress_hour = max(range(HOURS), key=lambda h: base_ts[h].get("max_loading_percent") or 0)
    dc_buses = [e["eq_id"] for e in equipment if e["type"] == "bus" and e.get("group") == "datacenter"]
    monitored_branches = [e["eq_id"] for e in equipment if e["type"] in ("line", "transformer")]

    perturbed = dict(base_dc)
    comps = dict(base_dc["components"])
    comps[train["eq_id"]] = list(x0)
    comps[train["eq_id"]][stress_hour] = max(0.0, x0[stress_hour] - FD_STEP_MW)
    perturbed = dc_hourly(profile, temperature_c, comps[train["eq_id"]])
    p_out = simulate(equipment, interventions or [],
                     hourly_conditions(equipment, base_conditions, profiles, perturbed, stress_hour), thresholds)
    p_res = p_out["result"]
    step = x0[stress_hour] - comps[train["eq_id"]][stress_hour]
    sens_loading, sens_v = {}, {}
    if p_res.get("converged") and step > 0:
        pl = {b["id"]: b["loading_percent"] for b in p_res["branches"]}
        pv = {b["id"]: b["vm_pu"] for b in p_res["buses"]}
        base_row = base_ts[stress_hour]
        for e in monitored_branches:
            s = (base_row["branches"][e] - pl[e]) / step
            if s > 0.05:
                sens_loading[e] = s
        for b in dc_buses:
            s = (pv[b] - base_row["buses"][b]) / step
            if s > 0:
                sens_v[b] = s

    limits = {e["eq_id"]: th["loading"]["line" if e["type"] == "line" else "transformer"]["limit"]
              for e in equipment if e["eq_id"] in sens_loading}
    lower = th["voltage"]["lower_pu"] + VOLTAGE_MARGIN_PU
    n_cons = HOURS * (len(sens_loading) + len(sens_v))
    # variables: x_0..x_23, slack_0..slack_{n_cons-1}
    c = np.concatenate([np.array(prices, dtype=float), np.full(n_cons, SLACK_PENALTY)])
    a_ub, b_ub = [], []
    k = 0
    for h in range(HOURS):
        row_base = base_ts[h]
        if not row_base.get("converged"):
            continue
        for e, s in sens_loading.items():
            row = np.zeros(HOURS + n_cons)
            row[h] = s
            row[HOURS + k] = -1.0
            a_ub.append(row)
            cap = max(limits[e] - LOADING_MARGIN_PERCENT, row_base["branches"][e])  # never worse than baseline
            b_ub.append(cap - row_base["branches"][e] + s * x0[h])
            k += 1
        for b, s in sens_v.items():
            row = np.zeros(HOURS + n_cons)
            row[h] = s
            row[HOURS + k] = -1.0
            a_ub.append(row)
            floor = min(lower, row_base["buses"][b])  # never worse than baseline
            b_ub.append(row_base["buses"][b] - floor + s * x0[h])
            k += 1
    a_eq = np.concatenate([np.ones(HOURS), np.zeros(n_cons)])[None, :]
    bounds = [(train["min_mw"], train["max_mw"])] * HOURS + [(0, None)] * n_cons
    lp = linprog(c, A_ub=np.array(a_ub) if a_ub else None, b_ub=np.array(b_ub) if b_ub else None,
                 A_eq=a_eq, b_eq=[energy], bounds=bounds, method="highs")
    if not lp.success:
        return {"success": False, "message": lp.message, "baseline_schedule": x0.round(4).tolist()}
    sched = [round(float(v), 4) for v in lp.x[:HOURS]]
    slack_used = float(lp.x[HOURS:].sum()) if n_cons else 0.0
    new_dc = dc_hourly(profile, temperature_c, sched)
    new_ts = network_timeseries(equipment, base_conditions, profiles, new_dc, interventions, thresholds)
    cost_before = float(np.dot(prices, base_dc["total"]))
    cost_after = float(np.dot(prices, new_dc["total"]))
    return {
        "success": True, "method": "linear program (HiGHS) with linearized network constraints; AC-verified",
        "optimality_claim": "optimal for the linearized LP; AC verification below is authoritative",
        "stress_hour": stress_hour, "sensitivities": {"loading_percent_per_mw": sens_loading, "vm_pu_per_mw": sens_v},
        "baseline_schedule": x0.round(4).tolist(), "optimized_schedule": sched, "daily_energy_mwh": round(energy, 3),
        "shifted_mwh": round(float(np.abs(np.array(sched) - x0).sum() / 2), 3), "slack_used": round(slack_used, 5),
        "dc_peak_before_mw": max(base_dc["total"]), "dc_peak_after_mw": max(new_dc["total"]),
        "energy_cost_before_usd": round(cost_before, 2), "energy_cost_after_usd": round(cost_after, 2),
        "violation_hours_before": sum(1 for r in base_ts if r.get("violations")),
        "violation_hours_after": sum(1 for r in new_ts if r.get("violations")),
        "timeseries_before": _slim(base_ts), "timeseries_after": _slim(new_ts),
        "dc_before": base_dc, "dc_after": new_dc,
    }


def _fold_training_scale(profile: dict, conditions: dict) -> tuple[dict, dict]:
    """Move a scenario's dc_training scale into the profile so the LP schedules real MW/MWh."""
    cat = dict(conditions.get("category_scale", {}))
    factor = float(cat.pop("dc_training", 1.0))
    if factor == 1.0:
        return profile, conditions
    comps = []
    for c in profile["components"]:
        if c["kind"] == "ai_training":
            c = {**c, "base_mw": c["base_mw"] * factor, "min_mw": c["min_mw"] * factor, "max_mw": c["max_mw"] * factor,
                 "daily_energy_mwh": c.get("daily_energy_mwh", c["base_mw"] * HOURS) * factor}
        comps.append(c)
    return {**profile, "components": comps}, {**conditions, "category_scale": cat}


def _slim(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in ("buses", "branches")} for r in rows]


def flexibility_summary(equipment: list[dict], profile: dict) -> dict:
    by_id = {e["eq_id"]: e for e in equipment}
    flex = []
    for c in profile["components"]:
        if c.get("flexible"):
            base = by_id[c["eq_id"]]["params"]["p_mw"]
            flex.append({"id": c["eq_id"], "name": by_id[c["eq_id"]]["name"], "base_mw": base,
                         "min_mw": c["min_mw"], "max_mw": c["max_mw"],
                         "max_reduction_mw": round(max(0.0, base - c["min_mw"]), 4)})
    storage = [{"id": e["eq_id"], "max_p_mw": e["params"].get("max_p_mw", e["params"]["max_e_mwh"] / 2),
                "max_e_mwh": e["params"]["max_e_mwh"], "soc_percent": e["params"].get("soc_percent", 50.0)}
               for e in equipment if e["type"] == "storage"]
    return {"flexible_loads": flex, "storage": storage}
