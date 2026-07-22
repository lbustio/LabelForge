"""Garantias de aislamiento del almacen de metricas en memoria."""

import threading
import unittest

from labelforge_core.infrastructure.execution import InMemoryRunMetrics


def empty_stats():
    return {"run_id": None, "processed": 0, "items": set()}


class TestInMemoryRunMetrics(unittest.TestCase):
    def test_snapshot_cannot_mutate_internal_state(self):
        store = InMemoryRunMetrics(empty_stats)
        snapshot = store.snapshot()
        snapshot["items"].add("external")
        self.assertEqual(store.snapshot()["items"], set())

    def test_edit_serializes_concurrent_updates(self):
        store = InMemoryRunMetrics(empty_stats)

        def increment():
            for _ in range(100):
                with store.edit() as editor:
                    editor.stats["processed"] += 1

        threads = [threading.Thread(target=increment) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(store.snapshot()["processed"], 400)

    def test_replace_if_empty_does_not_overwrite_a_live_run(self):
        store = InMemoryRunMetrics(empty_stats)
        self.assertTrue(store.replace_if_empty({"run_id": "first"}))
        self.assertFalse(store.replace_if_empty({"run_id": "second"}))
        self.assertEqual(store.snapshot()["run_id"], "first")


if __name__ == "__main__":
    unittest.main()
