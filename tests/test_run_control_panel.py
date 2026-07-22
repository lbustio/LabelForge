"""Contratos de estado y fuentes de datos de la pestana Ejecutar."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webapp import app as webapp_module
from labelforge_core import checkpoint_io
from labelforge_core.run_contract import build_resume_contract
from labelforge_core.domain.run_record import RunRecord
from labelforge_core.infrastructure.persistence import JsonRunRepository
from labelforge_core.infrastructure.execution import InMemoryRunSupervisor


class TestRuntimeSnapshot(unittest.TestCase):
    def test_log_events_keep_stable_sequences_and_duplicate_text(self):
        supervisor = InMemoryRunSupervisor(max_log_lines=3)
        supervisor.claim_start()
        for line in ["1", "2", "3", "4", "5", "mensaje repetido", "mensaje repetido", "ultimo"]:
            supervisor.append_log(line)
        supervisor.request_stop()
        with mock.patch.object(webapp_module, "_run_supervisor", supervisor):
            with mock.patch.object(webapp_module, "_hydrate_current_stats_from_disk"):
                initial = webapp_module._runtime_snapshot(0)
                incremental = webapp_module._runtime_snapshot(7)

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
        running_before = webapp_module._run_supervisor.is_active()
        with webapp_module.app.test_request_context(
            "/logout",
            method="POST",
            data={"csrf_token": "token", "next": "/run"},
        ):
            from flask import session
            session["csrf_token"] = "token"
            response = webapp_module.logout()

        self.assertEqual(webapp_module._run_supervisor.is_active(), running_before)
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
        self._incident_tmp = tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR)
        self._incident_patch = mock.patch.object(
            checkpoint_io, "RUN_INCIDENTS_PATH",
            Path(self._incident_tmp.name) / "run_incidents.jsonl",
        )
        self._performance_patch = mock.patch.object(
            checkpoint_io, "RUN_PERFORMANCE_PATH",
            Path(self._incident_tmp.name) / "run_performance.jsonl",
        )
        self._incident_patch.start()
        self._performance_patch.start()
        webapp_module._reset_current_stats()

    def tearDown(self):
        self._performance_patch.stop()
        self._incident_patch.stop()
        self._incident_tmp.cleanup()

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

    def test_successful_retry_closes_row_and_clears_queue_and_incident(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-recovered", "total_rows": 1,
            "eligible_rows": 1, "execution_order": "row_major",
            "arbiters": [{"id": "a1"}],
        })
        webapp_module._apply_run_metric_event({
            "event": "claim_failed", "run_id": "run-recovered", "row_index": 0,
            "arbiter_id": "a1", "queued": True, "definitive": False,
            "reason_code": "rate_limit", "reason": "temporal",
        })
        stats = webapp_module._apply_run_metric_event({
            "event": "claim_ok", "run_id": "run-recovered", "row_index": 0,
            "consensus": {"AUTH": 1}, "arbiters": [],
        })
        self.assertEqual(stats["successful_rows"], 1)
        self.assertEqual(stats["failed_rows"], 0)
        self.assertEqual(stats["queued_rows"], 0)
        incidents = checkpoint_io.load_run_incidents("run-recovered")
        self.assertEqual(incidents[0]["status"], "resolved")

    def test_definitive_failure_closes_row_and_removes_previous_queue_entry(self):
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-exhausted", "total_rows": 1,
            "eligible_rows": 1, "execution_order": "row_major",
            "arbiters": [{"id": "a1"}],
        })
        webapp_module._apply_run_metric_event({
            "event": "claim_failed", "run_id": "run-exhausted", "row_index": 0,
            "arbiter_id": "a1", "queued": True, "definitive": False,
            "reason_code": "server_error", "reason": "temporal",
        })
        stats = webapp_module._apply_run_metric_event({
            "event": "claim_failed", "run_id": "run-exhausted", "row_index": 0,
            "arbiter_id": "a1", "queued": False, "definitive": True,
            "reason_code": "retry_exhausted", "reason": "sin mas intentos",
        })
        self.assertEqual(stats["processed_rows"], 1)
        self.assertEqual(stats["successful_rows"], 0)
        self.assertEqual(stats["failed_rows"], 1)
        self.assertEqual(stats["queued_rows"], 0)
        incidents = checkpoint_io.load_run_incidents("run-exhausted")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["status"], "final")
        self.assertEqual(incidents[0]["kind"], "Fallo definitivo")


class TestRunDashboardContract(unittest.TestCase):
    def _dashboard(self, stats, **runtime):
        base_runtime = {
            "running": False, "stop_requested": False,
            "exit_code": None, "last_error": "",
        }
        base_runtime.update(runtime)
        with mock.patch.object(webapp_module, "load_run_state", return_value={}):
            return webapp_module._build_run_dashboard(stats, base_runtime)

    def test_arbiter_major_keeps_entry_and_evaluation_units_separate(self):
        stats = {
            "run_id": "run-units", "execution_order": "arbiter_major",
            "total_rows": 10, "dataset_total_rows": 12, "eligible_rows": 9,
            "processed_rows": 2, "successful_rows": 1, "failed_rows": 1,
            "evaluated_rows": 7, "arbiter_work_completed": 14,
            "arbiter_work_total": 27, "queued_indices": [8], "arbiters": {},
        }
        dashboard = self._dashboard(stats, running=True)
        progress = dashboard["progress"]
        self.assertEqual(progress["closed_rows"], 2)
        self.assertEqual(progress["successful_rows"], 1)
        self.assertEqual(progress["failed_rows"], 1)
        self.assertEqual(progress["evaluated_rows"], 7)
        self.assertEqual(progress["evaluations_completed"], 14)
        self.assertEqual(progress["evaluations_total"], 27)
        self.assertEqual(dashboard["identity"]["run_id"], "run-units")

    def test_paused_run_has_one_authoritative_state_and_resume_action(self):
        stats = {
            "run_id": "run-paused", "pause_kind": "user_stopped",
            "pause_reason": "Detenida por el usuario", "total_rows": 3,
            "processed_rows": 1, "successful_rows": 1,
            "queued_indices": [], "arbiters": {},
        }
        dashboard = self._dashboard(stats)
        self.assertEqual(dashboard["lifecycle"]["code"], "paused")
        self.assertTrue(dashboard["lifecycle"]["can_resume"])
        self.assertFalse(dashboard["lifecycle"]["can_stop"])
        self.assertEqual(dashboard["results"]["availability"], "partial")

    def test_completed_with_failures_is_not_reported_as_healthy_completion(self):
        stats = {
            "run_id": "run-done", "finished": True, "total_rows": 4,
            "processed_rows": 4, "successful_rows": 3, "failed_rows": 1,
            "queued_indices": [], "arbiters": {},
        }
        dashboard = self._dashboard(stats)
        self.assertEqual(dashboard["lifecycle"]["code"], "completed_with_issues")
        self.assertEqual(dashboard["lifecycle"]["tone"], "warning")
        self.assertEqual(dashboard["results"]["availability"], "final")

    def test_retry_queue_is_structured_and_not_inferred_from_log_text(self):
        stats = {
            "run_id": "run-queue", "total_rows": 2, "processed_rows": 0,
            "queued_indices": [0, 1], "arbiters": {},
        }
        dashboard = self._dashboard(stats, running=True)
        self.assertEqual(dashboard["queue"]["count"], 2)
        self.assertEqual(len(dashboard["incidents"]), 1)
        self.assertEqual(dashboard["incidents"][0]["kind"], "Reintentos pendientes")

    def test_orphaned_running_state_is_reported_as_interrupted(self):
        stats = {"run_id": "run-orphan", "total_rows": 2, "arbiters": {}}
        runtime = {"running": False, "stop_requested": False, "exit_code": None, "last_error": ""}
        with mock.patch.object(
            webapp_module, "load_run_state",
            return_value={"run_id": "run-orphan", "status": "running"},
        ), mock.patch.object(webapp_module, "read_config", return_value={"dataset": {}}):
            dashboard = webapp_module._build_run_dashboard(stats, runtime)
        self.assertEqual(dashboard["lifecycle"]["code"], "interrupted")
        self.assertTrue(dashboard["lifecycle"]["can_resume"])

    def test_completed_arbiter_pass_is_not_labeled_waiting(self):
        stats = {
            "run_id": "run-arbiters", "total_rows": 3, "eligible_rows": 3,
            "arbiters": {"a1": {"evaluations": 3, "calls": 3}},
        }
        dashboard = self._dashboard(stats, running=True)
        self.assertEqual(dashboard["arbiters"][0]["status"], "completed")

    def test_effective_objective_separates_limit_empty_reused_and_pending_work(self):
        stats = {
            "run_id": "run-objective", "total_rows": 8, "dataset_total_rows": 20,
            "eligible_rows": 6, "processed_rows": 4, "successful_rows": 3,
            "failed_rows": 0, "skipped_rows": 1, "completed_before_start": 2,
            "arbiter_work_total": 12, "arbiter_work_completed": 7,
            "arbiter_work_before_start": 4, "arbiters": {},
        }
        objective = self._dashboard(stats, running=True)["objective"]
        self.assertEqual(objective["outside_target_rows"], 12)
        self.assertEqual(objective["non_analyzable_rows"], 2)
        self.assertEqual(objective["analyzable_closed_rows"], 3)
        self.assertEqual(objective["analyzable_pending_rows"], 3)
        self.assertEqual(objective["reused_closed_rows"], 2)
        self.assertEqual(objective["evaluations_reused"], 4)
        self.assertEqual(objective["evaluations_this_session"], 3)
        self.assertEqual(objective["evaluations_pending"], 5)


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

    def test_completed_run_with_missing_artifact_never_falls_back_to_historical_csv(self):
        with tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR) as tmp:
            root = Path(tmp)
            checkpoints = root / "checkpoints"
            outputs = root / "output"
            checkpoints.mkdir()
            outputs.mkdir()
            (checkpoints / "run_state.json").write_text(json.dumps({
                "run_id": "completed-current", "status": "completed",
                "output_path": "output/labeled_20990101_000000.csv",
                "output_file": "labeled_20990101_000000.csv",
            }), encoding="utf-8")
            with open(outputs / "labeled_20200101_000000.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["txt", "AUTH_consensus"])
                writer.writeheader()
                writer.writerow({"txt": "historical", "AUTH_consensus": 1})

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

            self.assertEqual(status, 410)
            self.assertEqual(payload["code"], "artifact_missing")
            self.assertEqual(payload["run_id"], "completed-current")
            self.assertNotIn("historical", json.dumps(payload))


class TestDatasetExecutionIdentity(unittest.TestCase):
    def test_dataset_revision_changes_with_selection(self):
        base = {"input_file": "input.csv", "sheet": "", "text_column": "claim"}
        other_column = {**base, "text_column": "claim_es"}
        self.assertNotEqual(
            webapp_module._dataset_config_revision(base),
            webapp_module._dataset_config_revision(other_column),
        )

    def test_start_rejects_dataset_revision_not_seen_by_browser(self):
        cfg = {
            "dataset": {"input_file": "input.csv", "sheet": "", "text_column": "claim"},
            "arbiters": [], "task": {"principles": {}}, "processing": {},
        }
        supervisor = InMemoryRunSupervisor()
        with mock.patch.object(webapp_module, "_run_supervisor", supervisor):
            with mock.patch.object(webapp_module, "read_config", return_value=cfg), \
                 mock.patch.object(webapp_module, "validate_provider_preflight", return_value=[]):
                with webapp_module.app.test_request_context(
                    "/api/run/start", method="POST",
                    json={"dataset_revision": "stale-revision"},
                ):
                    response, status = webapp_module.api_run_start()
                    payload = response.get_json()

        self.assertEqual(status, 409)
        self.assertIn("cambio", payload["error"])


class TestCurrentArtifactProtection(unittest.TestCase):
    def test_current_run_artifact_cannot_be_deleted_as_historical_output(self):
        with tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR) as tmp:
            output_dir = Path(tmp)
            name = "labeled_20260721_120000.csv"
            path = output_dir / name
            path.write_text("claim\nexample\n", encoding="utf-8")
            with mock.patch.multiple(
                webapp_module,
                OUTPUT_DIR=output_dir,
                load_run_state=mock.DEFAULT,
            ), mock.patch.object(
                webapp_module, "load_run_state",
                return_value={"run_id": "current", "output_file": name},
            ), mock.patch.object(webapp_module, "_can_access_output", return_value=True):
                with webapp_module.app.test_request_context(f"/api/outputs/{name}", method="DELETE"):
                    response, status = webapp_module.api_delete_output(name)
                    payload = response.get_json()

            self.assertEqual(status, 409)
            self.assertEqual(payload["code"], "current_run_artifact")
            self.assertTrue(path.exists())


class TestResumeCompatibility(unittest.TestCase):
    def test_contract_ignores_secret_rotation_but_detects_model_change(self):
        base = {
            "task": {"principles": {"AUTH": "authority"}, "user_prompt_template": "{claim}"},
            "arbiters": [{"id": "a1", "provider": {"type": "openai", "model": "m1", "api_key": "one"}}],
            "processing": {"execution_order": "arbiter_major"},
        }
        rotated = json.loads(json.dumps(base))
        rotated["arbiters"][0]["provider"]["api_key"] = "two"
        changed_model = json.loads(json.dumps(base))
        changed_model["arbiters"][0]["provider"]["model"] = "m2"
        kwargs = {"input_file": "input.csv", "sheet": "", "text_column": "claim", "total_rows": 2}
        original = build_resume_contract(base, **kwargs)
        self.assertEqual(original["fingerprint"], build_resume_contract(rotated, **kwargs)["fingerprint"])
        self.assertNotEqual(original["fingerprint"], build_resume_contract(changed_model, **kwargs)["fingerprint"])

    def test_contract_allows_expanding_but_not_reducing_target(self):
        cfg = {"task": {"principles": {}}, "arbiters": [], "processing": {}}
        common = {"input_file": "input.csv", "sheet": "", "text_column": "claim"}
        saved = build_resume_contract(cfg, total_rows=10, **common)
        expanded = build_resume_contract(cfg, total_rows=20, **common)
        reduced = build_resume_contract(cfg, total_rows=5, **common)
        from labelforge_core.run_contract import compare_resume_contract
        self.assertTrue(compare_resume_contract(saved, expanded)["compatible"])
        self.assertFalse(compare_resume_contract(saved, reduced)["compatible"])

    def test_dashboard_blocks_resume_when_semantic_config_changed(self):
        old_cfg = {
            "dataset": {"input_file": "input.csv", "sheet": "", "text_column": "claim"},
            "task": {"principles": {"AUTH": "old"}}, "arbiters": [], "processing": {},
        }
        new_cfg = json.loads(json.dumps(old_cfg))
        new_cfg["task"]["principles"]["AUTH"] = "changed"
        saved = build_resume_contract(
            old_cfg, input_file="input.csv", sheet="", text_column="claim", total_rows=2,
        )
        state = {
            "run_id": "resume-blocked", "status": "paused", "input_file": "input.csv",
            "sheet": "", "text_column": "claim", "total_rows": 2, "resume_contract": saved,
        }
        stats = {"run_id": "resume-blocked", "pause_kind": "user_stopped", "arbiters": {}}
        runtime = {"running": False, "stop_requested": False, "exit_code": None, "last_error": ""}
        with mock.patch.object(webapp_module, "load_run_state", return_value=state), \
             mock.patch.object(webapp_module, "read_config", return_value=new_cfg):
            dashboard = webapp_module._build_run_dashboard(stats, runtime)
        self.assertEqual(dashboard["lifecycle"]["code"], "resume_incompatible")
        self.assertFalse(dashboard["lifecycle"]["can_resume"])


class TestPersistentRunIncidents(unittest.TestCase):
    def test_failed_event_survives_runtime_reset_and_resolves_on_success(self):
        with tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR) as tmp:
            incidents_path = Path(tmp) / "run_incidents.jsonl"
            performance_path = Path(tmp) / "run_performance.jsonl"
            with mock.patch.object(checkpoint_io, "RUN_INCIDENTS_PATH", incidents_path), \
                 mock.patch.object(checkpoint_io, "RUN_PERFORMANCE_PATH", performance_path):
                webapp_module._reset_current_stats()
                webapp_module._apply_run_metric_event({
                    "event": "start", "run_id": "run-incidents", "total_rows": 1,
                    "arbiters": [{"id": "a1"}],
                })
                webapp_module._apply_run_metric_event({
                    "event": "claim_failed", "run_id": "run-incidents", "row_index": 0,
                    "arbiter_id": "a1", "reason_code": "rate_limit", "reason": "quota",
                    "queued": True, "definitive": False,
                })
                self.assertEqual(checkpoint_io.load_run_incidents("run-incidents")[0]["status"], "open")
                webapp_module._apply_run_metric_event({
                    "event": "claim_ok", "run_id": "run-incidents", "row_index": 0,
                    "consensus": {}, "arbiters": [],
                })
                self.assertEqual(checkpoint_io.load_run_incidents("run-incidents")[0]["status"], "resolved")


class TestBackendPerformanceEstimates(unittest.TestCase):
    def test_rate_summary_requires_evidence_and_returns_eta_range(self):
        sparse = [
            {"timestamp": "2026-01-01T00:00:00", "completed": 0},
            {"timestamp": "2026-01-01T00:01:00", "completed": 1},
        ]
        self.assertFalse(webapp_module._performance_rate_summary(sparse, 10)["ready"])

        samples = [
            {"timestamp": f"2026-01-01T00:{minute:02d}:00", "completed": completed}
            for minute, completed in enumerate([0, 1, 3, 4, 6, 8, 9, 11, 13, 14])
        ]
        summary = webapp_module._performance_rate_summary(samples, 10)
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["confidence"], "medium")
        self.assertGreater(summary["eta_minutes_high"], 0)
        self.assertLessEqual(summary["eta_minutes_low"], summary["eta_minutes_high"])

    def test_token_projection_waits_for_three_completed_units(self):
        with mock.patch.object(webapp_module, "load_performance_samples", return_value=[]):
            insufficient = webapp_module._build_performance_dashboard(
                "r", completed=2, total=10, total_tokens=100,
            )
            ready = webapp_module._build_performance_dashboard(
                "r", completed=5, total=10, total_tokens=100,
            )
        self.assertIsNone(insufficient["tokens"]["projected"])
        self.assertEqual(ready["tokens"]["projected"], 200)

    def test_rate_history_is_generated_by_backend(self):
        samples = [
            {"timestamp": "2026-01-01T00:00:00", "completed": 0},
            {"timestamp": "2026-01-01T00:01:00", "completed": 2},
            {"timestamp": "2026-01-01T00:02:00", "completed": 5},
        ]
        history = webapp_module._performance_rate_history(samples)
        self.assertEqual([item["rate_per_minute"] for item in history], [2.0, 3.0])


class TestRunTemplateHasSingleStateAuthority(unittest.TestCase):
    def test_template_does_not_derive_cards_from_log_text(self):
        template = (webapp_module.WEBAPP_DIR / "templates" / "run.html").read_text(encoding="utf-8")
        for obsolete in (
            "function parseStats(", "statsOK", "statsFailed", "statsErrors",
            "lastArbErrors", "speedHistory", "recentItemsPerMinute",
        ):
            self.assertNotIn(obsolete, template)

    def test_execute_links_to_run_analysis_without_duplicating_scientific_panels(self):
        template = (webapp_module.WEBAPP_DIR / "templates" / "run.html").read_text(encoding="utf-8")
        self.assertIn("btn-open-run-analysis", template)
        self.assertIn("btn-download-labeled", template)
        self.assertIn("Descargar dataset etiquetado", template)
        self.assertIn("dashboard.results?.availability === 'final'", template)
        self.assertIn("dashboard.results?.artifact_exists", template)
        self.assertNotIn('id="paper-summary-panel"', template)
        self.assertNotIn('id="agreement-panel"', template)
        self.assertIn("No se pudieron cargar los resultados de esta ejecucion.", template)
        self.assertIn("Tokens / resultado cerrado", template)
        self.assertIn("item.severity || 'warning'", template)
        self.assertIn("Sin incidentes activos.", template)


class TestRunManifest(unittest.TestCase):
    def test_manifest_roundtrip_and_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = JsonRunRepository(Path(tmp))
            repository.save(RunRecord.from_dict({
                "schema_version": 1,
                "run_id": "run-20260721",
                "status": "completed",
                "completed_at": "2026-07-21T10:00:00",
            }))
            self.assertEqual(repository.get("run-20260721").status, "completed")
            self.assertEqual(repository.list()[0].run_id, "run-20260721")
            self.assertEqual(repository.list_by_owner(999), [])
            self.assertTrue(repository.delete("run-20260721"))
            self.assertIsNone(repository.get("run-20260721"))

    def test_manifest_rejects_unsafe_run_id(self):
        with self.assertRaises(ValueError):
            RunRecord(run_id="../outside", status="completed")

    def test_run_record_rejects_artifact_paths_and_credentials(self):
        with self.assertRaises(ValueError):
            RunRecord(run_id="safe", status="completed", output_file="../result.csv")
        with self.assertRaises(ValueError):
            RunRecord(
                run_id="safe", status="completed",
                configuration={"provider": {"api_key": "must-not-persist"}},
            )

    def test_completed_run_flows_from_manifest_to_history_detail(self):
        with tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR) as tmp:
            root = Path(tmp)
            outputs = root / "output"
            manifests = outputs / ".manifests"
            outputs.mkdir()
            output_name = "labeled_20260721_150000.csv"
            with open(outputs / output_name, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["claim", "AUTH_consensus"])
                writer.writeheader()
                writer.writerow({"claim": "example", "AUTH_consensus": 1})

            state = {
                "run_id": "20260721_150000", "status": "completed",
                "created_at": "2026-07-21T14:59:00", "updated_at": "2026-07-21T15:00:00",
                "input_file": "input.csv", "sheet": "", "text_column": "claim",
                "output_file": output_name, "total_rows": 1, "eligible_rows": 1,
                "arbiters": [{"id": "a1", "provider": "openai", "model": "m1"}],
                "principles": ["AUTH"],
            }
            cfg = {
                "dataset": {"input_file": "input.csv", "sheet": "", "text_column": "claim"},
                "task": {"principles": {"AUTH": "authority"}},
                "arbiters": [{"id": "a1", "provider": {"type": "openai", "model": "m1", "api_key": "secret"}}],
                "processing": {"execution_order": "row_major"},
            }
            event = {
                "event": "complete", "run_id": "20260721_150000",
                "timestamp": "2026-07-21T15:00:00", "output_file": output_name,
                "total_rows": 1, "final_summary": {"successful_rows": 1},
            }
            user = {"id": 7, "username": "owner"}

            repository = JsonRunRepository(manifests)
            with mock.patch.multiple(webapp_module, OUTPUT_DIR=outputs, _run_repository=repository), \
                 mock.patch.object(webapp_module, "load_run_state", return_value=state), \
                 mock.patch.object(webapp_module, "read_config", return_value=cfg):
                webapp_module._record_run_manifest(event, user)
                stored = repository.get("20260721_150000").to_dict()
                self.assertTrue(stored["configuration"]["fingerprint"])
                self.assertNotIn("secret", json.dumps(stored))

                with mock.patch.object(webapp_module, "_current_user_is_admin", return_value=False), \
                     mock.patch.object(webapp_module, "_current_user_id", return_value=7), \
                     mock.patch.object(webapp_module, "_can_access_output", return_value=True):
                    with webapp_module.app.test_request_context("/api/runs"):
                        listed = webapp_module.api_runs().get_json()
                    with webapp_module.app.test_request_context("/api/runs/20260721_150000"):
                        detail = webapp_module.api_run_detail("20260721_150000").get_json()

            self.assertEqual([item["run_id"] for item in listed], ["20260721_150000"])
            self.assertEqual(detail["manifest"]["run_id"], "20260721_150000")
            self.assertEqual(detail["stats"]["total_rows"], 1)

    def test_manifest_remains_visible_when_artifact_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = JsonRunRepository(Path(tmp))
            repository.save(RunRecord.from_dict({
                "schema_version": 1, "run_id": "run-without-csv", "status": "completed",
                "completed_at": "2026-07-21T10:00:00", "output_file": "missing.csv",
                "owner": {"user_id": 7, "username": "owner"},
            }))
            with mock.patch.object(webapp_module, "_run_repository", repository), \
                 mock.patch.object(webapp_module, "_current_user_is_admin", return_value=False), \
                 mock.patch.object(webapp_module, "_current_user_id", return_value=7):
                with webapp_module.app.test_request_context("/api/runs/run-without-csv"):
                    payload = webapp_module.api_run_detail("run-without-csv").get_json()
            self.assertFalse(payload["manifest"]["artifact_exists"])
            self.assertIsNone(payload["stats"])


class TestStopState(unittest.TestCase):
    def test_user_stop_becomes_resumable_pause(self):
        saved = []
        with mock.patch.object(webapp_module, "load_run_state", return_value={"run_id": "r", "status": "running"}), \
             mock.patch.object(webapp_module, "save_run_state", side_effect=lambda state: saved.append(dict(state))):
            webapp_module._reset_current_stats()
            webapp_module._mark_user_stopped_state()
        self.assertEqual(saved[0]["status"], "paused")
        self.assertEqual(saved[0]["pause_kind"], "user_stopped")
        self.assertFalse(webapp_module._run_metrics.snapshot()["active"])


class TestRunLifecycleEndpoints(unittest.TestCase):
    def setUp(self):
        self.supervisor_patch = mock.patch.object(
            webapp_module, "_run_supervisor", InMemoryRunSupervisor()
        )
        self.supervisor = self.supervisor_patch.start()
        webapp_module._reset_current_stats()

    def tearDown(self):
        self.supervisor_patch.stop()
        webapp_module._reset_current_stats()

    def test_stop_endpoint_transitions_from_stopping_to_resumable_pause(self):
        fake_proc = object()
        fake_proc = mock.Mock(pid=123)
        self.supervisor.claim_start()
        self.supervisor.attach_process(fake_proc)
        webapp_module._apply_run_metric_event({
            "event": "start", "run_id": "run-stop", "total_rows": 3,
            "eligible_rows": 3, "arbiters": [{"id": "a1"}],
        })

        fake_thread = mock.Mock()
        with mock.patch.object(webapp_module.threading, "Thread", return_value=fake_thread):
            with webapp_module.app.test_request_context("/api/run/stop", method="POST"):
                response = webapp_module.api_run_stop()
        self.assertTrue(response.get_json()["stopping"])
        self.assertTrue(self.supervisor.snapshot().stop_requested)
        fake_thread.start.assert_called_once_with()

        with mock.patch.object(webapp_module, "load_run_state", return_value={"run_id": "run-stop", "status": "running"}), \
             mock.patch.object(webapp_module, "save_run_state") as save_state, \
             mock.patch.object(webapp_module, "_resume_compatibility", return_value={"applicable": True, "compatible": True}):
            stopping = webapp_module._build_run_dashboard(
                webapp_module._public_current_stats(),
                {"running": True, "stop_requested": True, "exit_code": None, "last_error": ""},
            )
            webapp_module._mark_user_stopped_state()
            paused = webapp_module._build_run_dashboard(
                webapp_module._public_current_stats(),
                {"running": False, "stop_requested": True, "exit_code": "STOPPED", "last_error": ""},
            )

        self.assertEqual(stopping["lifecycle"]["code"], "stopping")
        self.assertFalse(stopping["lifecycle"]["can_stop"])
        self.assertEqual(paused["lifecycle"]["code"], "paused")
        self.assertTrue(paused["lifecycle"]["can_resume"])
        self.assertEqual(save_state.call_args.args[0]["status"], "paused")

    def test_start_endpoint_accepts_compatible_paused_run(self):
        with tempfile.TemporaryDirectory(dir=webapp_module.BASE_DIR) as tmp:
            input_path = Path(tmp) / "input.csv"
            input_path.write_text("claim\nexample\n", encoding="utf-8")
            cfg = {
                "dataset": {"input_file": str(input_path), "sheet": "", "text_column": "claim"},
                "processing": {}, "arbiters": [], "task": {"principles": {}},
            }
            revision = "current-dataset-revision"
            fake_thread = mock.Mock()
            with mock.patch.object(webapp_module, "read_config", return_value=cfg), \
                 mock.patch.object(webapp_module, "validate_provider_preflight", return_value=[]), \
                 mock.patch.object(webapp_module, "load_run_state", return_value={"run_id": "paused", "status": "paused", "total_rows": 1}), \
                 mock.patch.object(webapp_module, "_resume_compatibility", return_value={"applicable": True, "compatible": True}), \
                 mock.patch.object(webapp_module, "_resolve_allowed_dataset_path", return_value=input_path), \
                 mock.patch.object(webapp_module, "_dataset_config_revision", return_value=revision), \
                 mock.patch.object(webapp_module.threading, "Thread", return_value=fake_thread):
                with webapp_module.app.test_request_context(
                    "/api/run/start", method="POST", json={"dataset_revision": revision},
                ):
                    response = webapp_module.api_run_start()

            response_obj = response[0] if isinstance(response, tuple) else response
            status = response[1] if isinstance(response, tuple) else response.status_code
            self.assertEqual(status, 200, response_obj.get_json())
            self.assertTrue(response_obj.get_json()["ok"])
            self.assertTrue(self.supervisor.is_active())
            fake_thread.start.assert_called_once_with()


class TestRunStateHydration(unittest.TestCase):
    def setUp(self):
        webapp_module._reset_current_stats()

    def tearDown(self):
        webapp_module._reset_current_stats()

    def test_completed_run_keeps_summary_resources_when_partial_files_are_gone(self):
        state = {
            "run_id": "run-completed-restart", "status": "completed",
            "total_rows": 2, "eligible_rows": 2, "output_file": "labeled_done.csv",
            "arbiters": [{"id": "a1", "name": "A1"}], "principles": ["AUTH"],
            "summary": {
                "processed_rows": 2, "successful_rows": 2, "failed_rows": 0,
                "total_tokens_global": 120, "total_latency_s": 3.5,
                "arbiters": {"a1": {
                    "prompt_tokens": 90, "completion_tokens": 30,
                    "total_tokens": 120, "evaluations": 2, "model_calls": 2,
                    "latency_total_s": 3.5,
                }},
            },
        }
        with mock.patch.object(webapp_module, "load_run_state", return_value=state), \
             mock.patch.object(webapp_module, "load_arbiter_results", return_value={}), \
             mock.patch.object(webapp_module, "load_failed_queue", return_value=[]), \
             mock.patch.object(webapp_module, "read_config", return_value={"processing": {"execution_order": "row_major"}}):
            webapp_module._hydrate_current_stats_from_disk()
            restored = webapp_module._public_current_stats()

        self.assertTrue(restored["finished"])
        self.assertEqual(restored["total_tokens_global"], 120)
        self.assertEqual(restored["total_latency_s"], 3.5)
        self.assertEqual(restored["arbiters"]["a1"]["evaluations"], 2)

    def test_paused_arbiter_run_restores_partial_work_queue_and_resources(self):
        state = {
            "run_id": "run-paused-restart", "status": "paused",
            "pause_kind": "provider_unavailable", "pause_reason": "servicio temporal",
            "total_rows": 3, "dataset_total_rows": 10, "eligible_rows": 2,
            "arbiters": [{"id": "a1", "name": "A1"}, {"id": "a2", "name": "A2"}],
            "principles": ["AUTH"], "summary": {},
        }
        partial = {
            "0": {
                "a1": {"_usage": {"prompt_tokens": 10, "completion_tokens": 2}, "_latency_s": 1.25, "_chunks": 1},
                "a2": {"_usage": {"prompt_tokens": 20, "completion_tokens": 3}, "_latency_s": 2.5, "_chunks": 2},
            },
        }
        with mock.patch.object(webapp_module, "load_run_state", return_value=state), \
             mock.patch.object(webapp_module, "load_arbiter_results", return_value=partial), \
             mock.patch.object(webapp_module, "load_failed_queue", return_value=[{"index": 1}]), \
             mock.patch.object(webapp_module, "read_config", return_value={"processing": {"execution_order": "arbiter_major"}}), \
             mock.patch.object(webapp_module, "_resume_compatibility", return_value={"applicable": True, "compatible": True}), \
             mock.patch.object(webapp_module, "load_run_incidents", return_value=[]), \
             mock.patch.object(webapp_module, "load_performance_samples", return_value=[]):
            webapp_module._hydrate_current_stats_from_disk()
            restored = webapp_module._public_current_stats()
            dashboard = webapp_module._build_run_dashboard(
                restored,
                {"running": False, "stop_requested": False, "exit_code": None, "last_error": ""},
            )

        self.assertFalse(restored["active"])
        self.assertEqual(restored["arbiter_work_completed"], 2)
        self.assertEqual(restored["total_tokens_global"], 35)
        self.assertEqual(restored["queued_indices"], ["1"])
        self.assertEqual(dashboard["lifecycle"]["code"], "paused")
        self.assertTrue(dashboard["lifecycle"]["can_resume"])
        self.assertEqual(dashboard["queue"]["count"], 1)
        self.assertEqual(dashboard["objective"]["dataset_rows"], 10)
        self.assertEqual(dashboard["objective"]["outside_target_rows"], 7)


class TestRunOwnershipAccess(unittest.TestCase):
    def _identity_patches(self, user_id, *, admin=False):
        return (
            mock.patch.object(webapp_module, "_current_user_id", return_value=user_id),
            mock.patch.object(webapp_module, "_current_user_is_admin", return_value=admin),
        )

    def test_run_listing_is_filtered_for_users_and_complete_for_admin(self):
        manifests = [
            RunRecord.from_dict({"run_id": "run-seven", "status": "completed", "completed_at": "2026-07-21T10:00:00", "owner": {"user_id": 7, "username": "seven"}}),
            RunRecord.from_dict({"run_id": "run-eight", "status": "completed", "completed_at": "2026-07-21T11:00:00", "owner": {"user_id": 8, "username": "eight"}}),
        ]
        repository = mock.Mock()
        repository.list.return_value = manifests
        repository.list_by_owner.return_value = [manifests[0]]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.multiple(webapp_module, OUTPUT_DIR=Path(tmp)), \
             mock.patch.object(webapp_module, "_run_repository", repository):
            user_patch, admin_patch = self._identity_patches(7)
            with user_patch, admin_patch, webapp_module.app.test_request_context("/api/runs"):
                user_runs = webapp_module.api_runs().get_json()
            user_patch, admin_patch = self._identity_patches(1, admin=True)
            with user_patch, admin_patch, webapp_module.app.test_request_context("/api/runs"):
                admin_runs = webapp_module.api_runs().get_json()

        self.assertEqual([item["run_id"] for item in user_runs], ["run-seven"])
        self.assertEqual({item["run_id"] for item in admin_runs}, {"run-seven", "run-eight"})

    def test_run_detail_rejects_other_user_but_admin_can_audit(self):
        manifest = RunRecord.from_dict({
            "run_id": "run-seven", "status": "completed",
            "output_file": "", "owner": {"user_id": 7, "username": "seven"},
        })
        repository = mock.Mock()
        repository.get.return_value = manifest
        with mock.patch.object(webapp_module, "_run_repository", repository):
            user_patch, admin_patch = self._identity_patches(8)
            with user_patch, admin_patch, webapp_module.app.test_request_context("/api/runs/run-seven"):
                response, status = webapp_module.api_run_detail("run-seven")
            self.assertEqual(status, 403)
            self.assertEqual(response.get_json()["error"], "No autorizado")

            user_patch, admin_patch = self._identity_patches(1, admin=True)
            with user_patch, admin_patch, webapp_module.app.test_request_context("/api/runs/run-seven"):
                payload = webapp_module.api_run_detail("run-seven").get_json()
            self.assertEqual(payload["manifest"]["run_id"], "run-seven")

    def test_download_and_delete_enforce_output_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = Path(tmp)
            name = "labeled_20260721_160000.csv"
            path = outputs / name
            path.write_text("claim\nexample\n", encoding="utf-8")
            owner = {"id": 7, "run_id": "run-seven", "username": "seven"}

            def can_access():
                return webapp_module._current_user_is_admin() or owner["id"] == webapp_module._current_user_id()

            with mock.patch.multiple(webapp_module, OUTPUT_DIR=outputs), \
                 mock.patch.object(webapp_module, "_output_owner", return_value=owner), \
                 mock.patch.object(webapp_module, "_can_access_output", side_effect=lambda _name: can_access()), \
                 mock.patch.object(webapp_module, "load_run_state", return_value={}):
                user_patch, admin_patch = self._identity_patches(8)
                with user_patch, admin_patch, webapp_module.app.test_request_context(f"/outputs/{name}"):
                    response, status = webapp_module.api_serve_output(name)
                self.assertEqual(status, 403)
                self.assertTrue(path.exists())

                user_patch, admin_patch = self._identity_patches(8)
                with user_patch, admin_patch, webapp_module.app.test_request_context(
                    f"/api/outputs/{name}", method="DELETE",
                ):
                    response, status = webapp_module.api_delete_output(name)
                self.assertEqual(status, 403)
                self.assertTrue(path.exists())

                user_patch, admin_patch = self._identity_patches(7)
                with user_patch, admin_patch, webapp_module.app.test_request_context(f"/outputs/{name}"):
                    body, status, headers = webapp_module.api_serve_output(name)
                self.assertEqual(status, 200)
                self.assertIn(b"example", body)
                self.assertIn("attachment", headers["Content-Disposition"])

    def test_run_repository_is_authoritative_over_legacy_ownership(self):
        manifest = RunRecord.from_dict({
            "run_id": "run-seven", "output_file": "labeled_20260721_160000.csv",
            "status": "completed", "owner": {"user_id": 7, "username": "seven"},
        })
        repository = mock.Mock()
        repository.find_by_output_file.return_value = manifest
        user_patch, admin_patch = self._identity_patches(7)
        with user_patch, admin_patch, \
             mock.patch.object(webapp_module, "_legacy_output_owner", return_value=None), \
             mock.patch.object(webapp_module, "_run_repository", repository):
            self.assertTrue(webapp_module._can_access_output(manifest.output_file))

        # Un registro legacy contradictorio no puede reemplazar al RunRecord.
        user_patch, admin_patch = self._identity_patches(7)
        with user_patch, admin_patch, \
             mock.patch.object(webapp_module, "_legacy_output_owner", return_value={"id": 8}), \
             mock.patch.object(webapp_module, "_run_repository", repository):
            self.assertTrue(webapp_module._can_access_output(manifest.output_file))

    def test_legacy_output_is_migrated_to_run_repository_on_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "output"
            outputs.mkdir()
            name = "labeled_20260721_170000.csv"
            (outputs / name).write_text("claim\nexample\n", encoding="utf-8")
            repository = JsonRunRepository(root / "runs")
            legacy_owner = {"id": 7, "username": "seven", "run_id": "20260721_170000"}
            user_patch, admin_patch = self._identity_patches(7)
            with user_patch, admin_patch, \
                 mock.patch.multiple(webapp_module, OUTPUT_DIR=outputs, _run_repository=repository), \
                 mock.patch.object(webapp_module, "_legacy_output_owner", return_value=legacy_owner):
                with webapp_module.app.test_request_context("/api/runs"):
                    payload = webapp_module.api_runs().get_json()

            migrated = repository.get("20260721_170000")
            self.assertEqual([item["run_id"] for item in payload], ["20260721_170000"])
            self.assertIsNotNone(migrated)
            self.assertTrue(migrated.legacy)
            self.assertEqual(migrated.schema_version, 0)
            self.assertEqual(migrated.owner.user_id, 7)

if __name__ == "__main__":
    unittest.main()
