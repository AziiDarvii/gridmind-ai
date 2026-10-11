"""Integration tests: MongoDB persistence, full agent workflow, independent verification, memory reuse, API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.orchestrator import AGENT_GRAPH, create_run, execute_run
from app.api import routes
from app.db.repositories import Repository

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def first_run(repo: Repository) -> dict:
    run = create_run(repo, "cigre-mv-dc", "scn-base-peak", max_iterations=3)
    execute_run(repo, run["_id"])
    return repo.get_run(run["_id"])


def test_seed_persists_circuit_and_public_data(repo):
    assert repo.get_circuit("cigre-mv-dc")["name"].startswith("CIGRE")
    assert len(repo.get_equipment("cigre-mv-dc")) == 75
    kinds = {m["category"] for m in repo.get_measurements("cigre-mv-dc")}
    assert {"residential", "commercial", "temperature", "pv", "energy_price"} <= kinds
    assert repo.get_dc_profile("cigre-mv-dc")["connection_bus"] == "B14"
    indexes = repo.db.detected_faults.index_information()
    assert any("circuit_id" in str(v["key"]) for v in indexes.values())


def test_workflow_completes_and_is_independently_verified(repo, first_run):
    assert first_run["status"] == "completed"
    assert first_run["verified"] is True
    final = first_run["context"]["iterations"][-1]
    rec = repo.db.recommendations.find_one({"_id": final["verified_recommendation_id"]})
    assert rec["verified"] is True and rec["status"] == "verified"
    verified = repo.get_result(rec["verified_result_id"])
    proposed = repo.get_result(final["proposed_result_id"])
    assert verified["_id"] != proposed["_id"]  # verification re-simulated and stored its own result
    assert verified["kind"] == "verified" and verified["converged"]
    assert not [v for v in verified["violations"] if v["severity"] in ("moderate", "severe")]
    light = repo.get_result(rec["verified_light_result_id"])
    assert light["conditions"]["light_load"] is True


def test_every_agent_ran_in_dependency_order(repo, first_run):
    tasks = repo.list_agent_tasks(first_run["_id"])
    finished = {}
    for t in tasks:
        assert t["status"] == "completed", t
        finished[t["agent"]] = t
    assert set(AGENT_GRAPH) <= set(finished)
    for agent, deps in AGENT_GRAPH.items():
        for dep in deps:
            assert finished[dep]["finished_at"] <= finished[agent]["started_at"], (dep, agent)


def test_faults_are_persisted_and_resolution_tracked(repo, first_run):
    rows, total = repo.query_faults(run_id=first_run["_id"], page_size=200)
    assert total >= 17
    assert {r["status"] for r in rows if r["result_id"] == first_run["context"]["base_result_id"]} == {"resolved"}
    assert all(r.get("root_cause") for r in rows if r["type"] == "undervoltage")


def test_second_scenario_reuses_verified_lessons(repo, first_run):
    assert repo.find_memory(["undervoltage"], verified_only=True)
    run = create_run(repo, "cigre-mv-dc", "scn-ai-surge", max_iterations=3)
    execute_run(repo, run["_id"])
    second = repo.get_run(run["_id"])
    assert second["context"]["memory_hints"], "lessons from the first run must be retrieved"
    retrieve = next(t for t in repo.list_agent_tasks(run["_id"]) if t["agent"] == "learning_retrieve")
    assert "Retrieved" in retrieve["output_summary"] and "no prior history" not in retrieve["output_summary"]
    assert second["status"] == "completed"


def test_api_endpoints(repo, first_run):
    app = routes.router
    from app.main import create_app

    api = create_app()
    api.dependency_overrides[routes.get_repo] = lambda: repo
    try:
        client = TestClient(api)
        circuit = client.get("/api/circuits/cigre-mv-dc").json()
        assert len(circuit["equipment"]) == 75 and all(e.get("layout") for e in circuit["equipment"] if e["type"] == "bus")
        bad = client.post("/api/scenarios/scn-base-peak/simulate", json={"interventions": [{"type": "tap"}]})
        assert bad.status_code == 422
        final = first_run["context"]["iterations"][-1]
        comp = client.get("/api/compare", params={"before": first_run["context"]["base_result_id"],
                                                  "after": final["verified_result_id"]}).json()
        assert comp["comparable"] and comp["violations"]["resolved"]
        detail = client.get("/api/circuits/cigre-mv-dc/components/B11",
                            params={"result_id": first_run["context"]["base_result_id"]}).json()
        assert detail["values"]["vm_pu"] < 0.95 and detail["supply_path"]
        whatif = client.post("/api/circuits/cigre-mv-dc/datacenter/whatif",
                             json={"scenario_id": "scn-base-peak", "dc_scale": 1.3}).json()
        assert whatif["whatif_totals"]["datacenter_p_mw"] > whatif["base_totals"]["datacenter_p_mw"]
        assert client.get("/api/runs/does-not-exist").status_code == 404
    finally:
        api.dependency_overrides.clear()
    assert app is not None
