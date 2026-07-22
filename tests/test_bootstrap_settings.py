"""Contratos puros de settings y rutas del workspace."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from labelforge_core.bootstrap import AppSettings, SettingsError, WorkspacePaths
from labelforge_core import paths as legacy_paths


class TestWorkspacePaths(unittest.TestCase):
    def test_legacy_path_constants_delegate_to_workspace_paths(self):
        paths = WorkspacePaths.from_root(legacy_paths.BASE_DIR)
        self.assertEqual(legacy_paths.CONFIG_DIR, paths.config)
        self.assertEqual(legacy_paths.RUN_RECORDS_DIR, paths.run_records)
        self.assertEqual(legacy_paths.HW_PROFILE_PATH, paths.hardware_profile)

    def test_constructing_paths_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "workspace-not-created"
            paths = WorkspacePaths.from_root(root)
            self.assertFalse(root.exists())
            self.assertEqual(paths.output, root.resolve() / "output")
            self.assertEqual(paths.run_records, root.resolve() / "output" / ".manifests")
            self.assertFalse(root.exists())

    def test_runtime_directories_are_unique_and_inside_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            paths = WorkspacePaths.from_root(root)
            directories = paths.runtime_directories()
            self.assertEqual(len(directories), len(set(directories)))
            for directory in directories:
                self.assertTrue(directory.is_relative_to(paths.root))

    def test_filesystem_root_is_rejected_as_workspace(self):
        anchor = Path.cwd().anchor
        with self.assertRaises(ValueError):
            WorkspacePaths.from_root(anchor)


class TestAppSettings(unittest.TestCase):
    def test_overrides_have_precedence_over_environment(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AppSettings.from_sources(
                default_root=root,
                environ={"LABELFORGE_PORT": "9000", "LABELFORGE_HOST": "localhost"},
                overrides={"port": 8123},
            )
        self.assertEqual(settings.port, 8123)
        self.assertEqual(settings.host, "localhost")

    def test_defaults_are_local_and_do_not_trust_a_proxy(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AppSettings.from_sources(default_root=root, environ={})
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 7860)
        self.assertEqual(settings.trusted_proxy_hops, 0)

    def test_secret_is_not_exposed_by_repr(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AppSettings.from_sources(
                default_root=root,
                environ={"LABELFORGE_SECRET_KEY": "do-not-render-this-secret"},
            )
        self.assertNotIn("do-not-render-this-secret", repr(settings))

    def test_invalid_values_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SettingsError):
                AppSettings.from_sources(default_root=root, environ={"LABELFORGE_PORT": "70000"})
            with self.assertRaises(SettingsError):
                AppSettings.from_sources(default_root=root, environ={"LABELFORGE_DEBUG": "maybe"})

    def test_debug_cannot_be_exposed_on_the_network(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SettingsError):
                AppSettings.from_sources(
                    default_root=root,
                    environ={"LABELFORGE_DEBUG": "true", "LABELFORGE_HOST": "0.0.0.0"},
                )

    def test_absolute_session_must_not_expire_before_idle_session(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SettingsError):
                AppSettings.from_sources(
                    default_root=root,
                    environ={
                        "LABELFORGE_SESSION_IDLE_SECONDS": "3600",
                        "LABELFORGE_SESSION_ABSOLUTE_SECONDS": "300",
                    },
                )


if __name__ == "__main__":
    unittest.main()
