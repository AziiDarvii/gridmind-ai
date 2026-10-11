"""REST API. Routers stay thin: persistence via Repository, physics via engine modules."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents.orchestrator import AGENT_GRAPH, claim_for_resume, create_run, start_background
from app.api.schemas import RunRequest, ScenarioCreate, SimulateRequest, ThresholdUpdate, WhatIfRequest
from app.config import COST_ASSUMPTIONS, DEFAULT_THRESHOLDS, SETTINGS
from app.db.mongo import get_db
from app.db.repositories import Repository
from app.engine.comparison import compare_results
from app.engine.datacenter import dc_hourly
from app.engine.simulate import simulate
from app.engine.topology import compute_layout, connected_equipment, path_from_source
from app.engine.violations import UNSUPPORTED_STUDIES, merged_thresholds

router = APIRouter(prefix="/api")
DC_CATEGORIES = ("dc_it", "dc_training", "dc_inference", "dc_cooling", "dc_ups", "dc_aux")
HISTORY_LIMIT = 30


def get_repo() -> Repository:
    return Repository(get_db())


Repo = Annotated[Repository, Depends(get_repo)]


def _require(doc: dict | None, what: str) -> dict:
    if doc is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return doc


@router.get("/health")
def health(repo: Repo) -> dict:
    repo.db.command("ping")
    return {"status": "ok", "database": repo.db.name, "llm_enabled": SETTINGS.llm_enabled,
            "llm_model": SETTINGS.anthropic_model if SETTINGS.llm_enabled else None}


@router.get("/config")
def config() -> dict:
    return {"default_thresholds": DEFAULT_THRESHOLDS, "cost_assumptions": COST_ASSUMPTIONS,
            "unsupported_studies": UNSUPPORTED_STUDIES, "agent_graph": AGENT_GRAPH,
            "llm_enabled": SETTINGS.llm_enabled, "max_iterations": SETTINGS.max_iterations}


# ---- circuits -----------------------------------------------------------------
@router.get("/circuits")
def list_circuits(repo: Repo) -> list[dict]:
    return repo.list_circuits()


@router.get("/circuits/{circuit_id}")
def get_circuit(circuit_id: str, repo: Repo) -> dict:
    circuit = _require(repo.get_circuit(circuit_id), "circuit")
    equipment = repo.get_equipment(circuit_id)
    layout = compute_layout(equipment)
    for eq in equipment:
        eq["layout"] = layout.get(eq["eq_id"], eq.get("layout"))
    return {**circuit, "equipment": equipment, "effective_thresholds": merged_thresholds(circuit.get("thresholds"))}


@router.put("/circuits/{circuit_id}/thresholds")
def update_thresholds(circuit_id: str, body: ThresholdUpdate, repo: Repo) -> dict:
    _require(repo.get_circuit(circuit_id), "circuit")
    repo.update_thresholds(circuit_id, body.model_dump(exclude_none=True))
    return merged_thresholds(repo.get_circuit(circuit_id).get("thresholds"))


@router.get("/circuits/{circuit_id}/scenarios")
def list_scenarios(circuit_id: str, repo: Repo) -> list[dict]:
    return repo.list_scenarios(circuit_id)


@router.post("/circuits/{circuit_id}/scenarios", status_code=201)
def create_scenario(circuit_id: str, body: ScenarioCreate, repo: Repo) -> dict:
    _require(repo.get_circuit(circuit_id), "circuit")
    doc = repo.create_scenario(circuit_id, body.name, [i.model_dump(exclude_none=True) for i in body.interventions],
                               kind="user", conditions=body.conditions.model_dump(exclude_none=True),
                               description=body.description)
    repo.audit("scenario.create", doc["_id"], actor="user")
    return doc


@router.get("/circuits/{circuit_id}/components/{eq_id}")
def component_detail(circuit_id: str, eq_id: str, repo: Repo, result_id: str | None = None) -> dict:
    equipment = repo.get_equipment(circuit_id)
    eq = next((e for e in equipment if e["eq_id"] == eq_id), None)
    result = repo.get_result(result_id) if result_id else None
    proposed = None
    if eq is None and result:
        proposed = next((p for p in result.get("proposed_equipment", []) if p["eq_id"] == eq_id), None)
    if eq is None and proposed is None:
        raise HTTPException(status_code=404, detail="component not found")
    values = _component_values(result, eq_id) if result else None
    history = []
    for doc in repo.db.simulation_results.find({"circuit_id": circuit_id, "converged": True},
                                               {"buses": 1, "branches": 1, "loads": 1, "kind": 1, "label": 1,
                                                "scenario_id": 1, "created_at": 1}).sort("created_at", -1).limit(HISTORY_LIMIT):
        v = _component_values(doc, eq_id)
        if v:
            history.append({"result_id": doc["_id"], "kind": doc.get("kind"), "label": doc.get("label"),
                            "scenario_id": doc.get("scenario_id"), "created_at": doc["created_at"], "values": v})
    faults, _ = repo.query_faults(circuit_id=circuit_id, equipment_id=eq_id, page_size=50)
    recs = list(repo.db.recommendations.find({"circuit_id": circuit_id, "interventions": {"$elemMatch": {"$or": [
        {"bus": eq_id}, {"transformer": eq_id}, {"line": eq_id}, {"switch": eq_id}, {"load": eq_id}, {"storage": eq_id}]}}},
        {"title": 1, "rank": 1, "verified": 1, "run_id": 1, "status": 1}).sort("created_at", -1).limit(10))
    supply_path = path_from_source(equipment, eq_id) if eq and eq["type"] == "bus" else []
    return {"equipment": eq or {**proposed, "proposed": True}, "values": values, "history": history,
            "faults": faults, "recommendations": recs, "connected": connected_equipment(equipment, eq_id),
            "supply_path": supply_path, "status": (result or {}).get("component_status", {}).get(eq_id)}


def _component_values(result: dict, eq_id: str) -> dict | None:
    for table in ("buses", "branches", "loads", "capacitors", "sgens", "storage", "ext_grid"):
        for row in result.get(table, []) or []:
            if row["id"] == eq_id:
                return {**row, "table": table}
    return None


# ---- simulation ---------------------------------------------------------------------
@router.post("/scenarios/{scenario_id}/simulate")
def simulate_scenario(scenario_id: str, body: SimulateRequest, repo: Repo) -> dict:
    scenario = _require(repo.get_scenario(scenario_id), "scenario")
    circuit = repo.get_circuit(scenario["circuit_id"]) or {}
    equipment = repo.get_equipment(scenario["circuit_id"])
    interventions = scenario.get("interventions", []) + [i.model_dump(exclude_none=True) for i in body.interventions]
    out = simulate(equipment, interventions, scenario.get("conditions", {}), circuit.get("thresholds"))
    doc = repo.save_result({"scenario_id": scenario_id, "circuit_id": scenario["circuit_id"], "kind": "manual",
                            "label": body.label or scenario["name"], "interventions": interventions,
                            "conditions": scenario.get("conditions", {}), **out["result"],
                            "violations": out["violations"], "component_status": out["status"]})
    repo.audit("simulate", doc["_id"], actor="user", details={"scenario_id": scenario_id})
    return doc


@router.get("/scenarios/{scenario_id}/latest-result")
def latest_result(scenario_id: str, repo: Repo, kind: str | None = None) -> dict:
    query: dict = {"scenario_id": scenario_id}
    if kind:
        query["kind"] = kind
    doc = repo.db.simulation_results.find_one(query, sort=[("created_at", -1)])
    return _require(doc, "result")


@router.get("/results/{result_id}")
def get_result(result_id: str, repo: Repo) -> dict:
    return _require(repo.get_result(result_id), "result")


@router.get("/compare")
def compare(before: str, after: str, repo: Repo) -> dict:
    b = _require(repo.get_result(before), "before result")
    a = _require(repo.get_result(after), "after result")
    if b["circuit_id"] != a["circuit_id"]:
        raise HTTPException(status_code=400, detail="results belong to different circuits")
    circuit = repo.get_circuit(b["circuit_id"]) or {}
    bv = [v for v in b.get("violations", []) if v.get("condition", "peak") == "peak"]
    av = [v for v in a.get("violations", []) if v.get("condition", "peak") == "peak"]
    return {"before_id": before, "after_id": after, "before_label": b.get("label"), "after_label": a.get("label"),
            "interventions": a.get("interventions", []), "proposed_equipment": a.get("proposed_equipment", []),
            **compare_results(b, a, bv, av, circuit.get("thresholds"))}


# ---- faults ---------------------------------------------------------------------------
@router.get("/faults")
def list_faults(repo: Repo, circuit_id: str | None = None, run_id: str | None = None, result_id: str | None = None,
                type: str | None = None, status: str | None = None, equipment_id: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500)) -> dict:
    rows, total = repo.query_faults(circuit_id=circuit_id, run_id=run_id, result_id=result_id, fault_type=type,
                                    status=status, equipment_id=equipment_id, page=page, page_size=page_size)
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


# ---- agent runs -----------------------------------------------------------------------
@router.post("/runs", status_code=202)
def start_run(body: RunRequest, repo: Repo) -> dict:
    _require(repo.get_circuit(body.circuit_id), "circuit")
    _require(repo.get_scenario(body.scenario_id), "scenario")
    run = create_run(repo, body.circuit_id, body.scenario_id, body.max_iterations)
    start_background(get_repo, run["_id"])
    return run


@router.get("/runs")
def list_runs(repo: Repo, circuit_id: str | None = None, status: str | None = None) -> list[dict]:
    return repo.list_runs(circuit_id, status)


@router.get("/runs/{run_id}")
def get_run(run_id: str, repo: Repo) -> dict:
    return _require(repo.get_run(run_id), "run")


@router.get("/runs/{run_id}/tasks")
def run_tasks(run_id: str, repo: Repo) -> list[dict]:
    return repo.list_agent_tasks(run_id)


@router.get("/runs/{run_id}/recommendations")
def run_recommendations(run_id: str, repo: Repo) -> list[dict]:
    return repo.list_recommendations(run_id)


@router.post("/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: str, repo: Repo) -> dict:
    run = _require(repo.get_run(run_id), "run")
    if not claim_for_resume(repo, run_id):
        raise HTTPException(status_code=409, detail=f"run is {run['status']}; only interrupted/failed runs resume")
    start_background(get_repo, run_id)
    return repo.get_run(run_id)


# ---- data center ----------------------------------------------------------------------
@router.get("/circuits/{circuit_id}/datacenter")
def datacenter(circuit_id: str, repo: Repo) -> dict:
    profile = _require(repo.get_dc_profile(circuit_id), "data-center profile")
    meas = {m["category"]: m for m in repo.get_measurements(circuit_id)}
    hourly = dc_hourly(profile, meas["temperature"]["values"])
    return {"profile": profile, "measurements": list(meas.values()), "hourly": hourly}


@router.post("/circuits/{circuit_id}/datacenter/whatif")
def datacenter_whatif(circuit_id: str, body: WhatIfRequest, repo: Repo) -> dict:
    scenario = _require(repo.get_scenario(body.scenario_id), "scenario")
    circuit = repo.get_circuit(circuit_id) or {}
    equipment = repo.get_equipment(circuit_id)
    base_cond = scenario.get("conditions", {})
    scale = dict(base_cond.get("category_scale", {}))
    for cat in DC_CATEGORIES:
        scale[cat] = scale.get(cat, 1.0) * body.dc_scale
    if body.training_scale is not None:
        scale["dc_training"] = scale.get("dc_training", 1.0) * body.training_scale
    cond = {**base_cond, "category_scale": scale}
    interventions = [i.model_dump(exclude_none=True) for i in body.interventions]
    base = simulate(equipment, interventions, base_cond, circuit.get("thresholds"))
    what = simulate(equipment, interventions, cond, circuit.get("thresholds"))
    comparison = compare_results(base["result"], what["result"], base["violations"], what["violations"],
                                 circuit.get("thresholds"))
    doc = repo.save_result({"scenario_id": body.scenario_id, "circuit_id": circuit_id, "kind": "whatif",
                            "label": f"DC x{body.dc_scale:.2f}" + (f", training x{body.training_scale:.2f}" if body.training_scale else ""),
                            "interventions": interventions, "conditions": cond, **what["result"],
                            "violations": what["violations"], "component_status": what["status"]})
    return {"result_id": doc["_id"], "base_totals": base["result"].get("totals"), "whatif_totals": what["result"].get("totals"),
            "violations": what["violations"], "comparison": comparison}


# ---- memory & audit -------------------------------------------------------------------
@router.get("/memory")
def memory(repo: Repo, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500)) -> list[dict]:
    return repo.list_memory(page, page_size)


@router.get("/audit")
def audit(repo: Repo, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)) -> list[dict]:
    return repo.list_audit(page, page_size)
