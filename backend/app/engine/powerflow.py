"""AC power flow execution and result extraction (pandapower Newton-Raphson)."""
from __future__ import annotations

import math

import pandapower as pp

from app.engine.network import BuiltNetwork

NON_CONVERGENCE_PROBE_FACTORS = (0.75, 0.5, 0.25)


def _r(value: float, digits: int = 5) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), digits)


def _solve(net: pp.pandapowerNet) -> bool:
    try:
        pp.runpp(net, algorithm="nr", max_iteration=40, calculate_voltage_angles=True, numba=False,
                 init="auto")
        return True
    except pp.LoadflowNotConverged:
        return False


def run_powerflow(built: BuiltNetwork) -> dict:
    """Run the power flow. Never fabricates numbers: returns a diagnosis when it fails."""
    if not _solve(built.net):
        return {"converged": False, "diagnosis": diagnose_non_convergence(built)}
    return {"converged": True, **extract_results(built)}


def diagnose_non_convergence(built: BuiltNetwork) -> dict:
    net = built.net
    import pandapower.topology as top

    unsupplied = [built.eq_id("bus", int(b)) for b in top.unsupplied_buses(net)]
    original = net.load[["p_mw", "q_mvar"]].copy()
    converged_at = None
    for factor in NON_CONVERGENCE_PROBE_FACTORS:
        net.load["p_mw"] = original["p_mw"] * factor
        net.load["q_mvar"] = original["q_mvar"] * factor
        if _solve(net):
            converged_at = factor
            break
    net.load[["p_mw", "q_mvar"]] = original
    if unsupplied:
        cause = "Islanded (unsupplied) buses exist; check switch states and topology."
    elif converged_at:
        cause = (f"Network converges at {int(converged_at * 100)} % of the scenario loading: the requested demand "
                 "likely exceeds the voltage-stability (maximum loadability) limit of the network.")
    else:
        cause = "Does not converge even at 25 % load: likely inconsistent impedance or rating parameters."
    return {"unsupplied_buses": unsupplied, "converges_at_load_factor": converged_at,
            "total_load_mw": _r(original["p_mw"].sum(), 3), "likely_cause": cause}


