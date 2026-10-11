"""Evidence-based root-cause analysis for detected violations.

Every cause is backed by numbers computed from the solved network: segment
voltage drops along the supply path, the active/reactive split of the drop
(dV ~ (R*P + X*Q) / V^2), transformer loading and tap headroom, and a
counterfactual solve without the data-center load.
"""
from __future__ import annotations

import math

from app.engine.topology import buses_fed_through, downstream_buses, loads_on, open_branches, path_from_source

LINE_SHARE_THRESHOLD = 0.5
TRAFO_SHARE_THRESHOLD = 0.25
REACTIVE_SHARE_THRESHOLD = 0.35
DC_IMPACT_THRESHOLD_PU = 0.005


def _segment_impedance(eq: dict) -> tuple[float, float, float]:
    """(R ohm, X ohm, reference kV) of a line or transformer (transformer referred to LV)."""
    p = eq["params"]
    if eq["type"] == "line":
        return p["r_ohm_per_km"] * p["length_km"], p["x_ohm_per_km"] * p["length_km"], None
    zbase = p["vn_lv_kv"] ** 2 / p["sn_mva"]
    z = p["vk_percent"] / 100 * zbase
    r = p["vkr_percent"] / 100 * zbase
    return r, math.sqrt(max(z * z - r * r, 0.0)), p["vn_lv_kv"]


def _dc_load_ids(equipment: list[dict]) -> set[str]:
    return {e["eq_id"] for e in equipment if e["type"] == "load" and e.get("group") == "datacenter"}


def no_datacenter_conditions(equipment: list[dict], conditions: dict) -> dict:
    overrides = {eq_id: {"p_mw": 0.0, "q_mvar": 0.0} for eq_id in _dc_load_ids(equipment)}
    return {**conditions, "load_overrides": {**conditions.get("load_overrides", {}), **overrides}}


def _path_evidence(equipment: list[dict], result: dict, bus_id: str) -> dict:
    by_id = {e["eq_id"]: e for e in equipment}
    branches = {b["id"]: b for b in result["branches"]}
    buses = {b["id"]: b for b in result["buses"]}
    segments = []
    for seg in path_from_source(equipment, bus_id):
        br = branches.get(seg["eq_id"])
        eq = by_id[seg["eq_id"]]
        if br is None:
            continue
        r, x, ref_kv = _segment_impedance(eq)
        forward = br["from"] == seg["from"]
        p = br["p_from_mw"] if forward else br["p_to_mw"]
        q = br["q_from_mvar"] if forward else br["q_to_mvar"]
        v_kv = ref_kv or buses[seg["to"]]["vn_kv"]
        drop = (buses[seg["from"]]["vm_pu"] - buses[seg["to"]]["vm_pu"]) * 100
        segments.append({
            "eq_id": seg["eq_id"], "kind": seg["kind"], "from": seg["from"], "to": seg["to"],
            "r_ohm": round(r, 4), "x_ohm": round(x, 4), "p_mw": round(p, 4), "q_mvar": round(q, 4),
            "loading_percent": br["loading_percent"], "vdrop_percent": round(drop, 4),
            "dv_p_percent": round(100 * r * p / v_kv ** 2, 4), "dv_q_percent": round(100 * x * q / v_kv ** 2, 4),
            "tap_pos": br.get("tap_pos"), "tap_min": eq["params"].get("tap_min"), "tap_max": eq["params"].get("tap_max"),
            "on_load_tap_changer": eq["params"].get("on_load_tap_changer")})
    total_drop = sum(s["vdrop_percent"] for s in segments)
    line_drop = sum(s["vdrop_percent"] for s in segments if s["kind"] == "line")
    trafo_drop = total_drop - line_drop
    dv_p = sum(s["dv_p_percent"] for s in segments)
    dv_q = sum(s["dv_q_percent"] for s in segments)
    return {"segments": segments, "total_drop_percent": round(total_drop, 4), "line_drop_percent": round(line_drop, 4),
            "transformer_drop_percent": round(trafo_drop, 4),
            "reactive_share": round(dv_q / (dv_p + dv_q), 4) if (dv_p + dv_q) > 0 else 0.0,
            "top_contributors": sorted(segments, key=lambda s: -s["vdrop_percent"])[:3]}


