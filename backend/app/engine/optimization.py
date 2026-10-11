"""Plan optimization: beam search over combinations of sized candidates.

Objective (explicit, USD/year):
    J = annuity * capex + loss_cost + opex + PENALTY_PER_VIOLATION * hard_violations
        + PENALTY_PER_MAGNITUDE * normalized_violation_magnitude
Every plan is evaluated with full AC power flow at peak and light load.
The result is the best plan found among the evaluated combinations; it is
not claimed to be a proven global optimum.
"""
from __future__ import annotations

from app.config import COST_ASSUMPTIONS
from app.engine.remediation import RemediationContext

PENALTY_PER_VIOLATION = 1_000_000.0
PENALTY_PER_MAGNITUDE = 50_000.0
DEFAULT_BEAM_WIDTH = 4
DEFAULT_MAX_DEPTH = 4
MAX_CAPEX_BUDGET_USD = 3_000_000.0


def objective(metrics: dict, candidates: list[dict]) -> dict:
    capex = sum(c["capex_usd"] for c in candidates)
    opex = sum(c["opex_usd_per_year"] for c in candidates)
    loss_cost = (metrics["losses_p_mw"] or 0.0) * COST_ASSUMPTIONS["loss_equivalent_hours_per_year"] \
        * COST_ASSUMPTIONS["energy_price_usd_per_mwh"]
    penalty = PENALTY_PER_VIOLATION * metrics["hard_count"] + PENALTY_PER_MAGNITUDE * metrics["violation_magnitude"]
    if not metrics["converged"]:
        penalty += 10 * PENALTY_PER_VIOLATION
    annualized = COST_ASSUMPTIONS["annuity_factor"] * capex
    return {"capex_usd": round(capex, 0), "capex_annualized_usd": round(annualized, 0), "opex_usd_per_year": round(opex, 0),
            "losses_cost_usd_per_year": round(loss_cost, 0), "violation_penalty": round(penalty, 0),
            "total": round(annualized + opex + loss_cost + penalty, 0)}


def _plan_record(ctx: RemediationContext, plan: tuple[str, ...], by_id: dict[str, dict]) -> dict:
    cands = [by_id[c] for c in plan]
    interventions = [i for c in cands for i in c["interventions"]]
    metrics = ctx.evaluate(interventions)
    return {"plan_id": "+".join(plan) or "do-nothing", "candidate_ids": list(plan), "interventions": interventions,
            "objective": objective(metrics, cands), "metrics": metrics,
            "feasible": metrics["converged"] and metrics["hard_count"] == 0}


def beam_search(ctx: RemediationContext, candidates: list[dict], beam_width: int = DEFAULT_BEAM_WIDTH,
                max_depth: int = DEFAULT_MAX_DEPTH, budget_usd: float = MAX_CAPEX_BUDGET_USD,
                seeds: list[tuple[str, ...]] | None = None) -> dict:
    by_id = {c["id"]: c for c in candidates}
    evaluated: dict[tuple[str, ...], dict] = {}

    def score(rec: dict) -> tuple:
        return (rec["objective"]["total"],)

    def ev(plan: tuple[str, ...]) -> dict:
        key = tuple(sorted(plan))
        if key not in evaluated:
            evaluated[key] = _plan_record(ctx, key, by_id)
        return evaluated[key]

    base = ev(())
    singles = [ev((c["id"],)) for c in candidates]
    seeded = [ev(tuple(seed)) for seed in (seeds or []) if seed and all(c in by_id for c in seed)]
    beam = sorted([r for r in singles + seeded if not r["feasible"]], key=score)[:beam_width]
    for _ in range(max_depth - 1):
        feasible_totals = [r["objective"]["total"] for r in evaluated.values() if r["feasible"] and r["candidate_ids"]]
        best_feasible = min(feasible_totals, default=float("inf"))
        # Expand only partial plans whose penalty-free cost can still beat the best feasible plan.
        frontier = [r for r in beam if not r["feasible"] and
                    r["objective"]["total"] - r["objective"]["violation_penalty"] < best_feasible]
        if not frontier:
            break
        expansions = []
        for rec in frontier:
            used = {by_id[c]["conflict_key"] for c in rec["candidate_ids"]}
            for cand in candidates:
                if cand["conflict_key"] in used:
                    continue
                plan = tuple(rec["candidate_ids"]) + (cand["id"],)
                if sum(by_id[c]["capex_usd"] for c in plan) > budget_usd:
                    continue
                expansions.append(ev(plan))
        if not expansions:
            break
        beam = sorted({r["plan_id"]: r for r in frontier + expansions if not r["feasible"]}.values(),
                      key=score)[:beam_width]
    ranked = sorted(evaluated.values(), key=score)
    feasible = [r for r in ranked if r["feasible"] and r["candidate_ids"]]
    return {"baseline": base, "ranked": ranked, "feasible": feasible, "best": feasible[0] if feasible else ranked[0],
            "plans_evaluated": len(evaluated), "power_flows_run": ctx.power_flows,
            "seeded_from_memory": [r["plan_id"] for r in seeded],
            "method": f"beam search (width {beam_width}, depth <= {max_depth}) over {len(candidates)} sized candidates; "
                      f"objective = annuity x capex + losses + opex + violation penalties; AC power flow at peak for "
                      f"every plan, plus light load for every plan that passes at peak; partial plans are expanded "
                      f"while their cost can still beat the best feasible plan",
            "optimality_claim": "best of the evaluated combinations (heuristic search, not a proven global optimum)"}


def tradeoff_summary(plan: dict, baseline: dict) -> dict:
    m, b = plan["metrics"], baseline["metrics"]

    def delta(key: str) -> float | None:
        if m.get(key) is None or b.get(key) is None:
            return None
        return round(m[key] - b[key], 5)

    return {"feasible": plan["feasible"], "residual_violations": m["hard_count"],
            "min_vm_change_pu": delta("min_vm_pu"), "max_loading_change_percent": delta("max_loading_percent"),
            "losses_change_mw": delta("losses_p_mw"), "grid_pf_change": delta("grid_pf"),
            "light_load_max_vm_pu": m.get("light_max_vm_pu"), "capex_usd": plan["objective"]["capex_usd"],
            "opex_usd_per_year": plan["objective"]["opex_usd_per_year"]}
