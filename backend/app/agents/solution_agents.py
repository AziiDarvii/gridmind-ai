"""Solution agents: corrective actions, optimization, independent verification, learning, report."""
from __future__ import annotations

from collections import Counter

from app import llm
from app.agents.analysis_agents import save_result, thresholds
from app.agents.base import Agent, RunState, StopWorkflow
from app.db.repositories import new_id, now_utc
from app.engine.comparison import compare_results
from app.engine.datacenter import schedule_training
from app.engine.optimization import beam_search, tradeoff_summary
from app.engine.remediation import RemediationContext, generate_candidates
from app.engine.simulate import simulate_conditions
from app.engine.violations import UNSUPPORTED_STUDIES, hard_violations, violation_key

TOP_RECOMMENDATIONS = 3


def _ctx(state: RunState, equipment: list[dict]) -> RemediationContext:
    return RemediationContext(equipment, state.context["conditions"], thresholds(state),
                              base_interventions=list(state.context.get("plan_interventions", [])),
                              memory_hints=state.context.get("memory_hints", []))


def slim_plan(plan: dict) -> dict:
    metrics = {k: v for k, v in plan["metrics"].items() if k not in ("result", "light_result", "violations")}
    return {**{k: v for k, v in plan.items() if k != "metrics"}, "metrics": metrics}


def _iteration(state: RunState) -> dict:
    iters = state.context.setdefault("iterations", [])
    while len(iters) < state.iteration:
        iters.append({"iteration": len(iters) + 1})
    return iters[state.iteration - 1]


class CorrectiveActionAgent(Agent):
    key, label = "corrective_action", "Corrective Action Agent"
    description = "Generate cause-specific remedies and size them by simulation (capacitor, tap, regulator, ...)."

    def task_text(self, state: RunState) -> str:
        prior = len(state.context.get("plan_interventions", []))
        return self.description + (f" Iteration {state.iteration}, building on {prior} accepted interventions."
                                   if prior else "")

    def run(self, state: RunState) -> dict:
        equipment = state.repo.get_equipment(state.circuit_id)
        ctx = _ctx(state, equipment)
        base = ctx.peak([])
        violations = base["violations"]
        if not hard_violations(violations):
            _iteration(state)["candidates"] = []
            return {"summary": "No hard violations at the current baseline; nothing to correct."}
        candidates = generate_candidates(ctx, base, violations, state.context.get("dc_analysis"))
        it = _iteration(state)
        it["candidates"] = candidates
        it["sizing_power_flows"] = ctx.evaluations
        if not candidates:
            raise StopWorkflow("No technically feasible candidate remedies could be generated (hard constraint).")
        cats = Counter(c["category"] for c in candidates)
        return {"summary": f"Generated and sized {len(candidates)} candidates: "
                           f"{', '.join(f'{k} ({n})' for k, n in cats.items())}.",
                "details": {"candidates": [{"id": c["id"], "title": c["title"], "capex_usd": c["capex_usd"],
                                            "historically_verified": c.get("historically_verified")} for c in candidates]}}


