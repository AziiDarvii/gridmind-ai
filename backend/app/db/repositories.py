"""Repository layer: every MongoDB read/write in the application goes through here."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import math

import numpy as np
from pymongo import DESCENDING
from pymongo.database import Database

MAX_PAGE_SIZE = 500


def sanitize(obj: Any) -> Any:
    """Convert numpy scalars/arrays and NaN to BSON-safe Python values (returns a new object)."""
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [sanitize(v) for v in obj.tolist()]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return None if math.isnan(value) or math.isinf(value) else value
    return obj


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _page(cursor, page: int, page_size: int) -> list[dict]:
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    return list(cursor.skip(max(0, page - 1) * size).limit(size))


class Repository:
    """Thin, explicit repository over the GridMind collections."""

    def __init__(self, db: Database):
        self.db = db

    # ---- audit -----------------------------------------------------------
    def audit(self, action: str, entity_id: str, actor: str = "system", details: dict | None = None) -> None:
        self.db.audit_logs.insert_one(
            {"_id": new_id("aud"), "timestamp": now_utc(), "action": action, "entity_id": entity_id,
             "actor": actor, "details": details or {}}
        )

    def list_audit(self, page: int = 1, page_size: int = 50) -> list[dict]:
        return _page(self.db.audit_logs.find().sort("timestamp", DESCENDING), page, page_size)

    # ---- circuits & equipment -------------------------------------------
    def list_circuits(self) -> list[dict]:
        return list(self.db.circuits.find().sort("created_at", DESCENDING))

    def get_circuit(self, circuit_id: str) -> dict | None:
        return self.db.circuits.find_one({"_id": circuit_id})

    def get_equipment(self, circuit_id: str) -> list[dict]:
        return list(self.db.equipment.find({"circuit_id": circuit_id}).sort("eq_id"))

    def get_equipment_item(self, circuit_id: str, eq_id: str) -> dict | None:
        return self.db.equipment.find_one({"circuit_id": circuit_id, "eq_id": eq_id})

    def replace_circuit(self, circuit: dict, equipment: list[dict]) -> None:
        cid = circuit["_id"]
        self.db.circuits.replace_one({"_id": cid}, circuit, upsert=True)
        self.db.equipment.delete_many({"circuit_id": cid})
        if equipment:
            self.db.equipment.insert_many(equipment)
        self.audit("circuit.replace", cid, details={"equipment_count": len(equipment)})

    def update_thresholds(self, circuit_id: str, thresholds: dict) -> None:
        self.db.circuits.update_one({"_id": circuit_id}, {"$set": {"thresholds": thresholds, "updated_at": now_utc()}})
        self.audit("circuit.thresholds", circuit_id, actor="user", details=thresholds)

    # ---- measurements & data-center profiles ----------------------------
    def upsert_measurement(self, doc: dict) -> None:
        self.db.measurements.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def get_measurements(self, circuit_id: str, kind: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"circuit_id": circuit_id}
        if kind:
            query["kind"] = kind
        return list(self.db.measurements.find(query))

    def upsert_dc_profile(self, doc: dict) -> None:
        self.db.data_center_profiles.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def get_dc_profile(self, circuit_id: str) -> dict | None:
        return self.db.data_center_profiles.find_one({"circuit_id": circuit_id})

    # ---- scenarios & results --------------------------------------------
    def create_scenario(self, circuit_id: str, name: str, interventions: list[dict], *, kind: str = "user",
                        conditions: dict | None = None, description: str = "", parent_id: str | None = None,
                        scenario_id: str | None = None) -> dict:
        doc = {"_id": scenario_id or new_id("scn"), "circuit_id": circuit_id, "name": name, "kind": kind,
               "description": description, "interventions": interventions, "conditions": conditions or {},
               "parent_id": parent_id, "created_at": now_utc()}
        self.db.simulation_scenarios.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return doc

    def get_scenario(self, scenario_id: str) -> dict | None:
        return self.db.simulation_scenarios.find_one({"_id": scenario_id})

    def list_scenarios(self, circuit_id: str, kinds: Iterable[str] | None = None) -> list[dict]:
        query: dict[str, Any] = {"circuit_id": circuit_id}
        if kinds:
            query["kind"] = {"$in": list(kinds)}
        return list(self.db.simulation_scenarios.find(query).sort("created_at", 1))

    def save_result(self, result: dict) -> dict:
        doc = {**result, "_id": result.get("_id") or new_id("res"), "created_at": now_utc()}
        doc = sanitize(doc)
        self.db.simulation_results.insert_one(doc)
        return doc

    def get_result(self, result_id: str) -> dict | None:
        return self.db.simulation_results.find_one({"_id": result_id})

    def latest_result(self, scenario_id: str) -> dict | None:
        return self.db.simulation_results.find_one({"scenario_id": scenario_id}, sort=[("created_at", DESCENDING)])

    # ---- faults -----------------------------------------------------------
    def save_faults(self, faults: list[dict]) -> None:
        if faults:
            self.db.detected_faults.insert_many(sanitize(faults))

    def query_faults(self, *, circuit_id: str | None = None, result_id: str | None = None,
                     run_id: str | None = None, fault_type: str | None = None, equipment_id: str | None = None,
                     status: str | None = None, page: int = 1, page_size: int = 100) -> tuple[list[dict], int]:
        query = {k: v for k, v in {"circuit_id": circuit_id, "result_id": result_id, "run_id": run_id,
                                   "type": fault_type, "equipment_id": equipment_id, "status": status}.items() if v}
        total = self.db.detected_faults.count_documents(query)
        rows = _page(self.db.detected_faults.find(query).sort("detected_at", DESCENDING), page, page_size)
        return rows, total

    def update_fault(self, fault_id: str, fields: dict) -> None:
        self.db.detected_faults.update_one({"_id": fault_id}, {"$set": sanitize(fields)})

    # ---- recommendations ---------------------------------------------------
    def save_recommendations(self, recs: list[dict]) -> None:
        for rec in recs:
            self.db.recommendations.replace_one({"_id": rec["_id"]}, sanitize(rec), upsert=True)

    def list_recommendations(self, run_id: str) -> list[dict]:
        return list(self.db.recommendations.find({"run_id": run_id}).sort("rank", 1))

    # ---- workflow runs & agent tasks --------------------------------------
    def create_run(self, doc: dict) -> dict:
        self.db.optimization_runs.insert_one(sanitize(doc))
        return doc

    def update_run(self, run_id: str, fields: dict) -> None:
        self.db.optimization_runs.update_one({"_id": run_id}, {"$set": {**sanitize(fields), "updated_at": now_utc()}})

    def get_run(self, run_id: str) -> dict | None:
        return self.db.optimization_runs.find_one({"_id": run_id})

    def list_runs(self, circuit_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        query = {k: v for k, v in {"circuit_id": circuit_id, "status": status}.items() if v}
        return list(self.db.optimization_runs.find(query, {"context": 0}).sort("created_at", DESCENDING).limit(limit))

    def save_agent_task(self, task: dict) -> None:
        self.db.agent_runs.replace_one({"_id": task["_id"]}, sanitize(task), upsert=True)

    def list_agent_tasks(self, run_id: str) -> list[dict]:
        return list(self.db.agent_runs.find({"run_id": run_id}).sort("started_at", 1))

    # ---- agent memory ------------------------------------------------------
    def save_memory(self, doc: dict) -> None:
        self.db.agent_memory.replace_one({"_id": doc["_id"]}, sanitize(doc), upsert=True)

    def find_memory(self, violation_types: list[str] | None = None, verified_only: bool = True,
                    limit: int = 50) -> list[dict]:
        query: dict[str, Any] = {}
        if violation_types:
            query["violation_type"] = {"$in": violation_types}
        if verified_only:
            query["verified"] = True
        return list(self.db.agent_memory.find(query).sort("created_at", DESCENDING).limit(limit))

    def list_memory(self, page: int = 1, page_size: int = 100) -> list[dict]:
        return _page(self.db.agent_memory.find().sort("created_at", DESCENDING), page, page_size)
