"""Unit tests for the electrical engine (no database)."""
from __future__ import annotations

import copy
import math

import pandapower as pp
import pandapower.networks as pn
import pytest

from app.engine.comparison import compare_results
from app.engine.datacenter import dc_hourly
from app.engine.network import ModelValidationError, build_network, validate_equipment
from app.engine.powerflow import run_powerflow
from app.engine.remediation import RemediationContext, capacitor_for_voltage, tap_candidates, voltage_zones
from app.engine.rootcause import analyze_voltage
from app.engine.simulate import simulate, simulate_conditions
from app.engine.topology import compute_layout, downstream_buses, path_from_source
from app.engine.violations import classify_loading, classify_voltage, detect_violations, merged_thresholds
from app.seed.public_data import datacenter_profile

pytestmark = pytest.mark.unit


def _without_datacenter(equipment: list[dict]) -> list[dict]:
    return [e for e in equipment if e.get("group") != "datacenter"]


def test_model_matches_reference_cigre_power_flow(equipment):
    # Arrange: the original public benchmark solved directly with pandapower
    ref = pn.create_cigre_network_mv(with_der=False)
    pp.runpp(ref)
    ref_v = {f"B{name.replace('Bus ', '')}": v for name, v in zip(ref.bus.name, ref.res_bus.vm_pu)}
    # Act: our persisted-model pipeline without the data-center extension
    result = run_powerflow(build_network(_without_datacenter(equipment)))
    # Assert: identical bus voltages (model fidelity to the public source)
    assert result["converged"]
    for bus in result["buses"]:
        assert bus["vm_pu"] == pytest.approx(ref_v[bus["id"]], abs=1e-5)


def test_validation_reports_missing_parameters(equipment):
    broken = copy.deepcopy(equipment)
    line = next(e for e in broken if e["type"] == "line")
    del line["params"]["max_i_ka"]
    problems = validate_equipment(broken)
    assert {"equipment_id": line["eq_id"], "field": "max_i_ka", "problem": "missing required parameter"} in problems
    with pytest.raises(ModelValidationError):
        build_network(broken)


def test_voltage_and_loading_classification_uses_configured_bands():
    th = merged_thresholds({})
    assert classify_voltage(1.0, th)[0] == "ok"
    assert classify_voltage(0.955, th)[0] == "warning"
    assert classify_voltage(0.93, th)[0] == "moderate"
    assert classify_voltage(0.91, th)[0] == "severe"
    assert classify_voltage(1.07, th)[0] == "moderate"
    band = th["loading"]["line"]
    assert classify_loading(95, band)[0] == "warning"
    assert classify_loading(105, band)[0] == "moderate"
    assert classify_loading(125, band)[0] == "severe"
    stricter = merged_thresholds({"voltage": {"lower_pu": 0.97}})
    assert classify_voltage(0.96, stricter)[0] == "moderate"


def test_base_scenario_detects_undervoltage_and_overloads(equipment, peak_conditions):
    out = simulate(equipment, [], peak_conditions)
    types = {(v["type"], v["equipment_id"]) for v in out["violations"]}
    assert ("undervoltage", "B11") in types
    assert ("transformer_overload", "T0-1") in types
    assert ("transformer_overload", "T0-12") in types
    every_component = {e["eq_id"] for e in equipment}
    assert every_component <= set(out["status"])  # every modelled component gets a status colour


def test_nonconvergence_is_reported_not_fabricated(equipment):
    out = simulate(equipment, [], {"global_scale": 6.0})
    assert out["result"]["converged"] is False
    assert "likely_cause" in out["result"]["diagnosis"]
    assert out["violations"][0]["type"] == "convergence_failure"
    assert "buses" not in out["result"]


def test_capacitor_raises_voltage_and_tap_direction(equipment, peak_conditions):
    base = simulate(equipment, [], peak_conditions)["result"]
    with_cap = simulate(equipment, [{"type": "capacitor", "bus": "B11", "q_mvar": 1.5}], peak_conditions)["result"]
    v = lambda r, b: next(x["vm_pu"] for x in r["buses"] if x["id"] == b)  # noqa: E731
    assert v(with_cap, "B11") > v(base, "B11") + 0.005
    boosted = simulate(equipment, [{"type": "tap", "transformer": "T0-1", "tap_pos": -2}], peak_conditions)["result"]
    assert v(boosted, "B1") == pytest.approx(v(base, "B1") + 0.03, abs=0.01)  # ~1.5 %/step on the HV side