class OptimizationAgent(Agent):
    key, label = "optimization", "Optimization Agent"
    description = "Rank candidate combinations with an explicit objective (beam search, AC power flow per plan)."

    def run(self, state: RunState) -> dict:
        it = _iteration(state)
        candidates = it.get("candidates") or []
        if not candidates:
            it["search"] = None
            return {"summary": "No candidates to optimize."}
        equipment = state.repo.get_equipment(state.circuit_id)
        ctx = _ctx(state, equipment)
        preferred = {h["intervention_type"] for h in state.context.get("memory_hints", [])}
        cheapest: dict[str, dict] = {}
        for c in sorted(candidates, key=lambda c: c["capex_usd"] + c["opex_usd_per_year"]):
            if c["category"] in preferred:
                cheapest.setdefault(c["category"], c)  # one (cheapest) candidate per previously verified category
        seed = tuple(sorted(c["id"] for c in cheapest.values()))
        search = beam_search(ctx, candidates, seeds=[seed] if seed else None)
        baseline = search["baseline"]
        ranked = [p for p in search["ranked"] if p["candidate_ids"]]
        top = (search["feasible"] or ranked)[:TOP_RECOMMENDATIONS]
        by_id = {c["id"]: c for c in candidates}
        recs = []
        for rank, plan in enumerate(top, start=1):
            cands = [by_id[c] for c in plan["candidate_ids"]]
            recs.append({"_id": new_id("rec"), "run_id": state.run_id, "circuit_id": state.circuit_id,
                         "scenario_id": state.scenario_id, "iteration": state.iteration, "rank": rank,
                         "plan_id": plan["plan_id"], "title": " + ".join(c["title"] for c in cands),
                         "candidates": cands, "interventions": state.context.get("plan_interventions", []) + plan["interventions"],
                         "new_interventions": plan["interventions"], "objective": plan["objective"],
                         "tradeoffs": tradeoff_summary(plan, baseline), "metrics": slim_plan(plan)["metrics"],
                         "feasible": plan["feasible"], "status": "proposed", "verified": None,
                         "requires_engineer_approval": True, "created_at": now_utc()})
        state.repo.save_recommendations(recs)
        best = top[0]
        proposed_id = save_result(state, {"result": best["metrics"]["result"], "violations": best["metrics"]["violations"],
                                          "status": {}}, "proposed",
                                  recs[0]["interventions"], state.context["conditions"], "Proposed correction")
        it["search"] = {k: search[k] for k in ("plans_evaluated", "power_flows_run", "method", "optimality_claim",
                                                "seeded_from_memory")}
        it["ranked"] = [slim_plan(p) for p in ranked[:12]]
        it["recommendation_ids"] = [r["_id"] for r in recs]
        it["proposed_result_id"] = proposed_id
        schedule = self._coordinate_datacenter(state, equipment, recs[0]) if best["feasible"] else None
        it["dc_schedule"] = schedule
        return {"summary": f"Evaluated {search['plans_evaluated']} plans ({search['power_flows_run']} AC power flows). "
                           f"Best: {recs[0]['title']} - {'feasible' if best['feasible'] else 'residual violations: ' + str(best['metrics']['hard_count'])}, "
                           f"capex ${best['objective']['capex_usd']:,.0f}."
                           + (f" Coordinated DC schedule saves ${schedule['energy_cost_before_usd'] - schedule['energy_cost_after_usd']:,.0f}/day."
                              if schedule and schedule.get("success") else ""),
                "details": {"objective": best["objective"], "search": it["search"], "seeded_plan": list(seed)}}

    def _coordinate_datacenter(self, state: RunState, equipment: list[dict], rec: dict) -> dict | None:
        dca = state.context.get("dc_analysis")
        profile = state.repo.get_dc_profile(state.circuit_id)
        if not dca or not profile:
            return None
        out = schedule_training(equipment, state.context["conditions"], dca["profiles"], profile, dca["temperature_c"],
                                dca["prices"], thresholds(state), interventions=rec["interventions"])
        if not out.get("success"):
            return out
        return {k: v for k, v in out.items() if k not in ("dc_before", "dc_after")} | {
            "dc_total_before": out["dc_before"]["total"], "dc_total_after": out["dc_after"]["total"]}


