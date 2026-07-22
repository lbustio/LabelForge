"""Pruebas de dominio para la maquina de estados de una ejecucion."""

import unittest

from labelforge_core.domain.run_lifecycle import (
    RunLifecycleDecision,
    RunLifecycleFacts,
    RunStatus,
    derive_run_lifecycle,
)


class TestRunLifecycle(unittest.TestCase):
    def test_state_matrix(self):
        cases = [
            (RunLifecycleFacts(), RunStatus.IDLE),
            (RunLifecycleFacts(has_run=True), RunStatus.PREPARED),
            (RunLifecycleFacts(has_run=True, process_running=True), RunStatus.RUNNING),
            (RunLifecycleFacts(has_run=True, process_running=True, stop_requested=True), RunStatus.STOPPING),
            (RunLifecycleFacts(has_run=True, pause_kind="user_stopped"), RunStatus.PAUSED),
            (RunLifecycleFacts(has_run=True, finished=True), RunStatus.COMPLETED),
            (RunLifecycleFacts(has_run=True, finished=True, failed_rows=1), RunStatus.COMPLETED_WITH_ISSUES),
            (RunLifecycleFacts(has_run=True, last_error="boom"), RunStatus.FAILED),
            (RunLifecycleFacts(has_run=True, persisted_status="running"), RunStatus.INTERRUPTED),
        ]
        for facts, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(derive_run_lifecycle(facts).status, expected)

    def test_paused_state_precedes_stopped_exit_code(self):
        decision = derive_run_lifecycle(RunLifecycleFacts(
            has_run=True,
            persisted_status="paused",
            pause_kind="user_stopped",
            pause_reason="detenida",
            exit_code="STOPPED",
        ))
        self.assertEqual(decision.status, RunStatus.PAUSED)
        self.assertTrue(decision.can_resume)
        self.assertFalse(decision.can_start)
        self.assertEqual(decision.reason, "detenida")

    def test_only_running_state_can_stop(self):
        for status in RunStatus:
            decision = RunLifecycleDecision(status)
            self.assertEqual(decision.can_stop, status is RunStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
