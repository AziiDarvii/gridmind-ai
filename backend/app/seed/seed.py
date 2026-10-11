"""Seed MongoDB with the reproducible demo network, public data and scenarios.

Usage:
    python -m app.seed.seed                 # (re)load circuit, data, scenarios; keep run history & memory
    python -m app.seed.seed --reset-history # also clear runs, results, faults, recommendations and memory
"""
from __future__ import annotations

import argparse
import logging

from pymongo.database import Database

from app.db.mongo import get_db, init_db
from app.db.repositories import Repository
from app.engine.topology import compute_layout
from app.seed.cigre_dc import CIRCUIT_ID, build_cigre_dc_circuit, demo_scenarios
from app.seed.public_data import datacenter_profile, measurement_docs

log = logging.getLogger(__name__)

HISTORY_COLLECTIONS = ("simulation_results", "detected_faults", "recommendations", "agent_runs", "agent_memory",
                       "optimization_runs", "audit_logs")


def seed(db: Database, reset_history: bool = False) -> dict:
    init_db(db)
    repo = Repository(db)
    if reset_history:
        for name in HISTORY_COLLECTIONS:
            db[name].delete_many({})
        db.simulation_scenarios.delete_many({"kind": {"$ne": "base"}})
    circuit, equipment = build_cigre_dc_circuit()
    layout = compute_layout(equipment)
    for eq in equipment:
        eq["layout"] = layout.get(eq["eq_id"])
    repo.replace_circuit(circuit, equipment)
    measurements = measurement_docs(CIRCUIT_ID)
    for doc in measurements:
        repo.upsert_measurement(doc)
    repo.upsert_dc_profile(datacenter_profile(CIRCUIT_ID))
    for scn in demo_scenarios():
        repo.create_scenario(CIRCUIT_ID, scn["name"], [], kind=scn["kind"], conditions=scn["conditions"],
                             description=scn["description"], scenario_id=scn["_id"])
    repo.audit("seed", CIRCUIT_ID, details={"equipment": len(equipment), "reset_history": reset_history})
    return {"circuit_id": CIRCUIT_ID, "equipment": len(equipment), "measurements": len(measurements),
            "scenarios": len(demo_scenarios()),
            "public_data_fallbacks": [m["_id"] for m in measurements if m.get("fallback")]}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Seed GridMind AI demo data into MongoDB")
    parser.add_argument("--reset-history", action="store_true", help="clear runs, results, faults and agent memory")
    args = parser.parse_args()
    summary = seed(get_db(), reset_history=args.reset_history)
    log.info("Seed complete: %s", summary)


if __name__ == "__main__":
    main()
