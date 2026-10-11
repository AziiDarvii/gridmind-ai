"""Reproducible demo network: CIGRE MV benchmark + a 4 MW data-center campus.

Source network: CIGRE Task Force C6.04.02 European medium-voltage benchmark
(K. Rudion et al., "Design of benchmark of medium voltage distribution
network for investigation of DG integration", IEEE PES GM 2006), as
implemented in ``pandapower.networks.create_cigre_network_mv``. Line,
transformer and load parameters are taken verbatim from that public model.

Additions (clearly flagged ``assumed`` in ``data_quality``):
  * On-load tap changers on the two 110/20 kV transformers (+/-9 x 1.5 %).
  * A data-center campus at Bus 14 with two 20/0.4 kV transformers and
    component loads (IT, AI training, AI inference, cooling, UPS losses,
    auxiliaries), a BESS, rooftop PV and a backup generator.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pandapower.networks as pn

CIRCUIT_ID = "cigre-mv-dc"
CIGRE_SOURCE = "CIGRE TF C6.04.02 MV benchmark (pandapower.networks.create_cigre_network_mv)"
DC_SOURCE = "GridMind demo data-center extension (documented example assumption)"

# Data-center component loads at the evening-peak study point.
# (eq_id, name, bus, P MW, power factor, category, flexible)
DC_LOADS = (
    ("DC-IT", "IT servers (cloud)", "B-DC-A", 1.20, 0.97, "dc_it", False),
    ("DC-TRAIN", "AI training cluster", "B-DC-A", 1.00, 0.97, "dc_training", True),
    ("DC-INFER", "AI inference cluster", "B-DC-B", 0.80, 0.97, "dc_inference", False),
    ("DC-COOL", "Cooling (chillers, CRAH fans)", "B-DC-B", 0.75, 0.86, "dc_cooling", False),
    ("DC-UPS", "UPS conversion losses", "B-DC-B", 0.16, 0.95, "dc_ups", False),
    ("DC-AUX", "Auxiliary / lighting / offices", "B-DC-B", 0.10, 0.93, "dc_aux", False),
)


def _bus_id(net, index: int) -> str:
    return "B" + str(net.bus.name.at[index]).replace("Bus ", "")


def _eq(eq_id: str, eq_type: str, name: str, params: dict, *, source: str = CIGRE_SOURCE,
        assumed: tuple[str, ...] = (), group: str | None = None) -> dict:
    quality = {k: ("assumed" if k in assumed else "input") for k in params}
    return {"_id": f"{CIRCUIT_ID}:{eq_id}", "circuit_id": CIRCUIT_ID, "eq_id": eq_id, "type": eq_type, "name": name,
            "params": params, "data_quality": quality, "source": source, "group": group, "proposed": False}


def _q(p_mw: float, pf: float) -> float:
    return round(p_mw * math.tan(math.acos(pf)), 4)


def build_cigre_dc_circuit() -> tuple[dict, list[dict]]:
    net = pn.create_cigre_network_mv(with_der=False)
    eqs: list[dict] = []
    for i, row in net.bus.iterrows():
        eqs.append(_eq(_bus_id(net, i), "bus", str(row["name"]), {"vn_kv": float(row.vn_kv), "zone": str(row.zone)}))
    for _, row in net.ext_grid.iterrows():
        eqs.append(_eq("GRID", "ext_grid", "110 kV utility supply",
                       {"bus": _bus_id(net, row.bus), "vm_pu": float(row.vm_pu), "va_degree": float(row.va_degree),
                        "s_sc_max_mva": float(row.get("s_sc_max_mva", 5000) or 5000), "rx_max": 0.1}))
    line_ids: dict[int, str] = {}
    for i, row in net.line.iterrows():
        a, b = _bus_id(net, row.from_bus), _bus_id(net, row.to_bus)
        eq_id = f"L{a[1:]}-{b[1:]}"
        line_ids[i] = eq_id
        eqs.append(_eq(eq_id, "line", str(row["name"]), {
            "from_bus": a, "to_bus": b, "length_km": float(row.length_km), "r_ohm_per_km": float(row.r_ohm_per_km),
            "x_ohm_per_km": float(row.x_ohm_per_km), "c_nf_per_km": float(row.c_nf_per_km),
            "max_i_ka": float(row.max_i_ka), "std_type": row.std_type if isinstance(row.std_type, str) else None,
            "in_service": bool(row.in_service)}))
    trafo_ids: dict[int, str] = {}
    for i, row in net.trafo.iterrows():
        a, b = _bus_id(net, row.hv_bus), _bus_id(net, row.lv_bus)
        eq_id = f"T{a[1:]}-{b[1:]}"
        trafo_ids[i] = eq_id
        eqs.append(_eq(eq_id, "transformer", str(row["name"]), {
            "hv_bus": a, "lv_bus": b, "sn_mva": float(row.sn_mva), "vn_hv_kv": float(row.vn_hv_kv),
            "vn_lv_kv": float(row.vn_lv_kv), "vk_percent": float(row.vk_percent), "vkr_percent": float(row.vkr_percent),
            "pfe_kw": float(row.pfe_kw), "i0_percent": float(row.i0_percent), "shift_degree": float(row.shift_degree),
            "tap_side": "hv", "tap_neutral": 0, "tap_min": -9, "tap_max": 9, "tap_step_percent": 1.5, "tap_pos": 0,
            "on_load_tap_changer": True},
            assumed=("tap_side", "tap_neutral", "tap_min", "tap_max", "tap_step_percent", "tap_pos", "on_load_tap_changer")))
    for _, row in net.load.iterrows():
        name = str(row["name"]).replace("Load ", "")
        category = "residential" if name.startswith("R") else "commercial"
        eqs.append(_eq(f"LD-{name}", "load", f"{category.title()} load {name}", {
            "bus": _bus_id(net, row.bus), "p_mw": round(float(row.p_mw), 5), "q_mvar": round(float(row.q_mvar), 5),
            "category": category, "load_model": "constant PQ", "flexible": False}))
    for i, row in net.switch.iterrows():
        et = row.et
        element = line_ids[row.element] if et == "l" else trafo_ids[row.element]
        bus = _bus_id(net, row.bus)
        eq_id = str(row["name"]) if isinstance(row["name"], str) else (f"CB-{element}" if et == "t" else f"SW-{element}-{bus}")
        eqs.append(_eq(eq_id, "switch", "Tie switch " + eq_id if eq_id.startswith("S") else eq_id, {
            "bus": bus, "element": element, "et": et, "closed": bool(row.closed),
            "switch_type": str(row.type)}))
    eqs.extend(_datacenter_equipment())
    circuit = {
        "_id": CIRCUIT_ID,
        "name": "CIGRE MV Benchmark + AI Data Center Campus",
        "description": ("European 20 kV MV benchmark feeder (CIGRE TF C6.04.02) supplied from 110 kV through two "
                        "25 MVA transformers, extended with a ~4 MW AI data-center campus connected at Bus 14."),
        "base_mva": 1.0, "frequency_hz": 50,
        "sources": [{"name": CIGRE_SOURCE, "url": "https://pandapower.readthedocs.io/en/latest/networks/cigre.html"},
                    {"name": DC_SOURCE, "url": None}],
        "thresholds": {}, "created_at": datetime.now(timezone.utc),
    }
    return circuit, eqs


def _datacenter_equipment() -> list[dict]:
    dc = {"source": DC_SOURCE, "group": "datacenter"}
    eqs = [
        _eq("B-DC-MV", "bus", "Data center 20 kV switchboard", {"vn_kv": 20.0, "zone": "DC"}, **dc),
        _eq("B-DC-A", "bus", "DC Hall A 0.4 kV (compute)", {"vn_kv": 0.4, "zone": "DC"}, **dc),
        _eq("B-DC-B", "bus", "DC Hall B 0.4 kV (inference + facility)", {"vn_kv": 0.4, "zone": "DC"}, **dc),
        _eq("L14-DC", "line", "Data center service cable", {
            "from_bus": "B14", "to_bus": "B-DC-MV", "length_km": 0.5, "r_ohm_per_km": 0.122, "x_ohm_per_km": 0.112,
            "c_nf_per_km": 304.0, "max_i_ka": 0.421, "std_type": "NA2XS2Y 1x240 RM/25 12/20 kV", "in_service": True}, **dc),
    ]
    for eq_id, name, sn in (("T-DC-A", "DC Hall A transformer", 2.5), ("T-DC-B", "DC Hall B transformer", 2.5)):
        hall = eq_id[-1]
        eqs.append(_eq(eq_id, "transformer", name, {
            "hv_bus": "B-DC-MV", "lv_bus": f"B-DC-{hall}", "sn_mva": sn, "vn_hv_kv": 20.0, "vn_lv_kv": 0.4,
            "vk_percent": 6.0, "vkr_percent": 0.95, "pfe_kw": 2.1, "i0_percent": 0.25, "shift_degree": 150.0,
            "tap_side": "hv", "tap_neutral": 0, "tap_min": -2, "tap_max": 2, "tap_step_percent": 2.5, "tap_pos": 0,
            "on_load_tap_changer": False},
            assumed=("vk_percent", "vkr_percent", "pfe_kw", "i0_percent"), **dc))
    for eq_id, name, bus, p, pf, category, flexible in DC_LOADS:
        eqs.append(_eq(eq_id, "load", name, {
            "bus": bus, "p_mw": p, "q_mvar": _q(p, pf), "category": category, "load_model": "constant PQ",
            "flexible": flexible, "power_factor": pf}, assumed=("p_mw", "q_mvar", "power_factor"), **dc))
    eqs.append(_eq("BESS-DC", "storage", "Battery energy storage (1 MW / 2 MWh)", {
        "bus": "B-DC-A", "p_mw": 0.0, "max_e_mwh": 2.0, "max_p_mw": 1.0, "soc_percent": 80.0, "in_service": True}, **dc))
    eqs.append(_eq("PV-DC", "sgen", "Rooftop PV (0.4 MWp)", {
        "bus": "B-DC-B", "p_mw": 0.4, "q_mvar": 0.0, "technology": "PV", "in_service": True}, **dc))
    eqs.append(_eq("GEN-DC", "generator", "Backup diesel generator (2 MW, standby)", {
        "bus": "B-DC-B", "p_mw": 2.0, "vm_pu": 1.0, "in_service": False}, **dc))
    eqs.append(_eq("CB-DC", "switch", "DC service breaker", {
        "bus": "B14", "element": "L14-DC", "et": "l", "closed": True, "switch_type": "CB"}, **dc))
    return eqs


def demo_scenarios() -> list[dict]:
    """Scenario definitions seeded into MongoDB (conditions only; no results)."""
    return [
        {"_id": "scn-base-peak", "name": "Evening peak with data center",
         "description": "CIGRE benchmark peak loading, PV at zero output (evening), data center at study-point demand.",
         "conditions": {"global_scale": 1.0, "sgen_scale": 0.0}, "kind": "base"},
        {"_id": "scn-ai-surge", "name": "AI training surge (+80 % training load)",
         "description": "Same evening peak with the AI training cluster ramped to 180 % (large model run).",
         "conditions": {"global_scale": 1.0, "sgen_scale": 0.0, "category_scale": {"dc_training": 1.8}}, "kind": "base"},
        {"_id": "scn-light-night", "name": "Light-load night (35 %)",
         "description": "Night valley: all loads at 35 % of peak, PV off.",
         "conditions": {"global_scale": 0.35, "sgen_scale": 0.0}, "kind": "base"},
    ]
