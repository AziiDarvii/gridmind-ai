"""High-level simulation entry points used by agents and the API."""
from __future__ import annotations

from app.engine.network import build_network
from app.engine.powerflow import run_powerflow
from app.engine.violations import detect_violations, merged_thresholds


def simulate(equipment: list[dict], interventions: list[dict] | None = None, conditions: dict | None = None,
             thresholds: dict | None = None, condition: str = "peak") -> dict:
    """Build the model from persisted equipment, solve, and check every limit."""
    built = build_network(equipment, interventions or [], conditions or {})
    result = run_powerflow(built)
    violations, status = detect_violations(result, built.meta, thresholds, condition)
    result["proposed_equipment"] = [{"eq_id": e["eq_id"], "type": e["type"], "params": e["params"]}
                                    for e in built.proposed]
    return {"result": result, "violations": violations, "status": status}


def _light_load_interventions(equipment: list[dict], interventions: list[dict]) -> list[dict]:
    """Automatic devices (OLTC with AVR, line regulators) re-regulate at light load.

    Their peak tap setting is therefore not held at light load; they return to
    neutral, which is conservative for overvoltage. Fixed devices (capacitors,
    off-circuit taps, reconfiguration) keep their proposed state.
    """
    oltc = {e["eq_id"] for e in equipment if e["type"] == "transformer" and e["params"].get("on_load_tap_changer")}
    out = []
    for item in interventions:
        if item["type"] == "tap" and item["transformer"] in oltc:
            continue
        if item["type"] == "regulator":
            item = {**item, "tap_pos": 0}
        out.append(item)
    return out


def simulate_conditions(equipment: list[dict], interventions: list[dict] | None = None,
                        conditions: dict | None = None, thresholds: dict | None = None) -> dict:
    """Peak-condition run plus a light-load run.

    The light-load run catches overvoltage introduced by fixed capacitors or
    raised taps, which a peak-only check would miss.
    """
    th = merged_thresholds(thresholds)
    base = dict(conditions or {})
    peak = simulate(equipment, interventions, base, th, "peak")
    light_conditions = {**base, "global_scale": base.get("global_scale", 1.0) * th["verification"]["light_load_factor"]}
    light = simulate(equipment, _light_load_interventions(equipment, interventions or []), light_conditions, th,
                     "light_load")
    return {"peak": peak, "light": light, "violations": peak["violations"] + light["violations"]}