def test_capacitor_sizing_reaches_target_and_records_calculation(equipment, peak_conditions):
    ctx = RemediationContext(equipment, peak_conditions, {})
    base = ctx.peak([])
    zones = voltage_zones(ctx, base["violations"])
    cand = capacitor_for_voltage(ctx, base, "T0-1", zones["T0-1"])
    s = cand["sizing"]
    assert s["dv_dq_pu_per_mvar"] > 0
    assert s["q_selected_mvar"] >= s["q_required_mvar"] - 1e-9
    assert math.isclose(s["q_selected_mvar"] / s["step_mvar"], round(s["q_selected_mvar"] / s["step_mvar"]), abs_tol=1e-6)
    assert s["refinement"][-1]["vm_pu"] >= 0.95


def test_tap_sweep_selects_boosting_position(equipment, peak_conditions):
    ctx = RemediationContext(equipment, peak_conditions, {})
    base = ctx.peak([])
    cands = {c["interventions"][0]["transformer"]: c for c in tap_candidates(ctx, base, base["violations"])}
    assert cands["T0-1"]["interventions"][0]["tap_pos"] < 0
    assert len(cands["T0-1"]["sizing"]["sweep"]) == 19


def test_light_load_verification_catches_capacitor_overvoltage(equipment, peak_conditions):
    huge = [{"type": "capacitor", "bus": "B11", "q_mvar": 12.0}]
    out = simulate_conditions(equipment, huge, peak_conditions)
    assert any(v["type"] == "overvoltage" and v["condition"] == "light_load" for v in out["violations"])


def test_regulator_insertion_adds_proposed_equipment(equipment, peak_conditions):
    out = simulate(equipment, [{"type": "regulator", "line": "L2-3", "tap_pos": 6}], peak_conditions)
    ids = {p["eq_id"] for p in out["result"]["proposed_equipment"]}
    assert "REG-NEW-L2-3" in ids
    reg = next(b for b in out["result"]["branches"] if b["id"] == "REG-NEW-L2-3")
    assert reg["kind"] == "regulator"


def test_comparison_classifies_resolved_remaining_new(equipment, peak_conditions):
    before = simulate(equipment, [], peak_conditions)
    after = simulate(equipment, [{"type": "tap", "transformer": "T0-1", "tap_pos": -3}], peak_conditions)
    comp = compare_results(before["result"], after["result"], before["violations"], after["violations"])
    resolved = {v["equipment_id"] for v in comp["violations"]["resolved"]}
    assert "B11" in resolved
    assert {r["id"] for r in comp["buses"]} == {b["id"] for b in after["result"]["buses"]}
    row = next(r for r in comp["buses"] if r["id"] == "B11")
    assert row["change_pu"] == pytest.approx(row["after_pu"] - row["before_pu"], abs=1e-6)


def test_root_cause_decomposes_path_drop(equipment, peak_conditions):
    out = simulate(equipment, [], peak_conditions)
    v = next(x for x in out["violations"] if x["equipment_id"] == "B11")
    a = analyze_voltage(equipment, out["result"], v, None)
    path = a["path"]
    assert path["segments"][0]["eq_id"] == "T0-1"
    assert path["total_drop_percent"] == pytest.approx(path["line_drop_percent"] + path["transformer_drop_percent"], abs=1e-3)
    assert a["causes"][0]["code"] == "feeder_impedance_drop"


def test_topology_helpers(equipment):
    assert [s["eq_id"] for s in path_from_source(equipment, "B-DC-A")][-1] == "T-DC-A"
    assert {"B-DC-MV", "B-DC-A", "B-DC-B"} <= downstream_buses(equipment, "L14-DC")
    layout = compute_layout(equipment)
    drawable = {e["eq_id"] for e in equipment if e["type"] not in ("line", "switch")}
    assert drawable <= set(layout)


def test_datacenter_energy_balance_and_cooling_model():
    profile = datacenter_profile("t")
    temps = [20.0] * 24
    dc = dc_hourly(profile, temps)
    it = dc["it_total"][19]
    cooling = dc["components"]["DC-COOL"][19]
    assert cooling == pytest.approx(it * (0.18 + 0.012 * 5), rel=1e-3)
    assert sum(dc["components"]["DC-TRAIN"]) == pytest.approx(24.0)
    assert 1.1 < dc["pue"][19] < 1.6


def test_detect_violations_flags_datacenter_capacity(equipment, peak_conditions):
    built = build_network(equipment, [], {**peak_conditions, "category_scale": {"dc_training": 3.0}})
    result = run_powerflow(built)
    violations, _ = detect_violations(result, built.meta, {})
    assert any(v["type"] == "datacenter_capacity" for v in violations)
