"""Markdown engineering report generated from persisted results (no invented values)."""
from __future__ import annotations

from app.agents.base import RunState
from app.config import COST_ASSUMPTIONS
from app.engine.comparison import compare_results
from app.engine.violations import UNSUPPORTED_STUDIES, hard_violations


def _fmt(v, digits: int = 4) -> str:
    return "-" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def build_report(state: RunState) -> str:
    repo, ctx = state.repo, state.context
    circuit = repo.get_circuit(state.circuit_id) or {}
    scenario = repo.get_scenario(state.scenario_id) or {}
    base = repo.get_result(ctx["base_result_id"])
    iters = ctx.get("iterations", [])
    final = iters[-1] if iters else {}
    rec = repo.db.recommendations.find_one({"_id": ctx.get("final_recommendation_id")}) if ctx.get("final_recommendation_id") else None
    verified = repo.get_result(final["verified_result_id"]) if final.get("verified_result_id") else None
    lines = [f"# GridMind AI Engineering Report", "",
             f"**Circuit:** {circuit.get('name')}  ", f"**Scenario:** {scenario.get('name')} - {scenario.get('description', '')}  ",
             f"**Run:** `{state.run_id}`  ", f"**Status:** {'VERIFIED' if final.get('verified') else 'NOT VERIFIED'}"
             + (f" ({ctx.get('stop_reason')})" if ctx.get("stop_reason") else ""), ""]
    t = base["totals"]
    lines += ["## 1. Original operating state", "",
              f"AC Newton-Raphson power flow converged. Minimum voltage {t['min_vm_pu']:.4f} pu, maximum branch loading "
              f"{t['max_loading_percent']:.1f} %, losses {t['losses_p_mw']:.3f} MW ({t['losses_percent']:.2f} % of "
              f"{t['load_p_mw']:.2f} MW load), grid power factor {t['grid_pf']:.3f}, data-center demand "
              f"{t['datacenter_p_mw']:.2f} MW.", ""]
    viol = ctx.get("base_violations", [])
    lines += ["## 2. Detected violations", "", "| Type | Equipment | Value | Limit | Severity | Basis | Condition |",
              "|---|---|---|---|---|---|---|"]
    for v in viol:
        lines.append(f"| {v['type']} | {v['equipment_id']} | {_fmt(v['value'])} {v['unit']} | {v['limit']} | "
                     f"{v['severity']} | {v['severity_basis']} | {v.get('condition')} |")
    lines += ["", "## 3. Root-cause analysis", ""]
    for a in ctx.get("rootcauses", [])[:30]:
        if a["causes"]:
            c = a["causes"][0]
            lines.append(f"- **{a['violation_key']}** - {c['title']}: {c['evidence']}")
    narrative = ctx.get("rootcause_narrative", {})
    if narrative.get("used_llm"):
        lines += ["", "### Agent diagnosis (Claude, from tool-retrieved solver evidence)", "", narrative["text"]]
    lines += ["", "## 4. Corrective-action candidates", ""]
    for i, it in enumerate(iters, start=1):
        lines += [f"### Iteration {i}", "", "| Candidate | Sizing method | Capex (USD) |", "|---|---|---|"]
        for c in it.get("candidates", []):
            lines.append(f"| {c['title']} | {c['sizing'].get('method', '')} | {c['capex_usd']:,.0f} |")
        if it.get("search"):
            lines += ["", f"Search: {it['search']['method']}. Plans evaluated: {it['search']['plans_evaluated']}; "
                          f"claim: {it['search']['optimality_claim']}.", ""]
            lines += ["| Rank | Plan | Feasible | Residual | Capex | Objective (USD/yr) |", "|---|---|---|---|---|---|"]
            for r, p in enumerate(it.get("ranked", [])[:6], start=1):
                lines.append(f"| {r} | {p['plan_id']} | {p['feasible']} | {p['metrics']['hard_count']} | "
                             f"{p['objective']['capex_usd']:,.0f} | {p['objective']['total']:,.0f} |")
    if rec:
        lines += ["", "## 5. Recommended plan (requires engineer approval)", "", f"**{rec['title']}**", ""]
        for c in rec["candidates"]:
            lines.append(f"- {c['title']} - {c['rationale']}")
            for note in c.get("notes", []):
                lines.append(f"  - Note: {note}")
        o = rec["objective"]
        lines += ["", f"Capex ${o['capex_usd']:,.0f} (annualized ${o['capex_annualized_usd']:,.0f}/yr), opex "
                      f"${o['opex_usd_per_year']:,.0f}/yr, losses ${o['losses_cost_usd_per_year']:,.0f}/yr. "
                      f"Cost basis: illustrative planning assumptions ({COST_ASSUMPTIONS['capacitor_per_kvar_usd']} USD/kvar, "
                      f"{COST_ASSUMPTIONS['line_upgrade_per_km_usd']:,.0f} USD/km cable, ...)."]
    if verified:
        comp = compare_results(base, verified, [v for v in viol if v.get("condition") == "peak"],
                               verified.get("violations", []))
        lines += ["", "## 6. Independent verification (before -> after)", "",
                  "| Bus | Before (pu) | After (pu) | Change | Status |", "|---|---|---|---|---|"]
        for r in comp["buses"]:
            lines.append(f"| {r['id']} | {_fmt(r['before_pu'])} | {_fmt(r['after_pu'])} | {_fmt(r['change_pu'])} | "
                         f"{r['before_status']} -> {r['after_status']} |")
        lines += ["", "| Branch | Before (%) | After (%) | Change | Status |", "|---|---|---|---|---|"]
        for r in comp["branches"]:
            lines.append(f"| {r['id']} | {_fmt(r['before_loading'], 1)} | {_fmt(r['after_loading'], 1)} | "
                         f"{_fmt(r['change_loading'], 1)} | {r['before_status']} -> {r['after_status']} |")
        tt = comp["totals"]
        lines += ["", f"Losses {tt['losses_p_mw']['before']:.3f} -> {tt['losses_p_mw']['after']:.3f} MW; grid PF "
                      f"{tt['grid_pf']['before']:.3f} -> {tt['grid_pf']['after']:.3f}.", "",
                  f"Resolved: {len(comp['violations']['resolved'])}; remaining: {len(comp['violations']['remaining'])}; "
                  f"new: {len(comp['violations']['new'])}. Light-load verification: "
                  f"{len(hard_violations(repo.get_result(final['verified_light_result_id']).get('violations', [])))} hard violations."]
    sched = final.get("dc_schedule")
    if sched and sched.get("success"):
        lines += ["", "## 7. Data-center coordination", "",
                  f"Training load re-scheduled by LP ({sched['method']}): {sched['shifted_mwh']} MWh shifted, energy cost "
                  f"${sched['energy_cost_before_usd']:,.0f} -> ${sched['energy_cost_after_usd']:,.0f} per day, violation hours "
                  f"{sched['violation_hours_before']} -> {sched['violation_hours_after']} (AC time series). "
                  f"Claim: {sched['optimality_claim']}."]
    lines += ["", "## 8. Limitations and assumptions", ""]
    for u in UNSUPPORTED_STUDIES:
        lines.append(f"- {u['study']}: {u['status']} - {u['reason']}")
    lines += ["- Steady-state balanced positive-sequence model; phase imbalance not modelled.",
              "- Parameters flagged 'assumed' in the equipment data-quality fields are documented example values.",
              "- No action is applied to real equipment; every recommendation requires engineer approval."]
    return "\n".join(lines)
