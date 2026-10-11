"""Build a pandapower network from persisted equipment documents.

The circuit model in MongoDB is the single source of truth. Every simulation
re-creates the electrical network from equipment documents, then applies the
scenario's operating conditions and proposed interventions on the copy.
"""
from __future__ import annotations

import copy
import logging
import warnings
from dataclasses import dataclass, field

import pandapower as pp

from app.config import UPGRADE_CABLE

warnings.filterwarnings("ignore", module="pandapower")
logging.getLogger("pandapower").setLevel(logging.ERROR)

REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "bus": ("vn_kv",),
    "ext_grid": ("bus", "vm_pu"),
    "line": ("from_bus", "to_bus", "length_km", "r_ohm_per_km", "x_ohm_per_km", "max_i_ka"),
    "transformer": ("hv_bus", "lv_bus", "sn_mva", "vn_hv_kv", "vn_lv_kv", "vk_percent", "vkr_percent"),
    "load": ("bus", "p_mw", "q_mvar"),
    "switch": ("bus", "element", "et", "closed"),
    "capacitor": ("bus", "q_mvar_rated"),
    "storage": ("bus", "p_mw", "max_e_mwh"),
    "sgen": ("bus", "p_mw"),
    "generator": ("bus", "p_mw", "vm_pu"),
}

REGULATOR_STEPS = 16          # 32-step regulator: +/-16 steps
REGULATOR_STEP_PERCENT = 0.625  # +/-10 % range


class ModelValidationError(ValueError):
    def __init__(self, problems: list[dict]):
        super().__init__(f"{len(problems)} model validation problem(s)")
        self.problems = problems


@dataclass
class BuiltNetwork:
    net: pp.pandapowerNet
    index: dict[str, dict[str, int]] = field(default_factory=dict)  # table -> eq_id -> pp index
    meta: dict[str, dict] = field(default_factory=dict)             # eq_id -> equipment doc (effective)
    proposed: list[dict] = field(default_factory=list)              # equipment added by interventions
    applied: list[dict] = field(default_factory=list)               # interventions with effective values

    def idx(self, table: str, eq_id: str) -> int:
        try:
            return self.index[table][eq_id]
        except KeyError as exc:
            raise KeyError(f"Unknown {table} '{eq_id}'") from exc

    def eq_id(self, table: str, pp_index: int) -> str | None:
        for key, value in self.index.get(table, {}).items():
            if value == pp_index:
                return key
        return None


def validate_equipment(equipment: list[dict]) -> list[dict]:
    """Return a list of missing/inconsistent parameter problems (empty = valid)."""
    problems: list[dict] = []
    ids = {e["eq_id"] for e in equipment}
    buses = {e["eq_id"] for e in equipment if e["type"] == "bus"}
    for eq in equipment:
        params = eq.get("params", {})
        for key in REQUIRED_PARAMS.get(eq["type"], ()):
            if params.get(key) is None:
                problems.append({"equipment_id": eq["eq_id"], "field": key, "problem": "missing required parameter"})
        for ref in ("bus", "from_bus", "to_bus", "hv_bus", "lv_bus"):
            if ref in params and params[ref] not in buses:
                problems.append({"equipment_id": eq["eq_id"], "field": ref, "problem": f"references unknown bus {params[ref]}"})
        if eq["type"] == "switch" and params.get("element") not in ids:
            problems.append({"equipment_id": eq["eq_id"], "field": "element", "problem": "references unknown element"})
        for key in ("length_km", "max_i_ka", "sn_mva", "vn_kv"):
            value = params.get(key)
            if value is not None and value <= 0:
                problems.append({"equipment_id": eq["eq_id"], "field": key, "problem": "must be positive"})
    if not any(e["type"] == "ext_grid" for e in equipment):
        problems.append({"equipment_id": "-", "field": "ext_grid", "problem": "no supply (external grid) modelled"})
    return problems


