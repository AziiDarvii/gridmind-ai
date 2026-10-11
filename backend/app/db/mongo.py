"""MongoDB client, collection names, JSON-schema validators and indexes."""
from __future__ import annotations

import logging
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, OperationFailure

from app.config import SETTINGS

log = logging.getLogger(__name__)

COLLECTIONS = (
    "circuits",
    "equipment",
    "measurements",
    "simulation_scenarios",
    "simulation_results",
    "detected_faults",
    "recommendations",
    "agent_runs",
    "agent_memory",
    "data_center_profiles",
    "optimization_runs",
    "audit_logs",
)

_REQUIRED = {
    "circuits": ["_id", "name", "created_at"],
    "equipment": ["_id", "circuit_id", "eq_id", "type", "params"],
    "simulation_scenarios": ["_id", "circuit_id", "name", "interventions"],
    "simulation_results": ["_id", "scenario_id", "circuit_id", "converged", "created_at"],
    "detected_faults": ["_id", "circuit_id", "result_id", "type", "equipment_id", "severity"],
    "recommendations": ["_id", "run_id", "circuit_id", "interventions", "status"],
    "agent_runs": ["_id", "run_id", "agent", "status"],
    "agent_memory": ["_id", "signature", "intervention_type", "verified"],
    "optimization_runs": ["_id", "circuit_id", "scenario_id", "status"],
}

_INDEXES = {
    "equipment": [[("circuit_id", ASCENDING), ("type", ASCENDING)], [("circuit_id", ASCENDING), ("eq_id", ASCENDING)]],
    "simulation_scenarios": [[("circuit_id", ASCENDING), ("created_at", DESCENDING)]],
    "simulation_results": [[("scenario_id", ASCENDING), ("created_at", DESCENDING)], [("circuit_id", ASCENDING)]],
    "detected_faults": [
        [("circuit_id", ASCENDING), ("type", ASCENDING)],
        [("result_id", ASCENDING)],
        [("equipment_id", ASCENDING)],
        [("run_id", ASCENDING), ("status", ASCENDING)],
        [("detected_at", DESCENDING)],
    ],
    "recommendations": [[("run_id", ASCENDING), ("rank", ASCENDING)], [("circuit_id", ASCENDING), ("verified", ASCENDING)]],
    "agent_runs": [[("run_id", ASCENDING), ("started_at", ASCENDING)]],
    "agent_memory": [[("signature", ASCENDING)], [("violation_type", ASCENDING), ("verified", ASCENDING)]],
    "measurements": [[("circuit_id", ASCENDING), ("kind", ASCENDING)]],
    "optimization_runs": [[("circuit_id", ASCENDING), ("created_at", DESCENDING)], [("status", ASCENDING)]],
    "audit_logs": [[("timestamp", DESCENDING)], [("entity_id", ASCENDING)]],
    "data_center_profiles": [[("circuit_id", ASCENDING)]],
}


@lru_cache(maxsize=4)
def _client(uri: str) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=15000, appname="gridmind-ai", tz_aware=True)


def get_db(name: str | None = None) -> Database:
    return _client(SETTINGS.mongodb_uri)[name or SETTINGS.mongodb_db]


def _validator(required: list[str]) -> dict:
    return {"$jsonSchema": {"bsonType": "object", "required": required}}


def init_db(db: Database) -> None:
    """Create collections with validators (idempotent) and ensure indexes."""
    existing = {c["name"]: c.get("options", {}) for c in db.list_collections()}
    for name in COLLECTIONS:
        required = _REQUIRED.get(name)
        if name not in existing:
            try:
                if required:
                    db.create_collection(name, validator=_validator(required))
                else:
                    db.create_collection(name)
            except CollectionInvalid:
                pass
        elif required and existing[name].get("validator") != _validator(required):
            try:
                db.command("collMod", name, validator=_validator(required))
            except OperationFailure as exc:  # app users usually lack collMod privileges
                log.warning("Validator for %s not updated (%s); existing validator kept", name, exc.code)
        for keys in _INDEXES.get(name, []):
            db[name].create_index(keys)
