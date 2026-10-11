"""Analysis agents: modelling, power flow, violation detection, root cause, data center, memory retrieval."""
from __future__ import annotations

from collections import Counter

from app import llm
from app.agents.base import Agent, RunState, StopWorkflow
from app.db.repositories import new_id, now_utc
from app.engine.datacenter import dc_hourly, flexibility_summary, network_timeseries
from app.engine.network import build_network, validate_equipment
from app.engine.rootcause import analyze_all, no_datacenter_conditions
from app.engine.simulate import simulate, simulate_conditions
from app.engine.violations import UNSUPPORTED_STUDIES, hard_violations, summarize, violation_key


def scenario_conditions(state: RunState) -> dict:
    scenario = state.repo.get_scenario(state.scenario_id)
    if scenario is None:
        raise StopWorkflow(f"Scenario {state.scenario_id} not found", status="failed")
    return scenario.get("conditions", {})


def thresholds(state: RunState) -> dict:
    circuit = state.repo.get_circuit(state.circuit_id) or {}
    return circuit.get("thresholds", {})


def save_result(state: RunState, out: dict, kind: str, interventions: list[dict], conditions: dict,
                label: str) -> str:
    doc = state.repo.save_result({
        "scenario_id": state.scenario_id, "circuit_id": state.circuit_id, "run_id": state.run_id, "kind": kind,
        "label": label, "interventions": interventions, "conditions": conditions, **out["result"],
        "violations": out["violations"], "component_status": out["status"]})
    return doc["_id"]


class CircuitModelingAgent(Agent):
    key, label = "circuit_modeling", "Circuit Modeling Agent"
    description = "Load the circuit model from MongoDB, validate parameters and topology, build the solver model."

    def run(self, state: RunState) -> dict:
        equipment = state.repo.get_equipment(state.circuit_id)
        if not equipment:
            raise StopWorkflow(f"Circuit {state.circuit_id} has no equipment", status="failed")
        problems = validate_equipment(equipment)
        if problems:
            state.context["model_problems"] = problems
            raise StopWorkflow(f"{len(problems)} missing/inconsistent parameters; supply them before simulation",
                               status="failed")
        built = build_network(equipment)
        counts = Counter(e["type"] for e in equipment)
        assumed = sorted({e["eq_id"] for e in equipment if "assumed" in e.get("data_quality", {}).values()})
        state.context["model"] = {"equipment_count": len(equipment), "by_type": dict(counts),
                                  "solver_buses": len(built.net.bus), "assumed_parameter_equipment": assumed}
        return {"summary": f"Validated {len(equipment)} components ({counts['bus']} buses, {counts['line']} lines, "
                           f"{counts['transformer']} transformers, {counts['load']} loads); {len(assumed)} carry "
                           f"documented assumed parameters.",
                "details": state.context["model"], "verification": "all required parameters present"}


class PowerFlowAgent(Agent):
    key, label = "power_flow", "Power Flow Agent"
    description = "Run the AC Newton-Raphson power flow for the scenario and persist bus/branch results."

    def run(self, state: RunState) -> dict:
        conditions = scenario_conditions(state)
        equipment = state.repo.get_equipment(state.circuit_id)
        out = simulate(equipment, [], conditions, thresholds(state))
        result_id = save_result(state, out, "base", [], conditions, "Original circuit")
        state.context.update(conditions=conditions, base_result_id=result_id)
        if not out["result"]["converged"]:
            state.context["base_violations"] = out["violations"]
            raise StopWorkflow("Power flow did not converge: " + out["result"]["diagnosis"]["likely_cause"],
                               status="failed")
        t = out["result"]["totals"]
        return {"summary": f"Converged. Vmin {t['min_vm_pu']:.4f} pu, max loading {t['max_loading_percent']:.1f} %, "
                           f"losses {t['losses_p_mw']:.3f} MW ({t['losses_percent']:.2f} %), grid PF {t['grid_pf']:.3f}.",
                "details": {"result_id": result_id, "totals": t}, "verification": "Newton-Raphson converged"}