def _load_factor(params: dict, conditions: dict) -> float:
    factor = float(conditions.get("global_scale", 1.0))
    factor *= float(conditions.get("category_scale", {}).get(params.get("category", ""), 1.0))
    return factor


def build_network(equipment: list[dict], interventions: list[dict] | None = None,
                  conditions: dict | None = None) -> BuiltNetwork:
    """Create the pandapower model, apply operating conditions and interventions."""
    problems = validate_equipment(equipment)
    if problems:
        raise ModelValidationError(problems)
    conditions = conditions or {}
    eqs = [copy.deepcopy(e) for e in equipment]
    _apply_parameter_interventions(eqs, interventions or [])

    net = pp.create_empty_network(name="gridmind")
    built = BuiltNetwork(net=net, index={t: {} for t in ("bus", "line", "trafo", "load", "switch", "shunt",
                                                         "storage", "sgen", "gen", "ext_grid")})
    by_type: dict[str, list[dict]] = {}
    for eq in eqs:
        by_type.setdefault(eq["type"], []).append(eq)
        built.meta[eq["eq_id"]] = eq

    for eq in by_type.get("bus", []):
        p = eq["params"]
        built.index["bus"][eq["eq_id"]] = pp.create_bus(net, vn_kv=p["vn_kv"], name=eq["eq_id"],
                                                        in_service=p.get("in_service", True))
    bus = built.index["bus"]
    for eq in by_type.get("ext_grid", []):
        p = eq["params"]
        built.index["ext_grid"][eq["eq_id"]] = pp.create_ext_grid(
            net, bus[p["bus"]], vm_pu=p["vm_pu"], va_degree=p.get("va_degree", 0.0), name=eq["eq_id"],
            s_sc_max_mva=p.get("s_sc_max_mva", 5000), rx_max=p.get("rx_max", 0.1))
    for eq in by_type.get("line", []):
        p = eq["params"]
        built.index["line"][eq["eq_id"]] = pp.create_line_from_parameters(
            net, bus[p["from_bus"]], bus[p["to_bus"]], length_km=p["length_km"], r_ohm_per_km=p["r_ohm_per_km"],
            x_ohm_per_km=p["x_ohm_per_km"], c_nf_per_km=p.get("c_nf_per_km", 0.0), max_i_ka=p["max_i_ka"],
            name=eq["eq_id"], in_service=p.get("in_service", True))
    for eq in by_type.get("transformer", []):
        p = eq["params"]
        has_tap = p.get("tap_step_percent") is not None
        built.index["trafo"][eq["eq_id"]] = pp.create_transformer_from_parameters(
            net, bus[p["hv_bus"]], bus[p["lv_bus"]], sn_mva=p["sn_mva"], vn_hv_kv=p["vn_hv_kv"],
            vn_lv_kv=p["vn_lv_kv"], vkr_percent=p["vkr_percent"], vk_percent=p["vk_percent"],
            pfe_kw=p.get("pfe_kw", 0.0), i0_percent=p.get("i0_percent", 0.0), shift_degree=p.get("shift_degree", 0.0),
            tap_side=p.get("tap_side", "hv") if has_tap else None,
            tap_neutral=p.get("tap_neutral", 0) if has_tap else None,
            tap_min=p.get("tap_min") if has_tap else None, tap_max=p.get("tap_max") if has_tap else None,
            tap_step_percent=p.get("tap_step_percent") if has_tap else None,
            tap_pos=p.get("tap_pos", 0) if has_tap else None, tap_changer_type="Ratio" if has_tap else None,
            name=eq["eq_id"], in_service=p.get("in_service", True))
    for eq in by_type.get("load", []):
        p = eq["params"]
        factor = _load_factor(p, conditions)
        built.index["load"][eq["eq_id"]] = pp.create_load(
            net, bus[p["bus"]], p_mw=p["p_mw"] * factor, q_mvar=p["q_mvar"] * factor, name=eq["eq_id"],
            in_service=p.get("in_service", True))
    for eq in by_type.get("capacitor", []):
        _create_capacitor(built, eq)
    for eq in by_type.get("storage", []):
        p = eq["params"]
        built.index["storage"][eq["eq_id"]] = pp.create_storage(
            net, bus[p["bus"]], p_mw=p["p_mw"], max_e_mwh=p["max_e_mwh"], soc_percent=p.get("soc_percent", 50),
            name=eq["eq_id"], in_service=p.get("in_service", True))
    for eq in by_type.get("sgen", []):
        p = eq["params"]
        factor = float(conditions.get("sgen_scale", 1.0))
        built.index["sgen"][eq["eq_id"]] = pp.create_sgen(
            net, bus[p["bus"]], p_mw=p["p_mw"] * factor, q_mvar=p.get("q_mvar", 0.0), name=eq["eq_id"],
            type=p.get("technology", "PV"), in_service=p.get("in_service", True))
    for eq in by_type.get("generator", []):
        p = eq["params"]
        built.index["gen"][eq["eq_id"]] = pp.create_gen(
            net, bus[p["bus"]], p_mw=p["p_mw"], vm_pu=p["vm_pu"], name=eq["eq_id"], in_service=p.get("in_service", False))
    for eq in by_type.get("switch", []):
        p = eq["params"]
        table = {"l": "line", "t": "trafo", "b": "bus"}[p["et"]]
        built.index["switch"][eq["eq_id"]] = pp.create_switch(
            net, bus[p["bus"]], built.index[table][p["element"]], et=p["et"], closed=p["closed"],
            type=p.get("switch_type", "LBS"), name=eq["eq_id"])

    _apply_topology_interventions(built, interventions or [])
    _apply_overrides(built, conditions, interventions or [])
    return built