class VerificationAgent(Agent):
    key, label = "verification", "Independent Verification Agent"
    description = "Re-load the model from MongoDB, re-simulate the proposed plan at peak and light load, compare."

    def run(self, state: RunState) -> dict:
        it = _iteration(state)
        rec_ids = it.get("recommendation_ids") or []
        if not rec_ids:
            state.context["loop_decision"] = "stop"
            state.context["stop_reason"] = "no recommendation to verify"
            return {"summary": "Nothing to verify."}
        rec = state.repo.db.recommendations.find_one({"_id": rec_ids[0]})
        equipment = state.repo.get_equipment(state.circuit_id)  # fresh read: independent of optimizer state
        th = thresholds(state)
        out = simulate_conditions(equipment, rec["interventions"], state.context["conditions"], th)
        peak_id = save_result(state, out["peak"], "verified", rec["interventions"], state.context["conditions"],
                              "Verified corrected circuit")
        light_id = save_result(state, out["light"], "verified_light", rec["interventions"],
                               {**state.context["conditions"], "light_load": True}, "Verified (light load)")
        base = state.repo.get_result(state.context["base_result_id"])
        base_viol = state.context["base_violations"]
        base_peak = [v for v in base_viol if v.get("condition", "peak") == "peak"]
        comparison = compare_results(base, out["peak"]["result"], base_peak, out["peak"]["violations"], th)
        if comparison.get("comparable"):
            _merge_light_load(comparison, base_viol, out["light"]["violations"])
        hard = hard_violations(out["violations"])
        verified = bool(out["peak"]["result"].get("converged") and out["light"]["result"].get("converged") and not hard)
        state.repo.db.recommendations.update_one({"_id": rec["_id"]}, {"$set": {
            "verified": verified, "status": "verified" if verified else "failed_verification",
            "verified_result_id": peak_id, "verified_light_result_id": light_id,
            "verification": {"remaining": len(comparison.get("violations", {}).get("remaining", [])),
                             "new": len(comparison.get("violations", {}).get("new", [])),
                             "resolved": len(comparison.get("violations", {}).get("resolved", [])),
                             "hard_after": len(hard)}}})
        self._update_faults(state, comparison, peak_id)
        it.update(verified=verified, verified_result_id=peak_id, verified_light_result_id=light_id,
                  verified_recommendation_id=rec["_id"], comparison_counts=comparison.get("violations", {}) and {
                      k: len(comparison["violations"][k]) for k in ("resolved", "remaining", "new")})
        state.context["final_recommendation_id"] = rec["_id"]
        if verified:
            state.context["loop_decision"] = "done"
        elif state.iteration >= state.max_iterations:
            state.context["loop_decision"] = "stop"
            state.context["stop_reason"] = f"iteration budget ({state.max_iterations}) exhausted with {len(hard)} violations"
        elif not rec["new_interventions"]:
            state.context["loop_decision"] = "stop"
            state.context["stop_reason"] = "no further improving intervention found"
        else:
            state.context["loop_decision"] = "iterate"
            state.context["plan_interventions"] = rec["interventions"]
        c = comparison.get("violations", {})
        return {"summary": (f"VERIFIED: all {len(c.get('resolved', []))} original violations resolved, no new violations "
                            "at peak or light load." if verified else
                            f"NOT verified: {len(hard)} hard violations remain ({len(c.get('remaining', []))} remaining, "
                            f"{len(c.get('new', []))} new). Decision: {state.context['loop_decision']}."),
                "details": {"verified_result_id": peak_id, "light_result_id": light_id,
                            "remaining": [violation_key(v) for v in c.get("remaining", [])],
                            "new": [violation_key(v) for v in c.get("new", [])]},
                "verification": "passed" if verified else "failed"}

    def _update_faults(self, state: RunState, comparison: dict, result_id: str) -> None:
        if not comparison.get("comparable"):
            return
        remaining = {violation_key(v) for v in comparison["violations"]["remaining"]}
        for fault in state.repo.db.detected_faults.find({"run_id": state.run_id, "status": {"$in": ["open", "resolved", "remaining"]},
                                                         "result_id": state.context["base_result_id"]}):
            status = "remaining" if fault["key"] in remaining else "resolved"
            state.repo.update_fault(fault["_id"], {"status": status, "verified_result_id": result_id})
        new = [{**v, "_id": new_id("flt"), "circuit_id": state.circuit_id, "scenario_id": state.scenario_id,
                "run_id": state.run_id, "result_id": result_id, "status": "new", "key": violation_key(v),
                "detected_at": now_utc(), "introduced_by_correction": True}
               for v in comparison["violations"]["new"]]
        state.repo.save_faults(new)


def _merge_light_load(comparison: dict, base_viol: list[dict], light_after: list[dict]) -> None:
    """Add light-load outcomes (keyed by condition) without mixing them into peak values."""
    before = {violation_key(v): v for v in base_viol if v.get("condition") == "light_load"}
    after = {violation_key(v): v for v in light_after}
    c = comparison["violations"]
    c["resolved"] += [{**v, "status": "resolved", "after_value": None} for k, v in before.items() if k not in after]
    c["remaining"] += [{**after[k], "status": "remaining", "before_value": v["value"]} for k, v in before.items() if k in after]
    c["new"] += [{**v, "status": "new"} for k, v in after.items() if k not in before]