class ViolationDetectionAgent(Agent):
    key, label = "violation_detection", "Violation Detection Agent"
    description = "Check every bus, branch, transformer and load against limits at peak and light load."

    def run(self, state: RunState) -> dict:
        equipment = state.repo.get_equipment(state.circuit_id)
        out = simulate_conditions(equipment, [], state.context["conditions"], thresholds(state))
        violations = out["violations"]
        faults = [{**v, "_id": new_id("flt"), "circuit_id": state.circuit_id, "scenario_id": state.scenario_id,
                   "run_id": state.run_id, "result_id": state.context["base_result_id"], "status": "open",
                   "key": violation_key(v), "detected_at": now_utc()} for v in violations]
        state.repo.save_faults(faults)
        state.context["base_violations"] = violations
        state.context["unsupported_studies"] = UNSUPPORTED_STUDIES
        s = summarize(violations)
        return {"summary": f"{s['count']} violations ({', '.join(f'{k}: {n}' for k, n in s['by_type'].items()) or 'none'}); "
                           f"{len(hard_violations(violations))} require correction.",
                "details": {**s, "fault_ids": [f["_id"] for f in faults],
                            "unsupported_studies": [u["study"] for u in UNSUPPORTED_STUDIES]}}


class RootCauseAgent(Agent):
    key, label = "root_cause", "Root Cause Agent"
    description = "Diagnose each violation with path voltage-drop decomposition, counterfactuals and Claude review."

    def run(self, state: RunState) -> dict:
        equipment = state.repo.get_equipment(state.circuit_id)
        base = state.repo.get_result(state.context["base_result_id"])
        conditions = state.context["conditions"]
        no_dc = simulate(equipment, [], no_datacenter_conditions(equipment, conditions), thresholds(state))
        peak_viol = [v for v in state.context["base_violations"] if v.get("condition") == "peak"]
        analyses = analyze_all(equipment, base, peak_viol, no_dc["result"])
        state.context["rootcauses"] = analyses
        state.context["no_dc_totals"] = no_dc["result"].get("totals")
        for a in analyses:
            top = a["causes"][0] if a["causes"] else None
            if top:
                state.repo.db.detected_faults.update_many(
                    {"run_id": state.run_id, "equipment_id": a["equipment_id"], "type": a["type"]},
                    {"$set": {"root_cause": top["title"], "root_cause_code": top["code"]}})
        fallback = _deterministic_diagnosis(analyses)
        by_key = {a["violation_key"]: a for a in analyses}
        by_id = {b["id"]: b for b in base["buses"] + base["branches"]}
        tools = [
            {"name": "list_violations", "description": "List violation keys with value and top computed cause.",
             "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "get_violation_evidence", "description": "Full computed evidence for one violation key.",
             "input_schema": {"type": "object", "properties": {"violation_key": {"type": "string"}},
                              "required": ["violation_key"], "additionalProperties": False}},
            {"name": "get_component_result", "description": "Solved power-flow values for a bus or branch id.",
             "input_schema": {"type": "object", "properties": {"eq_id": {"type": "string"}},
                              "required": ["eq_id"], "additionalProperties": False}},
        ]
        handlers = {
            "list_violations": lambda _: [{"key": a["violation_key"], "top_cause": a["causes"][0]["code"] if a["causes"] else None}
                                          for a in analyses],
            "get_violation_evidence": lambda i: by_key[i["violation_key"]],
            "get_component_result": lambda i: by_id[i["eq_id"]],
        }
        narrative = llm.tool_loop(
            "Diagnose the electrical violations in this network. Use the tools to inspect evidence, group related "
            "violations (same feeder or transformer zone), and for each group explain the engineering cause with "
            "the computed numbers. End with which causes point to which remedies. Max 350 words.",
            tools, handlers, fallback)
        state.context["rootcause_narrative"] = narrative
        codes = Counter(c["code"] for a in analyses for c in a["causes"][:1])
        return {"summary": f"Diagnosed {len(analyses)} violations; dominant causes: "
                           f"{', '.join(f'{k} ({n})' for k, n in codes.most_common(3))}.",
                "details": {"cause_counts": dict(codes), "llm_used": narrative["used_llm"],
                            "tool_calls": narrative.get("tool_calls", []), "note": narrative.get("note")}}