def _create_capacitor(built: BuiltNetwork, eq: dict) -> None:
    p = eq["params"]
    steps = int(p.get("steps", 1))
    steps_on = int(p.get("steps_on", steps)) if p.get("in_service", True) else 0
    q_effective = p["q_mvar_rated"] * steps_on / max(steps, 1)
    # pandapower shunt: positive q = absorption, so a capacitor injects with negative q.
    built.index["shunt"][eq["eq_id"]] = pp.create_shunt(
        built.net, built.index["bus"][p["bus"]], q_mvar=-q_effective, p_mw=0.0,
        vn_kv=built.net.bus.vn_kv.at[built.index["bus"][p["bus"]]], name=eq["eq_id"], in_service=steps_on > 0)


def _apply_parameter_interventions(eqs: list[dict], interventions: list[dict]) -> None:
    """Interventions that change equipment parameters (applied before building)."""
    by_id = {e["eq_id"]: e for e in eqs}
    for item in interventions:
        kind = item["type"]
        if kind == "tap":
            by_id[item["transformer"]]["params"]["tap_pos"] = int(item["tap_pos"])
        elif kind == "line_upgrade":
            p = by_id[item["line"]]["params"]
            for key in ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"):
                p[key] = item.get(key, UPGRADE_CABLE[key])
            p["std_type"] = item.get("conductor", UPGRADE_CABLE["name"])
        elif kind == "transformer_upgrade":
            by_id[item["transformer"]]["params"]["sn_mva"] = float(item["sn_mva"])
        elif kind == "switch":
            by_id[item["switch"]]["params"]["closed"] = bool(item["closed"])
        elif kind == "capacitor_switching":
            by_id[item["capacitor"]]["params"]["steps_on"] = int(item["steps_on"])
        elif kind == "load_adjust":
            p = by_id[item["load"]]["params"]
            ratio = p["q_mvar"] / p["p_mw"] if p["p_mw"] else 0.0
            p["p_mw"] = max(0.0, p["p_mw"] + float(item["delta_mw"]))
            p["q_mvar"] = p["p_mw"] * ratio
        elif kind == "storage_dispatch":
            by_id[item["storage"]]["params"]["p_mw"] = float(item["p_mw"])
            by_id[item["storage"]]["params"]["in_service"] = True


