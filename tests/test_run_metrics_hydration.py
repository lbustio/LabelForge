"""Pruebas del caso de uso de recuperacion de metricas."""

import unittest

from labelforge_core.application.run_metrics_hydration import (
    RunMetricsHydrator,
    aggregate_checkpoint_resources,
)
from labelforge_core.infrastructure.execution import InMemoryRunMetrics


def empty_stats():
    return {
        "run_id": None, "active": False, "finished": False,
        "arbiters": {}, "total_rows": 0, "dataset_total_rows": 0,
        "eligible_rows": 0, "processed_rows": 0, "successful_rows": 0,
        "failed_rows": 0, "skipped_rows": 0, "completed_before_start": 0,
        "total_tokens_global": 0, "total_latency_s": 0.0,
        "arbiter_work_total": 0, "arbiter_work_completed": 0,
        "arbiter_work_before_start": 0, "_queued_indices": set(),
    }


class TestRunMetricsHydrator(unittest.TestCase):
    def build(self, state, *, partial=None, rows=None, queue=None):
        store = InMemoryRunMetrics(empty_stats)
        hydrator = RunMetricsHydrator(
            store=store,
            empty_factory=empty_stats,
            run_state_loader=lambda: state,
            arbiter_results_loader=lambda: partial or {},
            failed_queue_loader=lambda: queue or [],
            input_rows_loader=lambda _state: (rows or [], []),
            execution_order_provider=lambda: "arbiter_major",
        )
        return hydrator, store

    def test_partial_checkpoints_are_authoritative_for_resources(self):
        state = {
            "run_id": "r1", "status": "paused", "total_rows": 2,
            "eligible_rows": 2, "arbiters": [{"id": "a1"}],
            "summary": {"total_tokens_global": 999, "total_latency_s": 99},
        }
        partial = {"0": {"a1": {
            "_usage": {"prompt_tokens": 3, "completion_tokens": 2},
            "_latency_s": 0.4, "_chunks": 2,
        }}}
        hydrator, store = self.build(state, partial=partial)
        self.assertTrue(hydrator.hydrate())
        restored = store.snapshot()
        self.assertEqual(restored["total_tokens_global"], 5)
        self.assertEqual(restored["arbiters"]["a1"]["model_calls"], 2)

    def test_completed_run_without_partial_keeps_durable_summary(self):
        state = {
            "run_id": "r1", "status": "completed", "total_rows": 2,
            "eligible_rows": 2, "arbiters": [{"id": "a1"}],
            "summary": {"processed_rows": 2, "total_tokens_global": 41},
        }
        hydrator, store = self.build(state)
        hydrator.hydrate()
        restored = store.snapshot()
        self.assertTrue(restored["finished"])
        self.assertEqual(restored["total_tokens_global"], 41)

    def test_missing_eligible_count_is_derived_from_nonempty_text(self):
        state = {
            "run_id": "r1", "status": "paused", "total_rows": 3,
            "text_column": "claim", "arbiters": [{"id": "a1"}],
        }
        hydrator, store = self.build(state, rows=[
            {"claim": "uno"}, {"claim": "  "}, {"claim": "tres"},
        ])
        hydrator.hydrate()
        self.assertEqual(store.snapshot()["eligible_rows"], 2)

    def test_existing_live_projection_is_never_overwritten(self):
        hydrator, store = self.build({"run_id": "disk", "status": "paused"})
        store.replace_if_empty({"run_id": "live"})
        self.assertFalse(hydrator.hydrate())
        self.assertEqual(store.snapshot()["run_id"], "live")

    def test_resource_aggregation_combines_arbiters_and_rows(self):
        resources = aggregate_checkpoint_resources({
            "0": {"a1": {"_usage": {"prompt_tokens": 2, "completion_tokens": 1}}},
            "1": {"a1": {"_usage": {"prompt_tokens": 4, "completion_tokens": 3}}},
        })
        self.assertEqual(resources["total_tokens_global"], 10)
        self.assertEqual(resources["arbiters"]["a1"]["evaluations"], 2)


if __name__ == "__main__":
    unittest.main()
