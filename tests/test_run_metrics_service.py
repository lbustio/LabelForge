"""Pruebas del coordinador de eventos independiente de Flask."""

import threading
import unittest

from labelforge_core.application.run_metrics_service import RunMetricsService
from labelforge_core.infrastructure.execution import InMemoryRunMetrics


def empty_stats():
    return {
        "run_id": None, "active": False, "finished": False,
        "source_file": None, "output_path": None, "input_file": None,
        "config_file": None, "started_at": None, "updated_at": None,
        "total_rows": 0, "dataset_total_rows": 0, "eligible_rows": 0,
        "processed_rows": 0, "successful_rows": 0, "failed_rows": 0,
        "skipped_rows": 0, "completed_before_start": 0,
        "total_tokens_global": 0, "total_latency_s": 0.0,
        "execution_order": "row_major", "checkpoint_every": 10,
        "arbiter_count": 0, "arbiter_work_total": 0,
        "arbiter_work_completed": 0, "arbiter_work_before_start": 0,
        "arbiters": {}, "principles": [], "principle_counts": {},
        "last_claim": None, "_claim_statuses": {}, "_queued_indices": set(),
        "_arbiter_result_keys": set(), "current_arbiter_id": None,
        "current_pass_completed": 0, "current_pass_work_total": 0,
    }


class TestRunMetricsService(unittest.TestCase):
    def build_service(self, **overrides):
        store = overrides.pop("store", InMemoryRunMetrics(empty_stats))
        captured = {"performance": [], "incidents": [], "resolved": []}
        service = RunMetricsService(
            store=store,
            empty_factory=empty_stats,
            public_mapper=lambda stats: stats,
            checkpoint_every_provider=overrides.pop("checkpoint_every_provider", lambda: 8),
            performance_sink=overrides.pop("performance_sink", lambda **values: captured["performance"].append(values)),
            incident_sink=lambda key, **values: captured["incidents"].append((key, values)),
            incident_resolver=lambda run_id, row: captured["resolved"].append((run_id, row)),
            timestamp_provider=lambda: "2026-01-01T00:00:00",
        )
        return service, store, captured

    def test_start_uses_injected_configuration_and_effect_sink(self):
        service, _, captured = self.build_service()
        result = service.apply({"event": "start", "run_id": "r1", "total_rows": 3})
        self.assertEqual(result["checkpoint_every"], 8)
        self.assertEqual(captured["performance"][0]["run_id"], "r1")

    def test_configuration_failure_falls_back_without_breaking_event(self):
        def fail():
            raise RuntimeError("config unavailable")
        service, _, _ = self.build_service(checkpoint_every_provider=fail)
        result = service.apply({"event": "start", "run_id": "r1", "total_rows": 1})
        self.assertEqual(result["checkpoint_every"], 10)

    def test_effect_sinks_run_after_metrics_lock_is_released(self):
        store = InMemoryRunMetrics(empty_stats)
        snapshot_completed = []

        def performance_sink(**_values):
            thread = threading.Thread(target=lambda: (store.snapshot(), snapshot_completed.append(True)))
            thread.start()
            thread.join(timeout=1)

        service, _, _ = self.build_service(store=store, performance_sink=performance_sink)
        service.apply({"event": "start", "run_id": "r1", "total_rows": 1})
        self.assertEqual(snapshot_completed, [True])

    def test_failed_event_persists_declared_incident(self):
        service, _, captured = self.build_service()
        service.apply({"event": "start", "run_id": "r1", "total_rows": 1})
        service.apply({"event": "claim_failed", "row_index": 0, "queued": True})
        self.assertEqual(captured["incidents"][0][0], "r1:row:0:arbiter:unknown")


if __name__ == "__main__":
    unittest.main()