def analyze_voltage(equipment: list[dict], result: dict, violation: dict, no_dc_result: dict | None) -> dict:
    bus_id = violation["equipment_id"]
    ev = _path_evidence(equipment, result, bus_id)
    fed = buses_fed_through(equipment, bus_id)
    loads = loads_on(equipment, fed)
    dc_ids = _dc_load_ids(equipment)
    load_p = sum(e["params"]["p_mw"] for e in loads)
    causes = []
    total = ev["total_drop_percent"] or 1e-9
    under = violation["type"] == "undervoltage"
    if under:
        if ev["line_drop_percent"] / total >= LINE_SHARE_THRESHOLD:
            line_segments = [s for s in ev["segments"] if s["kind"] == "line"]
            worst = max(line_segments, key=lambda s: s["vdrop_percent"]) if line_segments else None
            causes.append({"code": "feeder_impedance_drop", "title": "Voltage drop along a long, heavily loaded feeder",
                           "score": round(ev["line_drop_percent"] / total, 3),
                           "evidence": f"Line segments account for {ev['line_drop_percent']:.2f} % of the "
                                       f"{total:.2f} % total drop from the source; largest: "
                                       f"line {worst['eq_id']} ({worst['vdrop_percent']:.2f} %)." if worst else ""})
        trafo_segments = [s for s in ev["segments"] if s["kind"] == "transformer"]
        if trafo_segments and ev["transformer_drop_percent"] / total >= TRAFO_SHARE_THRESHOLD:
            t = max(trafo_segments, key=lambda s: s["vdrop_percent"])
            causes.append({"code": "transformer_drop", "title": "High transformer voltage drop under heavy loading",
                           "score": round(ev["transformer_drop_percent"] / total, 3),
                           "evidence": f"{t['eq_id']} drops {t['vdrop_percent']:.2f} % at {t['loading_percent']:.1f} % loading."})
        if ev["reactive_share"] >= REACTIVE_SHARE_THRESHOLD:
            causes.append({"code": "reactive_demand", "title": "Reactive power demand carried over the feeder",
                           "score": ev["reactive_share"],
                           "evidence": f"X*Q terms explain {100 * ev['reactive_share']:.0f} % of the estimated drop "
                                       "(dV ~ (R*P + X*Q)/V^2); local reactive support would reduce it."})
        for s in ev["segments"]:
            if s["tap_pos"] is not None and s["tap_min"] is not None and s["tap_pos"] > s["tap_min"]:
                causes.append({"code": "tap_not_regulating", "title": f"Tap changer {s['eq_id']} not boosting voltage",
                               "score": 0.4, "evidence": f"{s['eq_id']} tap at {s['tap_pos']} (range {s['tap_min']}..{s['tap_max']}); "
                               f"{'on-load' if s['on_load_tap_changer'] else 'off-circuit'} tap headroom is available."})
    else:
        causes.append({"code": "light_load_or_injection", "title": "Light loading or excess reactive injection",
                       "score": 0.5, "evidence": f"Voltage rises to {violation['value']:.4f} pu with "
                                                 f"{load_p:.2f} MW downstream load."})
    no_dc_v = None
    if no_dc_result and no_dc_result.get("converged"):
        no_dc_v = next((b["vm_pu"] for b in no_dc_result["buses"] if b["id"] == bus_id), None)
        if no_dc_v is not None and abs(no_dc_v - violation["value"]) >= DC_IMPACT_THRESHOLD_PU:
            causes.append({"code": "datacenter_demand", "title": "Data-center demand depresses the voltage",
                           "score": round(min(1.0, abs(no_dc_v - violation["value"]) / 0.03), 3),
                           "evidence": f"Without the data-center load the bus would be at {no_dc_v:.4f} pu "
                                       f"(vs {violation['value']:.4f} pu)."})
    causes.sort(key=lambda c: -c["score"])
    return {"violation_key": f"{violation['type']}|{bus_id}", "equipment_id": bus_id, "type": violation["type"],
            "causes": causes, "path": ev, "downstream_load_mw": round(load_p, 4),
            "downstream_dc_mw": round(sum(e["params"]["p_mw"] for e in loads if e["eq_id"] in dc_ids), 4),
            "value_without_dc": no_dc_v}