class LearningStoreAgent(Agent):
    key, label = "learning_store", "Recursive Learning Agent (store)"
    description = "Store structured, verification-tagged lessons in MongoDB agent memory."

    def run(self, state: RunState) -> dict:
        rec_id = state.context.get("final_recommendation_id")
        if not rec_id:
            return {"summary": "No recommendation; nothing learned."}
        rec = state.repo.db.recommendations.find_one({"_id": rec_id})
        scenario = state.repo.get_scenario(state.scenario_id) or {}
        causes = {a["violation_key"]: [c["code"] for c in a["causes"][:2]] for a in state.context.get("rootcauses", [])}
        base_types = Counter(v["type"] for v in hard_violations(state.context["base_violations"]))
        stored = []
        for cand in rec["candidates"]:
            targets = cand.get("targets", [])
            vtypes = sorted({t.split("|")[0] for t in targets}) or ["unknown"]
            for vtype in vtypes:
                cause_codes = sorted({c for t in targets if t.startswith(vtype) for c in causes.get("|".join(t.split("|")[:2]), [])})
                params = _lesson_parameters(cand)
                doc = {"_id": f"mem-{state.circuit_id}-{cand['category']}-{vtype}-{cand['id']}"[:120],
                       "signature": f"{vtype}|{cand['category']}|{','.join(cause_codes[:3])}",
                       "violation_type": vtype, "cause_codes": cause_codes, "intervention_type": cand["category"],
                       "parameters": params, "verified": bool(rec.get("verified")),
                       "outcome": {"plan_verified": bool(rec.get("verified")), "targets": len(targets),
                                   "plan": rec["plan_id"], "capex_usd": cand["capex_usd"]},
                       "circuit_id": state.circuit_id, "scenario_id": state.scenario_id,
                       "scenario_name": scenario.get("name"), "run_id": state.run_id,
                       "lesson": f"{cand['title']} was part of a {'verified' if rec.get('verified') else 'non-verified'} plan "
                                 f"for {base_types.get(vtype, 0)} {vtype} violation(s) in '{scenario.get('name')}'.",
                       "created_at": now_utc()}
                state.repo.save_memory(doc)
                stored.append(doc["_id"])
        return {"summary": f"Stored {len(stored)} lessons ({'verified' if rec.get('verified') else 'unverified'}).",
                "details": {"lesson_ids": stored}}


def _lesson_parameters(cand: dict) -> dict:
    s = cand.get("sizing", {})
    keep = ("bus", "q_selected_mvar", "dv_dq_pu_per_mvar", "selected", "step_percent", "site_line", "selected_tap",
            "selected_mva", "reduction_mw", "segments", "tie_line", "p_mw")
    params = {k: s[k] for k in keep if k in s}
    for item in cand["interventions"]:
        params.setdefault("transformer", item.get("transformer"))
    return {k: v for k, v in params.items() if v is not None}


class ReportAgent(Agent):
    key, label = "engineering_report", "Engineering Report Agent"
    description = "Compile the technical report: evidence, calculations, changes, verified results, limitations."

    def run(self, state: RunState) -> dict:
        from app.agents.report import build_report

        markdown = build_report(state)
        evidence = {"violations": len(state.context.get("base_violations", [])),
                    "final": state.context.get("iterations", [])[-1:] or None, "stop_reason": state.context.get("stop_reason")}
        summary = llm.narrate("Write a 120-word executive summary of this power-system study for a utility manager. "
                              "State what was wrong, what is recommended, and whether verification passed.",
                              {"report_markdown": markdown[:30000], **evidence},
                              fallback=_fallback_summary(state))
        state.context["report"] = {"markdown": markdown, "executive_summary": summary["text"],
                                   "llm_used": summary["used_llm"], "generated_at": now_utc().isoformat()}
        return {"summary": f"Report generated ({len(markdown.splitlines())} lines; executive summary by "
                           f"{'Claude' if summary['used_llm'] else 'template'}).",
                "details": {"note": summary.get("note")}}


def _fallback_summary(state: RunState) -> str:
    iters = state.context.get("iterations", [])
    last = iters[-1] if iters else {}
    n = len(hard_violations(state.context.get("base_violations", [])))
    status = "passed" if last.get("verified") else "did not pass"
    return (f"{n} limit violations were found in the original circuit. The agents evaluated "
            f"{sum((i.get('search') or {}).get('plans_evaluated', 0) for i in iters)} candidate plans over {len(iters)} "
            f"iteration(s). Independent verification {status}. See the recommendation and before/after comparison. "
            f"Unsupported studies: {', '.join(u['study'] for u in UNSUPPORTED_STUDIES)}.")
