"""Maquina de estados pura para el ciclo de vida de una ejecucion.

Este modulo no conoce Flask, archivos, SQLite ni procesos. Recibe hechos ya
observados y decide el estado semantico y las acciones validas del run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    IDLE = "idle"
    PREPARED = "prepared"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FAILED = "failed"
    RESUME_INCOMPATIBLE = "resume_incompatible"
    ARTIFACT_MISSING = "artifact_missing"


@dataclass(frozen=True, slots=True)
class RunLifecycleFacts:
    has_run: bool = False
    process_running: bool = False
    stop_requested: bool = False
    persisted_status: str = ""
    pause_kind: str = ""
    pause_reason: str = ""
    finished: bool = False
    failed_rows: int = 0
    exit_code: Any = None
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class RunLifecycleDecision:
    status: RunStatus
    reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ISSUES,
            RunStatus.FAILED,
            RunStatus.ARTIFACT_MISSING,
        }

    @property
    def can_start(self) -> bool:
        return self.status in {
            RunStatus.IDLE,
            RunStatus.PREPARED,
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ISSUES,
            RunStatus.FAILED,
        }

    @property
    def can_resume(self) -> bool:
        return self.status in {RunStatus.PAUSED, RunStatus.INTERRUPTED}

    @property
    def can_stop(self) -> bool:
        return self.status is RunStatus.RUNNING


def derive_run_lifecycle(facts: RunLifecycleFacts) -> RunLifecycleDecision:
    """Deriva un unico estado respetando la precedencia de hechos operativos."""
    reason = facts.pause_reason or facts.last_error

    if facts.process_running and facts.stop_requested:
        return RunLifecycleDecision(RunStatus.STOPPING, reason)
    if facts.process_running:
        return RunLifecycleDecision(RunStatus.RUNNING, reason)
    if facts.pause_kind or facts.persisted_status == RunStatus.PAUSED:
        return RunLifecycleDecision(RunStatus.PAUSED, reason)
    if facts.finished or facts.persisted_status == RunStatus.COMPLETED:
        status = RunStatus.COMPLETED_WITH_ISSUES if facts.failed_rows else RunStatus.COMPLETED
        return RunLifecycleDecision(status, reason)
    if facts.has_run and (facts.last_error or facts.exit_code not in (None, 0)):
        return RunLifecycleDecision(RunStatus.FAILED, reason)
    if facts.has_run and facts.persisted_status == RunStatus.RUNNING:
        return RunLifecycleDecision(
            RunStatus.INTERRUPTED,
            "El estado persistido indica una ejecucion activa, pero ya no existe un proceso asociado.",
        )
    if facts.has_run:
        return RunLifecycleDecision(RunStatus.PREPARED, reason)
    return RunLifecycleDecision(RunStatus.IDLE, reason)
