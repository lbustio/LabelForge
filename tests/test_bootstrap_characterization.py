"""Linea base de entrypoints e imports antes de reconstruir el bootstrap."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run_python(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["LABELFORGE_SECRET_KEY"] = "bootstrap-characterization-only"
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


class TestLightweightImports(unittest.TestCase):
    def test_domain_and_application_do_not_import_web_or_local_runtime(self):
        script = """
import json, sys
import labelforge_core.domain.run_lifecycle
import labelforge_core.domain.run_record
import labelforge_core.application.run_metrics_projection
print(json.dumps({
    'flask': 'flask' in sys.modules,
    'llama_cpp': 'llama_cpp' in sys.modules,
    'webapp': any(name == 'webapp' or name.startswith('webapp.') for name in sys.modules),
}))
"""
        result = run_python("-c", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        imported = json.loads(result.stdout.strip())
        self.assertEqual(imported, {"flask": False, "llama_cpp": False, "webapp": False})

    def test_legacy_paths_do_not_eagerly_import_container_or_infrastructure(self):
        script = """
import json, sys
import labelforge_core.paths
print(json.dumps({
    'container': 'labelforge_core.bootstrap.container' in sys.modules,
    'execution': 'labelforge_core.infrastructure.execution' in sys.modules,
}))
"""
        result = run_python("-c", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.strip()),
            {"container": False, "execution": False},
        )


class TestEntrypointContracts(unittest.TestCase):
    def assert_help(self, entrypoint: str, *expected: str) -> None:
        result = run_python(entrypoint, "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        for text in expected:
            self.assertIn(text, output)

    def test_cli_help_contract(self):
        self.assert_help("main_cli.py", "--input", "--limit", "--config")

    def test_web_help_contract(self):
        self.assert_help("main_webapp.py", "--host", "--port", "--debug", "--reload")

    def test_manage_help_contract(self):
        self.assert_help("manage.py", "reset-admin-password", "list-users")


class TestWebRouteSurface(unittest.TestCase):
    def test_critical_routes_and_methods_remain_available(self):
        from webapp.app import app

        routes: dict[str, set[str]] = {}
        for rule in app.url_map.iter_rules():
            routes.setdefault(rule.rule, set()).update(rule.methods or ())
        expected = {
            "/login": {"GET", "POST"},
            "/api/dataset/upload": {"POST"},
            "/api/arbiters": {"GET", "POST"},
            "/api/run/start": {"POST"},
            "/api/run/stop": {"POST"},
            "/api/run/snapshot": {"GET"},
            "/api/runs": {"GET"},
            "/outputs/<name>": {"GET"},
        }
        for path, methods in expected.items():
            self.assertIn(path, routes)
            self.assertTrue(methods.issubset(routes[path]), (path, routes[path]))


if __name__ == "__main__":
    unittest.main()
