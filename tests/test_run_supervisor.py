"""Contrato concurrente del supervisor local de ejecuciones."""

import threading
import unittest
from unittest import mock

from labelforge_core.infrastructure.execution import InMemoryRunSupervisor


class TestInMemoryRunSupervisor(unittest.TestCase):
    def test_only_one_concurrent_start_can_claim_the_slot(self):
        supervisor = InMemoryRunSupervisor()
        barrier = threading.Barrier(8)
        results = []
        result_lock = threading.Lock()

        def claim():
            barrier.wait()
            accepted = supervisor.claim_start()
            with result_lock:
                results.append(accepted)

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 7)

    def test_stop_requested_during_start_is_seen_when_process_attaches(self):
        supervisor = InMemoryRunSupervisor()
        self.assertTrue(supervisor.claim_start())
        request = supervisor.request_stop()
        process = mock.Mock(pid=42)

        self.assertTrue(request.accepted)
        self.assertIsNone(request.process)
        self.assertTrue(supervisor.attach_process(process))

        supervisor.record_exit(143)
        was_stopped = supervisor.finish()
        snapshot = supervisor.snapshot()
        self.assertTrue(was_stopped)
        self.assertFalse(snapshot.running)
        self.assertEqual(snapshot.exit_code, "STOPPED")

    def test_log_sequences_remain_stable_after_retention(self):
        supervisor = InMemoryRunSupervisor(max_log_lines=2)
        for line in ("uno", "dos", "tres"):
            supervisor.append_log(line)

        snapshot = supervisor.snapshot(since=1)
        self.assertEqual(snapshot.seq, 3)
        self.assertEqual(snapshot.lines, ("dos", "tres"))
        self.assertEqual(snapshot.log_entries, ((2, "dos"), (3, "tres")))

    def test_reset_rejects_an_active_execution(self):
        supervisor = InMemoryRunSupervisor()
        supervisor.claim_start()
        with self.assertRaises(RuntimeError):
            supervisor.reset()


if __name__ == "__main__":
    unittest.main()