def _apply_topology_interventions(built: BuiltNetwork, interventions: list[dict]) -> None:
    """Interventions that add equipment (applied after building)."""
    net = built.net
    for item in interventions:
        kind = item["type"]
        if kind == "capacitor":
            eq_id = item.get("id") or f"CAP-NEW-{item['bus']}"
            eq = {"eq_id": eq_id, "type": "capacitor", "proposed": True,
                  "params": {"bus": item["bus"], "q_mvar_rated": float(item["q_mvar"]),
                             "steps": int(item.get("steps", 1))}}
            _create_capacitor(built, eq)
            built.meta[eq_id] = eq
            built.proposed.append(eq)
        elif kind == "regulator":
            line_id = item["line"]
            li = built.idx("line", line_id)
            from_bus = int(net.line.from_bus.at[li])
            vn = float(net.bus.vn_kv.at[from_bus])
            reg_bus_id = f"B-REG-{line_id}"
            reg_bus = pp.create_bus(net, vn_kv=vn, name=reg_bus_id)
            built.index["bus"][reg_bus_id] = reg_bus
            net.line.at[li, "from_bus"] = reg_bus
            reg_id = item.get("id") or f"REG-NEW-{line_id}"
            built.index["trafo"][reg_id] = pp.create_transformer_from_parameters(
                net, from_bus, reg_bus, sn_mva=float(item.get("sn_mva", 10.0)), vn_hv_kv=vn, vn_lv_kv=vn,
                vkr_percent=0.1, vk_percent=0.5, pfe_kw=0.0, i0_percent=0.0, tap_side="lv", tap_neutral=0,
                tap_min=-REGULATOR_STEPS, tap_max=REGULATOR_STEPS, tap_step_percent=REGULATOR_STEP_PERCENT,
                tap_pos=int(item.get("tap_pos", 0)), tap_changer_type="Ratio", name=reg_id)
            eq = {"eq_id": reg_id, "type": "regulator", "proposed": True,
                  "params": {"line": line_id, "from_bus": built.eq_id("bus", from_bus), "to_bus": reg_bus_id,
                             "tap_pos": int(item.get("tap_pos", 0)), "tap_min": -REGULATOR_STEPS,
                             "tap_max": REGULATOR_STEPS, "tap_step_percent": REGULATOR_STEP_PERCENT,
                             "sn_mva": float(item.get("sn_mva", 10.0))}}
            built.meta[reg_id] = eq
            built.meta[reg_bus_id] = {"eq_id": reg_bus_id, "type": "bus", "proposed": True, "params": {"vn_kv": vn}}
            built.proposed.extend([eq, built.meta[reg_bus_id]])
        built.applied.append(item)


def _apply_overrides(built: BuiltNetwork, conditions: dict, interventions: list[dict]) -> None:
    """Hourly overrides replace study-point values; plan load adjustments still apply on top."""
    deltas: dict[str, float] = {}
    for item in interventions:
        if item["type"] == "load_adjust":
            deltas[item["load"]] = deltas.get(item["load"], 0.0) + float(item["delta_mw"])
    for eq_id, values in (conditions.get("load_overrides") or {}).items():
        li = built.idx("load", eq_id)
        p = float(values.get("p_mw", built.net.load.p_mw.at[li]))
        q = float(values.get("q_mvar", built.net.load.q_mvar.at[li]))
        if eq_id in deltas:
            ratio = q / p if p else 0.0
            p = max(0.0, p + deltas[eq_id])
            q = p * ratio
        built.net.load.at[li, "p_mw"] = p
        built.net.load.at[li, "q_mvar"] = q
