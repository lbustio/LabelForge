"""Servicio de aplicacion que coordina eventos y efectos del panel de ejecucion."""

from __future__ import annotations

from typing import Callable

from labelforge_core.application.run_metrics_projection import (
    ProjectionEffects,
    apply_arbiter_result_event,
    apply_claim_ok_event,
    apply_incident_event,
    apply_simple_event,
    apply_start_event,
)


class RunMetricsService:
    def __init__(
        self,
        *,
        store,
        empty_factory: Callable[[], dict],
        public_mapper: Callable[[dict], dict],
        checkpoint_every_provider: Callable[[], int],
        performance_sink: Callable[..., object],
        incident_sink: Callable[..., object],
        incident_resolver: Callable[[str, object], object],
        timestamp_provider: Callable[[], str],
    ) -> None:
        self._store = store
        self._empty_factory = empty_factory
        self._public_mapper = public_mapper
        self._checkpoint_every_provider = checkpoint_every_provider
        self._performance_sink = performance_sink
        self._incident_sink = incident_sink
        self._incident_resolver = incident_resolver
        self._timestamp_provider = timestamp_provider

    def apply(self, event: dict) -> dict:
        effects = ProjectionEffects()
        incident = None
        with self._store.edit() as editor:
            stats = editor.stats
            kind = event.get("event")
            stats["updated_at"] = event.get("timestamp") or self._timestamp_provider()

            if kind == "start":
                stats = editor.replace(self._empty_factory())
                try:
                    checkpoint_every = int(self._checkpoint_every_provider())
                except Exception:
                    checkpoint_every = 10
                effects = apply_start_event(
                    stats, event, checkpoint_every=checkpoint_every,
                )
            elif kind == "claim_ok":
                effects = apply_claim_ok_event(stats, event)
            elif kind == "arbiter_result":
                effects = apply_arbiter_result_event(stats, event)
            elif kind in {"claim_failed", "paused"}:
                incident = apply_incident_event(stats, event)
            else:
                apply_simple_event(stats, event)

        # La persistencia puede ser lenta y no debe bloquear snapshots HTTP.
        if incident:
            self._incident_sink(incident.key, **incident.values)
        for performance in effects.performance:
            self._performance_sink(**performance.values)
        for run_id, row_index in effects.resolved_rows:
            self._incident_resolver(run_id, row_index)
        return self._public_mapper(self._store.snapshot())
