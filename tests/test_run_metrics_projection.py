"""Pruebas unitarias de las reglas puras de proyeccion del panel."""

import unittest

from labelforge_core.application.run_metrics_projection import (
    apply_resource_summary,
    apply_incident_event,
    apply_arbiter_result_event,
    apply_claim_ok_event,
    apply_start_event,
    apply_simple_event,
    ensure_arbiter,
    set_claim_status,
)


def stats_fixture():
    return {
        "arbiters": {}, "_claim_statuses": {}, "completed_before_start": 2,
        "processed_rows": 2, "successful_rows": 0, "failed_rows": 0,
        "skipped_rows": 0, "total_tokens_global": 0, "total_latency_s": 0.0,
    }


class TestRunMetricsProjection(unittest.TestCase):
    def test_claim_retransmission_is_idempotent(self):
        stats = stats_fixture()
        self.assertTrue(set_claim_status(stats, 4, "ok"))
        self.assertFalse(set_claim_status(stats, 4, "ok"))
        self.assertEqual(stats["successful_rows"], 1)
        self.assertEqual(stats["processed_rows"], 3)

    def test_claim_transition_moves_count_between_buckets(self):
        stats = stats_fixture()
        set_claim_status(stats, 4, "failed")
        set_claim_status(stats, 4, "ok")
        self.assertEqual(stats["failed_rows"], 0)
        self.assertEqual(stats["successful_rows"], 1)
        self.assertEqual(stats["processed_rows"], 3)

    def test_resource_summary_replaces_instead_of_accumulating(self):
        stats = stats_fixture()
        stats["total_tokens_global"] = 999
        apply_resource_summary(stats, {
            "total_tokens_global": 25,
            "total_latency_s": 1.5,
            "arbiters": {"a1": {"total_tokens": 25, "evaluations": 2}},
        })
        self.assertEqual(stats["total_tokens_global"], 25)
        self.assertEqual(stats["arbiters"]["a1"]["calls"], 2)

    def test_arbiter_name_can_be_enriched_without_resetting_metrics(self):
        stats = stats_fixture()
        arbiter = ensure_arbiter(stats, "a1")
        arbiter["total_tokens"] = 10
        ensure_arbiter(stats, "a1", "Arbitro principal")
        self.assertEqual(stats["arbiters"]["a1"]["name"], "Arbitro principal")
        self.assertEqual(stats["arbiters"]["a1"]["total_tokens"], 10)

    def test_pass_event_derives_completed_work_from_pending_position(self):
        stats = stats_fixture()
        stats["eligible_rows"] = 10
        handled = apply_simple_event(stats, {
            "event": "arbiter_claim_start", "arbiter_id": "a1",
            "pass_position": 2, "pass_total": 3,
            "pass_pending_total": 4, "position": 2, "pass_work_total": 10,
        })
        self.assertTrue(handled)
        self.assertEqual(stats["current_pass_completed"], 7)
        self.assertEqual(stats["current_arbiter_id"], "a1")

    def test_complete_event_closes_projection_and_updates_artifact(self):
        stats = stats_fixture()
        stats.update({"active": True, "finished": False, "source_file": None, "output_path": None})
        self.assertTrue(apply_simple_event(stats, {
            "event": "complete", "output_file": "labeled.csv", "output_path": "outputs/labeled.csv",
            "final_summary": {"processed_rows": 8, "successful_rows": 7},
        }))
        self.assertFalse(stats["active"])
        self.assertTrue(stats["finished"])
        self.assertEqual(stats["processed_rows"], 8)
        self.assertEqual(stats["source_file"], "labeled.csv")

    def test_unknown_event_is_not_silently_claimed(self):
        self.assertFalse(apply_simple_event(stats_fixture(), {"event": "future_event"}))

    def test_provisional_failure_queues_row_and_requests_open_incident(self):
        stats = stats_fixture()
        stats.update({"run_id": "run-1", "_queued_indices": set(), "last_claim": None})
        effect = apply_incident_event(stats, {
            "event": "claim_failed", "row_index": 9, "arbiter_id": "a1",
            "queued": True, "reason": "timeout", "reason_code": "timeout",
        })
        self.assertEqual(stats["_queued_indices"], {"9"})
        self.assertEqual(effect.key, "run-1:row:9:arbiter:a1")
        self.assertEqual(effect.values["status"], "open")

    def test_definitive_failure_removes_row_from_retry_queue(self):
        stats = stats_fixture()
        stats.update({"run_id": "run-1", "_queued_indices": {"9"}, "last_claim": None})
        effect = apply_incident_event(stats, {
            "event": "claim_failed", "row_index": 9, "definitive": True,
        })
        self.assertEqual(stats["_queued_indices"], set())
        self.assertEqual(stats["failed_rows"], 1)
        self.assertEqual(effect.values["status"], "final")

    def test_pause_closes_activity_and_describes_incident(self):
        stats = stats_fixture()
        stats.update({
            "run_id": "run-1", "active": True, "finished": False,
            "current_arbiter_id": "a1", "pause_kind": "", "pause_reason": "",
        })
        effect = apply_incident_event(stats, {
            "event": "paused", "pause_kind": "provider", "pause_reason": "sin cuota",
        })
        self.assertFalse(stats["active"])
        self.assertEqual(stats["pause_reason"], "sin cuota")
        self.assertEqual(effect.key, "run-1:pause:provider")

    def test_duplicate_claim_ok_does_not_duplicate_resources_or_consensus(self):
        stats = stats_fixture()
        stats.update({
            "run_id": "run-1", "_queued_indices": set(), "principle_counts": {},
            "execution_order": "row_major", "eligible_rows": 3,
        })
        event = {
            "event": "claim_ok", "row_index": 4, "total_tokens": 12,
            "total_latency_s": 0.5, "consensus": {"P1": 1}, "arbiters": [],
        }
        first = apply_claim_ok_event(stats, event)
        second = apply_claim_ok_event(stats, event)
        self.assertEqual(stats["total_tokens_global"], 12)
        self.assertEqual(stats["principle_counts"]["P1"]["ones"], 1)
        self.assertEqual(len(first.performance), 1)
        self.assertEqual(second.performance, ())

    def test_duplicate_arbiter_result_has_no_effects(self):
        stats = stats_fixture()
        stats.update({
            "run_id": "run-1", "_arbiter_result_keys": set(),
            "arbiter_work_completed": 0, "arbiter_work_total": 4,
            "eligible_rows": 2, "total_rows": 2, "current_arbiter_id": "a1",
            "current_pass_completed": 0, "current_pass_work_total": 2,
        })
        event = {"event": "arbiter_result", "row_index": 1, "arbiter_id": "a1", "total_tokens": 5}
        apply_arbiter_result_event(stats, event)
        duplicate = apply_arbiter_result_event(stats, event)
        self.assertEqual(stats["arbiter_work_completed"], 1)
        self.assertEqual(stats["total_tokens_global"], 5)
        self.assertEqual(duplicate.performance, ())

    def test_start_event_declares_performance_effect_without_persisting_it(self):
        stats = stats_fixture()
        stats.update({
            "active": False, "finished": False, "principles": [],
            "arbiter_count": 0, "arbiter_work_total": 0,
            "arbiter_work_completed": 0, "arbiter_work_before_start": 0,
            "dataset_total_rows": 0, "eligible_rows": 0,
        })
        effects = apply_start_event(stats, {
            "event": "start", "run_id": "run-1", "total_rows": 5,
            "eligible_rows": 4, "execution_order": "row_major", "arbiters": [],
        }, checkpoint_every=7)
        self.assertTrue(stats["active"])
        self.assertEqual(stats["checkpoint_every"], 7)
        self.assertEqual(effects.performance[0].values["total"], 4)


if __name__ == "__main__":
    unittest.main()
