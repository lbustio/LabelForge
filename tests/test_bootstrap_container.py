"""Pruebas del composition root independiente de Flask, CLI y TUI."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from labelforge_core.bootstrap import AppSettings, build_container
from labelforge_core.infrastructure.execution import InMemoryRunMetrics, InMemoryRunSupervisor
from labelforge_core.infrastructure.persistence import JsonRunRepository


class TestAppContainer(unittest.TestCase):
    def test_default_container_does_not_create_workspace(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "workspace"
            settings = AppSettings.from_sources(default_root=root, environ={})
            container = build_container(settings)

            self.assertFalse(root.exists())
            self.assertIsInstance(container.run_repository, JsonRunRepository)
            self.assertIsInstance(container.run_supervisor, InMemoryRunSupervisor)
            self.assertIsInstance(container.run_metrics, InMemoryRunMetrics)

    def test_dependencies_can_be_replaced_without_global_patching(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AppSettings.from_sources(default_root=root, environ={})
            repository = mock.Mock()
            supervisor = mock.Mock()
            metrics = mock.Mock()
            container = build_container(
                settings,
                run_repository=repository,
                run_supervisor=supervisor,
                run_metrics=metrics,
            )

        self.assertIs(container.run_repository, repository)
        self.assertIs(container.run_supervisor, supervisor)
        self.assertIs(container.run_metrics, metrics)

    def test_each_container_has_isolated_runtime_state(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AppSettings.from_sources(default_root=root, environ={})
            first = build_container(settings)
            second = build_container(settings)

        self.assertIsNot(first.run_supervisor, second.run_supervisor)
        self.assertIsNot(first.run_metrics, second.run_metrics)


if __name__ == "__main__":
    unittest.main()