def _deterministic_diagnosis(analyses: list[dict]) -> str:
    lines = []
    for a in analyses:
        if not a["causes"]:
            continue
        top = a["causes"][0]
        lines.append(f"- {a['violation_key']}: {top['title']} - {top['evidence']}")
    return "Computed diagnosis (deterministic):\n" + "\n".join(lines)


class DataCenterAgent(Agent):
    key, label = "data_center", "Data Center Agent"
    description = "Model data-center demand (IT, AI training/inference, cooling, UPS), run 24 h AC time series."

    def run(self, state: RunState) -> dict:
        profile = state.repo.get_dc_profile(state.circuit_id)
        if profile is None:
            state.context["dc_analysis"] = None
            return {"summary": "No data-center profile for this circuit; skipped."}
        equipment = state.repo.get_equipment(state.circuit_id)
        meas = {m["category"]: m for m in state.repo.get_measurements(state.circuit_id)}
        temps = meas["temperature"]["values"]
        dc = dc_hourly(profile, temps)
        profiles = {"residential": meas["residential"]["values"], "commercial": meas["commercial"]["values"],
                    "pv": meas["pv"]["values"]}
        ts = network_timeseries(equipment, state.context["conditions"], profiles, dc, [], thresholds(state))
        slim = [{k: v for k, v in r.items() if k not in ("buses", "branches")} for r in ts]
        flex = flexibility_summary(equipment, profile)
        flex["schedule_feasible"] = True
        prices = meas["energy_price"]["values"]
        energy = sum(dc["total"])
        cost = sum(p * x for p, x in zip(prices, dc["total"]))
        state.context["dc_analysis"] = {
            "flexibility": flex, "hourly": dc, "timeseries": slim, "daily_energy_mwh": round(energy, 3),
            "daily_cost_usd": round(cost, 2), "peak_mw": max(dc["total"]), "avg_pue": round(energy / sum(dc["it_total"]), 4),
            "violation_hours": sum(1 for r in slim if r.get("violations")), "profiles": profiles, "temperature_c": temps,
            "prices": prices}
        return {"summary": f"DC daily energy {energy:.1f} MWh, peak {max(dc['total']):.2f} MW, average PUE "
                           f"{energy / sum(dc['it_total']):.2f}; network violations in "
                           f"{state.context['dc_analysis']['violation_hours']} of 24 h; flexible: "
                           f"{', '.join(f['id'] for f in flex['flexible_loads'])} + {len(flex['storage'])} BESS.",
                "details": {"peak_mw": max(dc["total"]), "daily_cost_usd": round(cost, 2),
                            "flexible_loads": flex["flexible_loads"], "storage": flex["storage"]}}


class MemoryRetrievalAgent(Agent):
    key, label = "learning_retrieve", "Recursive Learning Agent (retrieve)"
    description = "Retrieve verified lessons from MongoDB agent memory for the detected violation types."

    def run(self, state: RunState) -> dict:
        types = sorted({v["type"] for v in hard_violations(state.context["base_violations"])})
        hints = state.repo.find_memory(types, verified_only=True)
        state.context["memory_hints"] = [{k: h.get(k) for k in ("_id", "signature", "violation_type", "intervention_type",
                                                                "parameters", "outcome", "lesson", "run_id", "scenario_name")}
                                         for h in hints]
        categories = Counter(h["intervention_type"] for h in hints)
        return {"summary": f"Retrieved {len(hints)} verified lessons for {', '.join(types) or 'no violations'}"
                           + (f"; previously effective: {', '.join(f'{k} ({n})' for k, n in categories.most_common())}"
                              if hints else "; no prior history (first run)."),
                "details": {"lesson_ids": [h["_id"] for h in hints], "categories": dict(categories)}}