def extract_results(built: BuiltNetwork) -> dict:
    net = built.net
    buses = []
    for eq_id, i in built.index["bus"].items():
        vn = float(net.bus.vn_kv.at[i])
        vm = float(net.res_bus.vm_pu.at[i])
        buses.append({"id": eq_id, "vn_kv": vn, "vm_pu": _r(vm), "v_kv": _r(vm * vn, 4),
                      "va_degree": _r(net.res_bus.va_degree.at[i], 3),
                      "deviation_percent": _r((vm - 1.0) * 100, 3),
                      "p_mw": _r(net.res_bus.p_mw.at[i]), "q_mvar": _r(net.res_bus.q_mvar.at[i])})
    vm = {b["id"]: b["vm_pu"] for b in buses}

    branches = []
    for eq_id, i in built.index["line"].items():
        fb, tb = int(net.line.from_bus.at[i]), int(net.line.to_bus.at[i])
        res = net.res_line.loc[i]
        i_ka = max(float(res.i_from_ka), float(res.i_to_ka))
        branches.append({
            "id": eq_id, "kind": "line", "from": built.eq_id("bus", fb), "to": built.eq_id("bus", tb),
            "in_service": bool(net.line.in_service.at[i]), "i_ka": _r(i_ka), "i_rated_ka": _r(net.line.max_i_ka.at[i]),
            "loading_percent": _r(res.loading_percent, 3), "p_from_mw": _r(res.p_from_mw), "q_from_mvar": _r(res.q_from_mvar),
            "p_to_mw": _r(res.p_to_mw), "q_to_mvar": _r(res.q_to_mvar), "pl_mw": _r(res.pl_mw, 6), "ql_mvar": _r(res.ql_mvar, 6),
            "v_from_pu": _r(res.vm_from_pu), "v_to_pu": _r(res.vm_to_pu),
            "vdrop_percent": _r((float(res.vm_from_pu) - float(res.vm_to_pu)) * 100, 4)})
    for eq_id, i in built.index["trafo"].items():
        hb, lb = int(net.trafo.hv_bus.at[i]), int(net.trafo.lv_bus.at[i])
        res = net.res_trafo.loc[i]
        sn, vn_lv = float(net.trafo.sn_mva.at[i]), float(net.trafo.vn_lv_kv.at[i])
        tap = net.trafo.tap_pos.at[i]
        branches.append({
            "id": eq_id, "kind": "regulator" if built.meta.get(eq_id, {}).get("type") == "regulator" else "transformer",
            "from": built.eq_id("bus", hb), "to": built.eq_id("bus", lb), "in_service": bool(net.trafo.in_service.at[i]),
            "i_ka": _r(res.i_lv_ka), "i_rated_ka": _r(sn / (math.sqrt(3) * vn_lv)), "i_hv_ka": _r(res.i_hv_ka),
            "loading_percent": _r(res.loading_percent, 3), "s_mva": _r(math.hypot(res.p_hv_mw, res.q_hv_mvar), 4),
            "sn_mva": sn, "p_from_mw": _r(res.p_hv_mw), "q_from_mvar": _r(res.q_hv_mvar), "p_to_mw": _r(res.p_lv_mw),
            "q_to_mvar": _r(res.q_lv_mvar), "pl_mw": _r(res.pl_mw, 6), "ql_mvar": _r(res.ql_mvar, 6),
            "v_from_pu": vm.get(built.eq_id("bus", hb)), "v_to_pu": vm.get(built.eq_id("bus", lb)),
            "vdrop_percent": _r((float(res.vm_hv_pu) - float(res.vm_lv_pu)) * 100, 4),
            "tap_pos": None if tap is None or (isinstance(tap, float) and math.isnan(tap)) else int(tap)})

    loads = []
    for eq_id, i in built.index["load"].items():
        p, q = float(net.res_load.p_mw.at[i]), float(net.res_load.q_mvar.at[i])
        s = math.hypot(p, q)
        loads.append({"id": eq_id, "bus": built.eq_id("bus", int(net.load.bus.at[i])), "p_mw": _r(p), "q_mvar": _r(q),
                      "s_mva": _r(s), "pf": _r(p / s if s else 1.0, 4)})
    capacitors = [{"id": eq_id, "bus": built.eq_id("bus", int(net.shunt.bus.at[i])),
                   "q_injected_mvar": _r(-float(net.res_shunt.q_mvar.at[i])), "in_service": bool(net.shunt.in_service.at[i])}
                  for eq_id, i in built.index["shunt"].items()]
    sgens = [{"id": eq_id, "p_mw": _r(net.res_sgen.p_mw.at[i]), "q_mvar": _r(net.res_sgen.q_mvar.at[i])}
             for eq_id, i in built.index["sgen"].items()]
    storage = [{"id": eq_id, "p_mw": _r(net.res_storage.p_mw.at[i])} for eq_id, i in built.index["storage"].items()]
    grid = [{"id": eq_id, "p_mw": _r(net.res_ext_grid.p_mw.at[i]), "q_mvar": _r(net.res_ext_grid.q_mvar.at[i])}
            for eq_id, i in built.index["ext_grid"].items()]

    p_grid = sum(g["p_mw"] for g in grid)
    q_grid = sum(g["q_mvar"] for g in grid)
    s_grid = math.hypot(p_grid, q_grid)
    load_p = sum(item["p_mw"] for item in loads)
    load_q = sum(item["q_mvar"] for item in loads)
    losses_p = float(net.res_line.pl_mw.sum() + net.res_trafo.pl_mw.sum())
    losses_q = float(net.res_line.ql_mvar.sum() + net.res_trafo.ql_mvar.sum())
    dc_p = sum(item["p_mw"] for item in loads if built.meta[item["id"]].get("group") == "datacenter")
    return {
        "buses": buses, "branches": branches, "loads": loads, "capacitors": capacitors, "sgens": sgens,
        "storage": storage, "ext_grid": grid,
        "totals": {"load_p_mw": _r(load_p, 4), "load_q_mvar": _r(load_q, 4), "losses_p_mw": _r(losses_p, 5),
                   "losses_q_mvar": _r(losses_q, 5), "losses_percent": _r(100 * losses_p / load_p if load_p else 0, 3),
                   "grid_p_mw": _r(p_grid, 4), "grid_q_mvar": _r(q_grid, 4),
                   "grid_pf": _r(p_grid / s_grid if s_grid else 1.0, 4), "datacenter_p_mw": _r(dc_p, 4),
                   "min_vm_pu": min(b["vm_pu"] for b in buses), "max_vm_pu": max(b["vm_pu"] for b in buses),
                   "max_loading_percent": max((b["loading_percent"] or 0) for b in branches)},
    }
