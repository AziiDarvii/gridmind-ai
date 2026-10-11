"""Agent base class and the shared, persisted run context."""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from app.db.repositories import Repository, now_utc

log = logging.getLogger(__name__)


class StopWorkflow(Exception):
    """Raised by an agent to stop the workflow with an explicit reason."""

    def __init__(self, reason: str, status: str = "stopped"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass
class RunState:
    """Mutable workflow state; ``context`` is persisted after every agent step."""

    run_id: str
    repo: Repository
    circuit_id: str
    scenario_id: str
    max_iterations: int
    context: dict = field(default_factory=dict)
    iteration: int = 1

    def persist(self, **fields: Any) -> None:
        self.repo.update_run(self.run_id, {"context": self.context, "iteration": self.iteration, **fields})


class Agent:
    key: str = "agent"
    label: str = "Agent"
    description: str = ""

    def task_text(self, state: RunState) -> str:
        return self.description

    def run(self, state: RunState) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def execute(self, state: RunState, step_key: str) -> dict:
        task = {"_id": f"{state.run_id}:{step_key}", "run_id": state.run_id, "agent": self.key, "agent_label": self.label,
                "step": step_key, "iteration": state.iteration, "task": self.task_text(state), "status": "running",
                "started_at": now_utc(), "finished_at": None}
        state.repo.save_agent_task(task)
        t0 = time.perf_counter()
        try:
            output = self.run(state) or {}
            task.update(status="completed", output_summary=output.get("summary", ""),
                        details=output.get("details", {}), verification=output.get("verification"))
            return output
        except StopWorkflow as stop:
            task.update(status="stopped", output_summary=stop.reason)
            raise
        except Exception as exc:  # recorded on the task, re-raised to the orchestrator
            log.exception("Agent %s failed", self.key)
            task.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                        traceback=traceback.format_exc(limit=6))
            raise
        finally:
            task["finished_at"] = now_utc()
            task["duration_s"] = round(time.perf_counter() - t0, 3)
            state.repo.save_agent_task(task)