def analyze_overload(equipment: list[dict], result: dict, violation: dict, no_dc_result: dict | None) -> dict:
    eq_id = violation["equipment_id"]
    br = next(b for b in result["branches"] if b["id"] == eq_id)
    down = downstream_buses(equipment, eq_id)
    loads = loads_on(equipment, down)
    dc_ids = _dc_load_ids(equipment)
    p, q = abs(br["p_from_mw"]), abs(br["q_from_mvar"])
    s = math.hypot(p, q) or 1e-9
    causes = [{"code": "downstream_demand", "title": "Downstream demand exceeds the branch rating",
               "score": round(min(1.0, br["loading_percent"] / 100), 3),
               "evidence": f"{eq_id} carries {p:.3f} MW / {q:.3f} Mvar ({br['loading_percent']:.1f} % of rating, "
                           f"{br['i_ka'] * 1000:.0f} A vs {br['i_rated_ka'] * 1000:.0f} A) feeding "
                           f"{len(loads)} loads on {len(down)} buses."}]
    if q / s > 0.3:
        causes.append({"code": "reactive_flow", "title": "Reactive power flow inflates apparent power",
                       "score": round(q / s, 3),
                       "evidence": f"Q/S = {q / s:.2f}; correcting power factor downstream reduces loading to about "
                                   f"{100 * p / s * br['loading_percent'] / 100:.1f} %."})
    no_dc_loading = None
    if no_dc_result and no_dc_result.get("converged"):
        no_dc_loading = next((b["loading_percent"] for b in no_dc_result["branches"] if b["id"] == eq_id), None)
        if no_dc_loading is not None and br["loading_percent"] - no_dc_loading > 1.0:
            causes.append({"code": "datacenter_demand", "title": "Data-center demand drives the overload",
                           "score": round(min(1.0, (br["loading_percent"] - no_dc_loading) / 20), 3),
                           "evidence": f"Without the data center the loading would be {no_dc_loading:.1f} % "
                                       f"(vs {br['loading_percent']:.1f} %)."})
    ties = [e["params"]["element"] for e in equipment if e["type"] == "switch"
            and e["params"]["element"] in open_branches(equipment)
            and e["params"]["bus"] in down]
    if ties:
        causes.append({"code": "transfer_available", "title": "Normally-open tie available for load transfer",
                       "score": 0.3, "evidence": f"Open tie(s) {sorted(set(ties))} connect to the downstream area."})
    causes.sort(key=lambda c: -c["score"])
    return {"violation_key": f"{violation['type']}|{eq_id}", "equipment_id": eq_id, "type": violation["type"],
            "causes": causes, "downstream_buses": sorted(down), "downstream_load_mw": round(sum(e["params"]["p_mw"] for e in loads), 4),
            "downstream_dc_mw": round(sum(e["params"]["p_mw"] for e in loads if e["eq_id"] in dc_ids), 4),
            "value_without_dc": no_dc_loading, "p_mw": p, "q_mvar": q}


def analyze_all(equipment: list[dict], result: dict, violations: list[dict], no_dc_result: dict | None) -> list[dict]:
    out = []
    for v in violations:
        if v["type"] in ("undervoltage", "overvoltage"):
            out.append(analyze_voltage(equipment, result, v, no_dc_result))
        elif v["type"] in ("line_overload", "transformer_overload"):
            out.append(analyze_overload(equipment, result, v, no_dc_result))
        else:
            out.append({"violation_key": f"{v['type']}|{v['equipment_id']}", "equipment_id": v["equipment_id"],
                        "type": v["type"], "causes": [{"code": v["type"], "title": v["type"].replace("_", " "),
                                                       "score": 0.5, "evidence": v["severity_basis"]}]})
    return out
