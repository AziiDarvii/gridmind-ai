"""Workflow orchestrator: dependency graph, bounded recursive loop, persistence and resume.

Graph:
    circuit_modeling -> power_flow -> violation_detection
        -> {root_cause, data_center, learning_retrieve}            (parallel)
        -> loop[i = 1..max_iterations]: corrective_action -> optimization -> verification
        -> learning_store -> engineering_report
The loop stops when verification passes, the iteration budget is exhausted,
no improving action exists, or a hard constraint cannot be met. Nothing is
applied to real equipment: every plan is a recommendation for approval.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.agents.analysis_agents import (CircuitModelingAgent, DataCenterAgent, MemoryRetrievalAgent, PowerFlowAgent,
                                        RootCauseAgent, ViolationDetectionAgent)
from app.agents.base import Agent, RunState, StopWorkflow
from app.agents.solution_agents import (CorrectiveActionAgent, LearningStoreAgent, OptimizationAgent, ReportAgent,
                                        VerificationAgent)
from app.config import SETTINGS
from app.db.repositories import Repository, new_id, now_utc

log = logging.getLogger(__name__)

PIPELINE_HEAD = [CircuitModelingAgent(), PowerFlowAgent(), ViolationDetectionAgent()]
PARALLEL_STAGE = [RootCauseAgent(), DataCenterAgent(), MemoryRetrievalAgent()]
LOOP_STAGE = [CorrectiveActionAgent(), OptimizationAgent(), VerificationAgent()]
PIPELINE_TAIL = [LearningStoreAgent(), ReportAgent()]
AGENT_GRAPH = {
    "circuit_modeling": [], "power_flow": ["circuit_modeling"], "violation_detection": ["power_flow"],
    "root_cause": ["violation_detection"], "data_center": ["violation_detection"],
    "learning_retrieve": ["violation_detection"],
    "corrective_action": ["root_cause", "data_center", "learning_retrieve"], "optimization": ["corrective_action"],
    "verification": ["optimization"], "learning_store": ["verification"], "engineering_report": ["learning_store"],
}

_threads: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def create_run(repo: Repository, circuit_id: str, scenario_id: str, max_iterations: int | None = None) -> dict:
    doc = {"_id": new_id("run"), "circuit_id": circuit_id, "scenario_id": scenario_id, "status": "queued",
           "iteration": 1, "max_iterations": max_iterations or SETTINGS.max_iterations, "completed_steps": [],
           "current_step": None, "stop_reason": None, "context": {}, "agent_graph": AGENT_GRAPH,
           "created_at": now_utc(), "updated_at": now_utc()}
    repo.create_run(doc)
    repo.audit("run.create", doc["_id"], actor="user", details={"scenario_id": scenario_id})
    return doc


def start_background(repo_factory, run_id: str) -> None:
    with _lock:
        thread = _threads.get(run_id)
        if thread and thread.is_alive():
            return
        thread = threading.Thread(target=execute_run, args=(repo_factory(), run_id), name=f"run-{run_id}", daemon=True)
        _threads[run_id] = thread
        thread.start()


def claim_for_resume(repo: Repository, run_id: str) -> bool:
    """Atomically move an interrupted/failed run back to 'queued' (no double resume)."""
    with _lock:
        thread = _threads.get(run_id)
        if thread and thread.is_alive():
            return False
    result = repo.db.optimization_runs.update_one({"_id": run_id, "status": {"$in": ["interrupted", "failed"]}},
                                                  {"$set": {"status": "queued", "updated_at": now_utc()}})
    return result.modified_count == 1


def mark_interrupted(repo: Repository) -> int:
    """On startup: runs left 'running' by a previous process become resumable."""
    result = repo.db.optimization_runs.update_many({"status": {"$in": ["running", "queued"]}},
                                                   {"$set": {"status": "interrupted", "updated_at": now_utc()}})
    return result.modified_count


def _step(state: RunState, run_doc: dict, agent: Agent, step_key: str) -> None:
    if step_key in run_doc["completed_steps"]:
        return
    state.persist(current_step=step_key)
    agent.execute(state, step_key)
    run_doc["completed_steps"].append(step_key)
    state.persist(completed_steps=run_doc["completed_steps"], current_step=None)


def execute_run(repo: Repository, run_id: str) -> None:
    run_doc = repo.get_run(run_id)
    if run_doc is None:
        return
    state = RunState(run_id=run_id, repo=repo, circuit_id=run_doc["circuit_id"], scenario_id=run_doc["scenario_id"],
                     max_iterations=run_doc["max_iterations"], context=run_doc.get("context") or {},
                     iteration=run_doc.get("iteration", 1))
    repo.update_run(run_id, {"status": "running", "started_at": run_doc.get("started_at") or now_utc()})
    try:
        for agent in PIPELINE_HEAD:
            _step(state, run_doc, agent, agent.key)
        pending = [a for a in PARALLEL_STAGE if a.key not in run_doc["completed_steps"]]
        if pending:
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                futures = {pool.submit(a.execute, state, a.key): a for a in pending}
                errors = []
                for future in as_completed(futures):
                    if future.exception() is None:
                        run_doc["completed_steps"].append(futures[future].key)
                    else:
                        errors.append(future.exception())
            state.persist(completed_steps=run_doc["completed_steps"])
            if errors:
                raise errors[0]
        while True:
            for agent in LOOP_STAGE:
                _step(state, run_doc, agent, f"{agent.key}#{state.iteration}")
            decision = state.context.get("loop_decision")
            if decision != "iterate":
                break
            state.iteration += 1
            state.persist(remaining_work=f"iteration {state.iteration}: correct residual violations")
        for agent in PIPELINE_TAIL:
            _step(state, run_doc, agent, agent.key)
        final = (state.context.get("iterations") or [{}])[-1]
        status = "completed"
        reason = "verified" if final.get("verified") else state.context.get("stop_reason", "not verified")
        repo.update_run(run_id, {"status": status, "stop_reason": reason, "verified": bool(final.get("verified")),
                                 "finished_at": now_utc(), "context": state.context, "remaining_work": None})
        repo.audit("run.complete", run_id, details={"verified": bool(final.get("verified")), "reason": reason})
    except StopWorkflow as stop:
        state.persist(status=stop.status, stop_reason=stop.reason, finished_at=now_utc())
        repo.audit("run.stop", run_id, details={"reason": stop.reason})
    except Exception as exc:  # recorded for the UI; the agent task holds the traceback
        log.exception("Run %s failed", run_id)
        state.persist(status="failed", stop_reason=f"{type(exc).__name__}: {exc}", finished_at=now_utc())
        repo.audit("run.fail", run_id, details={"error": str(exc)})
