"""Contrato de packaging y autoridad unica de dependencias."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class TestPackagingContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_python_baseline_matches_conda_environment(self):
        self.assertEqual(self.pyproject["project"]["requires-python"], ">=3.11")
        environment = (ROOT / "setup" / "environment.yml").read_text(encoding="utf-8")
        self.assertIn("python=3.11", environment)

    def test_direct_dependencies_have_one_authoritative_list(self):
        dependencies = self.pyproject["project"]["dependencies"]
        names = {item.split(">=", 1)[0].lower() for item in dependencies}
        self.assertEqual(len(names), len(dependencies))
        self.assertTrue({"flask", "openai", "anthropic", "google-genai"}.issubset(names))
        self.assertNotIn("llama-cpp-python", names)

        requirements = (ROOT / "setup" / "requirements.txt").read_text(encoding="utf-8")
        requirement_lines = [
            line.strip() for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(requirement_lines, ["-e ."])

    def test_hardware_specific_runtime_is_not_a_base_dependency(self):
        all_requirements = "\n".join(self.pyproject["project"]["dependencies"])
        self.assertNotIn("llama-cpp-python", all_requirements)
        setup_source = (ROOT / "setup" / "setup_env.py").read_text(encoding="utf-8")
        self.assertIn("instala mas", setup_source)
        self.assertIn("backend especifico", setup_source)

    def test_tooling_is_centralized_in_pyproject(self):
        tools = self.pyproject["tool"]
        self.assertTrue({"coverage", "ruff", "mypy", "setuptools"}.issubset(tools))
        dev = "\n".join(self.pyproject["project"]["optional-dependencies"]["dev"])
        for tool in ("coverage", "ruff", "mypy"):
            self.assertIn(tool, dev)


if __name__ == "__main__":
    unittest.main()
