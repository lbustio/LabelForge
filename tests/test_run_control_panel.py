"""Contratos de estado y fuentes de datos de la pestana Ejecutar."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webapp import app as webapp_module


class TestRuntimeSnapshot(unittest.TestCase):
    def test_log_events_keep_stable_sequences_and_duplicate_text(self):
        state = webapp_module._process_state
        with state["state_lock"]:
            saved = {
                key: state.get(key)
                for key in (
                    "log_seq", "log_lines", "running", "pid", "exit_code",
                    "last_error", "run_log_path", "stop_requested",
                )
            }
            state.update({
                "log_seq": 8,
                "log_lines": ["mensaje repetido", "mensaje repetido", "ultimo"],
                "running": True,
                "stop_requested": True,
            })
        try:
            with mock.patch.object(webapp_module, "_hydrate_current_stats_from_disk"):
                initial = webapp_module._runtime_snapshot(0)
                incremental = webapp_module._runtime_snapshot(7)
        finally:
            with state["state_lock"]:
                state.update(saved)

        self.assertEqual(
            initial["log_entries"],
            [
                {"seq": 6, "line": "mensaje repetido"},
                {"seq": 7, "line": "mensaje repetido"},
                {"seq": 8, "line": "ultimo"},
            ],
        )
        self.assertEqual(incremental["log_entries"], [{"seq": 8, "line": "ultimo"}])
        self.assertTrue(initial["running"])
        self.assertTrue(initial["stop_requested"])

    def test_expired_api_session_returns_to_run_page_after_login(self):
        with webapp_module.app.test_request_context(
            "/api/run/snapshot?since=9",
            headers={"X-Return-To": "/run"},
        ):
            response, status = webapp_module._login_required_response("expirada")

        payload = response.get_json()
        self.assertEqual(status, 401)
        self.assertIn("next=/run", payload["login_url"])

    def test_logout_preserves_current_page_without_touching_the_run(self):
        state = webapp_module._process_state
        with state["state_lock"]:
            running_before = state.get("running")
        with webapp_module.app.test_request_context(
            "/logout",
            method="POST",
            data={"csrf_token": "token", "next": "/run"},
        ):
            from flask import session
            session["csrf_token"] = "token"
            response = webapp_module.logout()

        with state["state_lock"]:
            self.assertEqual(state.get("running"), running_before)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=/run", response.location)

    def test_runtime_snapshot_is_never_served_from_browser_cache(self):
        with mock.patch.object(webapp_module, "_runtime_snapshot", return_value={"seq": 4}):
            with webapp_module.app.test_request_context("/api/run/snapshot?since=3"):
                response = webapp_module.api_run_snapshot()

        self.assertEqual(response.get_json(), {"seq": 4})
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")


class TestRunMetricState(unittest.TestCase):
    def setUp(self):
        webapp_module._reset_current_stats()

    def test_arbiter_major_counts_vote_once_then_consolidates_without_duplication(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-a", "total_rows": 2,
            "dataset_total_rows": 2, "execution_order": "arbiter_major",
            "arbiter_count": 2, "arbiter_work_total": 4,
            "arbiter_work_completed": 0,
            "arbiters": [{"id": "a1", "name": "A1"}, {"id": "a2", "name": "A2"}],
        })
        vote = {
            "event": "arbiter_result", "row_index": 0, "arbiter_id": "a1",
            "arbiter_name": "A1", "prompt_tokens": 10,
            "completion_tokens": 2, "total_tokens": 12, "latency_s": 1.5,
        }
        webapp_module._apply_run_metric_event(vote)
        webapp_module._apply_run_metric_event(vote)  # retransmision idempotente
        stats = webapp_module._apply_run_metric_event({
            "event": "claim_ok", "row_index": 0, "total_tokens": 24,
            "total_latency_s": 3.0,
            "arbiters": [
                {"id": "a1", "total_tokens": 12, "latency_s": 1.5},
                {"id": "a2", "total_tokens": 12, "latency_s": 1.5},
            ],
            "consensus": {},
        })
        self.assertEqual(stats["arbiter_work_completed"], 1)
        self.assertEqual(stats["total_tokens_global"], 12)
        self.assertEqual(stats["arbiters"]["a1"]["calls"], 1)
        self.assertEqual(stats["processed_rows"], 1)
        self.assertEqual(stats["evaluated_rows"], 1)

    def test_arbiter_major_live_coverage_uses_most_advanced_arbiter(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-coverage", "total_rows": 4,
            "dataset_total_rows": 4, "eligible_rows": 3,
            "execution_order": "arbiter_major", "arbiter_count": 2,
            "arbiter_work_total": 6,
            "arbiters": [{"id": "a1"}, {"id": "a2"}],
        })
        for row_index in range(3):
            stats = webapp_module._apply_run_metric_event({
                "event": "arbiter_result", "row_index": row_index,
                "arbiter_id": "a1", "total_tokens": 1, "latency_s": 0.1,
            })
        self.assertEqual(stats["processed_rows"], 0)
        self.assertEqual(stats["arbiter_work_completed"], 3)
        self.assertEqual(stats["evaluated_rows"], 3)

        stats = webapp_module._apply_run_metric_event({
            "event": "arbiter_result", "row_index": 0,
            "arbiter_id": "a2", "total_tokens": 1, "latency_s": 0.1,
        })
        self.assertEqual(stats["arbiter_work_completed"], 4)
        self.assertEqual(stats["evaluated_rows"], 3)

    def test_row_major_keeps_entry_based_resource_accounting(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-r", "total_rows": 1,
            "dataset_total_rows": 1, "execution_order": "row_major",
            "arbiters": [{"id": "a1", "name": "A1"}],
        })
        stats = webapp_module._apply_run_metric_event({
            "event": "claim_ok", "row_index": 0, "total_tokens": 12,
            "total_latency_s": 1.5,
            "arbiters": [{
                "id": "a1", "name": "A1", "prompt_tokens": 10,
                "completion_tokens": 2, "total_tokens": 12, "latency_s": 1.5,
            }],
            "consensus": {},
        })
        self.assertEqual(stats["total_tokens_global"], 12)
        self.assertEqual(stats["arbiters"]["a1"]["calls"], 1)
        self.assertEqual(stats["successful_rows"], 1)

    def test_arbiter_claim_start_exposes_current_text_and_pass(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-a", "total_rows": 2,
            "dataset_total_rows": 2, "execution_order": "arbiter_major",
            "arbiters": [{"id": "a1", "name": "A1"}],
        })
        stats = webapp_module._apply_run_metric_event({
            "event": "arbiter_claim_start", "row_index": 1,
            "arbiter_id": "a1", "arbiter_name": "A1",
            "claim_preview": "Texto actualmente analizado",
            "position": 2, "pass_pending_total": 2,
            "pass_position": 1, "pass_total": 1,
            "stage": "arbiter_pass:a1",
        })
        self.assertEqual(stats["last_claim"]["claim_preview"], "Texto actualmente analizado")
        self.assertEqual(stats["last_claim"]["row_index"], 1)
        self.assertEqual(stats["current_arbiter_id"], "a1")
        self.assertEqual(stats["current_pass_completed"], 1)
        self.assertEqual(stats["current_pass_work_total"], 2)

    def test_empty_dataset_rows_do_not_inflate_arbiter_work(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-e", "total_rows": 3,
            "dataset_total_rows": 3, "eligible_rows": 2,
            "execution_order": "arbiter_major", "arbiter_count": 1,
            "arbiter_work_total": 2,
            "arbiters": [{"id": "a1", "name": "A1"}],
        })
        stats = webapp_module._apply_run_metric_event({
            "event": "arbiter_pass_start", "arbiter_id": "a1",
            "position": 1, "total_arbiters": 1, "pending_rows": 1,
            "pass_work_total": 2,
        })
        self.assertEqual(stats["eligible_rows"], 2)
        self.assertEqual(stats["arbiter_work_total"], 2)
        self.assertEqual(stats["current_pass_completed"], 1)

    def test_resume_restores_cumulative_resources(self):
        stats = webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-resume", "total_rows": 2,
            "dataset_total_rows": 2, "eligible_rows": 2,
            "execution_order": "arbiter_major", "arbiter_count": 1,
            "arbiter_work_total": 2, "arbiter_work_completed": 1,
            "arbiters": [{"id": "a1", "name": "A1"}],
            "initial_resource_summary": {
                "total_tokens_global": 120, "total_latency_s": 3.5,
                "arbiters": {"a1": {
                    "prompt_tokens": 100, "completion_tokens": 20,
                    "total_tokens": 120, "evaluations": 1,
                    "model_calls": 2, "latency_total_s": 3.5,
                    "latency_min_s": 3.5, "latency_max_s": 3.5,
                }},
            },
        })
        self.assertEqual(stats["total_tokens_global"], 120)
        self.assertEqual(stats["arbiters"]["a1"]["evaluations"], 1)
        self.assertEqual(stats["arbiters"]["a1"]["model_calls"], 2)
        self.assertEqual(stats["evaluated_rows"], 1)

    def test_provisional_failure_does_not_close_or_fail_row(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-f", "total_rows": 2,
            "dataset_total_rows": 2, "eligible_rows": 2,
            "execution_order": "row_major", "arbiters": [{"id": "a1"}],
        })
        stats = webapp_module._apply_run_metric_event({
            "event": "claim_failed", "row_index": 0, "queued": True,
            "definitive": False, "reason": "temporal",
        })
        self.assertEqual(stats["processed_rows"], 0)
        self.assertEqual(stats["failed_rows"], 0)
        self.assertEqual(stats["queued_rows"], 1)


class TestWebLogging(unittest.TestCase):
    def test_web_loggers_share_one_rotating_file_handler(self):
        handlers = [
            next(h for h in logger.handlers if hasattr(h, "baseFilename"))
            for logger in (
                webapp_module._req_log,
                webapp_module._log,
                webapp_module.app.logger,
                webapp_module._run_log,
            )
        ]
        self.assertTrue(all(handler is handlers[0] for handler in handlers))


class TestRunStatsSource(unittest.TestCase):
    def test_active_run_without_consensus_never_falls_back_to_old_csv(self):
        with tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR) as tmp:
            root = Path(tmp)
            checkpoints = root / "checkpoints"
            outputs = root / "output"
            checkpoints.mkdir()
            outputs.mkdir()
            (root / "input.csv").write_text("txt\nnueva\n", encoding="utf-8")
            (checkpoints / "run_state.json").write_text(json.dumps({
                "run_id": "current", "status": "running", "input_file": "input.csv",
                "sheet": "", "total_rows": 1,
            }), encoding="utf-8")
            # Un resultado historico mayor no debe contaminar el run activo.
            with open(outputs / "labeled_20200101_000000.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["txt", "AUTH_consensus"])
                writer.writeheader()
                writer.writerows([{"txt": f"old-{i}", "AUTH_consensus": 0} for i in range(5)])

            with mock.patch.multiple(
                webapp_module,
                BASE_DIR=root,
                CHECKPOINTS_DIR=checkpoints,
                OUTPUT_DIR=outputs,
                CLAIM_RESULTS_PATH=checkpoints / "claim_results.jsonl",
            ):
                with webapp_module.app.test_request_context("/api/run/stats"):
                    response, status = webapp_module.api_run_stats()
                    payload = response.get_json()

            self.assertEqual(status, 202)
            self.assertTrue(payload["pending"])
            self.assertEqual(payload["run_id"], "current")
            self.assertNotIn("old-0", json.dumps(payload))


class TestStopState(unittest.TestCase):
    def test_user_stop_becomes_resumable_pause(self):
        saved = []
        with mock.patch.object(webapp_module, "load_run_state", return_value={"run_id": "r", "status": "running"}), \
             mock.patch.object(webapp_module, "save_run_state", side_effect=lambda state: saved.append(dict(state))):
            webapp_module._reset_current_stats()
            webapp_module._mark_user_stopped_state()
        self.assertEqual(saved[0]["status"], "paused")
        self.assertEqual(saved[0]["pause_kind"], "user_stopped")
        self.assertFalse(webapp_module._process_state["current_stats"]["active"])


if __name__ == "__main__":
    unittest.main()
