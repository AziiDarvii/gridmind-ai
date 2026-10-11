"""Before/after comparison built strictly from two persisted simulation results."""
from __future__ import annotations

from app.engine.violations import classify_loading, classify_voltage, merged_thresholds, violation_key


def _delta(a: float | None, b: float | None, digits: int = 5) -> float | None:
    if a is None or b is None:
        return None
    return round(b - a, digits)


def compare_results(before: dict, after: dict, before_violations: list[dict], after_violations: list[dict],
                    thresholds: dict | None = None) -> dict:
    th = merged_thresholds(thresholds)
    if not (before.get("converged") and after.get("converged")):
        return {"comparable": False, "reason": "one of the simulations did not converge",
                "before_converged": before.get("converged"), "after_converged": after.get("converged")}
    b_buses = {b["id"]: b for b in before["buses"]}
    rows_bus = []
    for bus in after["buses"]:
        old = b_buses.get(bus["id"])
        status, _ = classify_voltage(bus["vm_pu"], th)
        old_status = classify_voltage(old["vm_pu"], th)[0] if old else "new"
        rows_bus.append({"id": bus["id"], "vn_kv": bus["vn_kv"], "before_pu": old["vm_pu"] if old else None,
                         "after_pu": bus["vm_pu"], "change_pu": _delta(old["vm_pu"] if old else None, bus["vm_pu"]),
                         "before_kv": old["v_kv"] if old else None, "after_kv": bus["v_kv"],
                         "limit": f"{th['voltage']['lower_pu']}-{th['voltage']['upper_pu']} pu",
                         "before_status": old_status, "after_status": status, "added": old is None})
    b_br = {b["id"]: b for b in before["branches"]}
    rows_br = []
    for br in after["branches"]:
        old = b_br.get(br["id"])
        kind = "line" if br["kind"] == "line" else "transformer"
        band = th["loading"][kind]
        status = classify_loading(br["loading_percent"] or 0, band)[0] if br["in_service"] else "unknown"
        old_status = (classify_loading(old["loading_percent"] or 0, band)[0] if old["in_service"] else "unknown") \
            if old else "new"
        rows_br.append({"id": br["id"], "kind": br["kind"], "from": br["from"], "to": br["to"],
                        "before_loading": old["loading_percent"] if old else None, "after_loading": br["loading_percent"],
                        "change_loading": _delta(old["loading_percent"] if old else None, br["loading_percent"], 3),
                        "before_i_ka": old["i_ka"] if old else None, "after_i_ka": br["i_ka"],
                        "before_rating_ka": old["i_rated_ka"] if old else None, "after_rating_ka": br["i_rated_ka"],
                        "before_losses_mw": old["pl_mw"] if old else None, "after_losses_mw": br["pl_mw"],
                        "before_p_mw": old["p_from_mw"] if old else None, "after_p_mw": br["p_from_mw"],
                        "before_q_mvar": old["q_from_mvar"] if old else None, "after_q_mvar": br["q_from_mvar"],
                        "limit": f"{band['limit']} %", "before_status": old_status, "after_status": status,
                        "added": old is None, "rating_changed": bool(old and old["i_rated_ka"] != br["i_rated_ka"])})
    totals = {}
    for key in ("load_p_mw", "load_q_mvar", "losses_p_mw", "losses_q_mvar", "losses_percent", "grid_p_mw",
                "grid_q_mvar", "grid_pf", "datacenter_p_mw", "min_vm_pu", "max_vm_pu", "max_loading_percent"):
        a, b = before["totals"].get(key), after["totals"].get(key)
        totals[key] = {"before": a, "after": b, "change": _delta(a, b)}
    before_keys = {violation_key(v): v for v in before_violations}
    after_keys = {violation_key(v): v for v in after_violations}
    resolved = [{**v, "status": "resolved", "after_value": _after_value(after, v)}
                for k, v in before_keys.items() if k not in after_keys]
    remaining = [{**after_keys[k], "status": "remaining", "before_value": v["value"]}
                 for k, v in before_keys.items() if k in after_keys]
    new = [{**v, "status": "new"} for k, v in after_keys.items() if k not in before_keys]
    return {"comparable": True, "buses": rows_bus, "branches": rows_br, "totals": totals,
            "violations": {"resolved": resolved, "remaining": remaining, "new": new,
                           "before_count": len(before_violations), "after_count": len(after_violations)}}


def _after_value(after: dict, v: dict) -> float | None:
    if v.get("condition", "peak") != "peak":
        return None  # 'after' is the peak result; other operating points are reported separately
    if v["equipment_type"] == "bus":
        return next((b["vm_pu"] for b in after["buses"] if b["id"] == v["equipment_id"]), None)
    return next((b["loading_percent"] for b in after["branches"] if b["id"] == v["equipment_id"]), None)
