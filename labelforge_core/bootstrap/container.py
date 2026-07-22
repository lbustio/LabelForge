"""Composition root de dependencias independientes de una interfaz."""

from __future__ import annotations

from dataclasses import dataclass

from labelforge_core.application.ports import (
    RunMetricsStore,
    RunRepository,
    RunSupervisor,
)
from labelforge_core.application.run_metrics_state import empty_run_metrics
from labelforge_core.infrastructure.execution import (
    InMemoryRunMetrics,
    InMemoryRunSupervisor,
)
from labelforge_core.infrastructure.persistence import JsonRunRepository

from .settings import AppSettings


@dataclass(slots=True)
class AppContainer:
    settings: AppSettings
    run_repository: RunRepository
    run_supervisor: RunSupervisor
    run_metrics: RunMetricsStore


def build_container(
    settings: AppSettings,
    *,
    run_repository: RunRepository | None = None,
    run_supervisor: RunSupervisor | None = None,
    run_metrics: RunMetricsStore | None = None,
) -> AppContainer:
    """Construye adaptadores sin abrir archivos ni preparar el runtime."""
    return AppContainer(
        settings=settings,
        run_repository=run_repository or JsonRunRepository(settings.paths.run_records),
        run_supervisor=run_supervisor or InMemoryRunSupervisor(),
        run_metrics=run_metrics or InMemoryRunMetrics(empty_run_metrics),
    )
