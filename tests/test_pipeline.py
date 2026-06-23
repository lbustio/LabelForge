"""
test_pipeline.py
Pruebas unitarias para validar las funciones utilitarias,
las metricas de acuerdo inter-arbitros y las validaciones de configuracion.
"""

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

# Asegurar que el root del proyecto este en el path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from labelforge_core.utils import safe_int, safe_float, safe_binary_int, number
from labelforge_core.agreement_metrics import compute_agreement_metrics, compute_consensus_metrics
from labelforge_core.label_persuasion import should_reuse_run_state, validate_safety_policy
from labelforge_core.arbiters_io import normalize_arbiter


class TestUtils(unittest.TestCase):
    def test_safe_int(self):
        self.assertEqual(safe_int(123), 123)
        self.assertEqual(safe_int("123"), 123)
        self.assertEqual(safe_int("12.3"), 12)
        self.assertEqual(safe_int(12.9), 12)
        self.assertEqual(safe_int(None, -1), -1)
        self.assertEqual(safe_int("", -1), -1)
        self.assertEqual(safe_int("abc", 999), 999)

    def test_safe_float(self):
        self.assertEqual(safe_float(12.5), 12.5)
        self.assertEqual(safe_float("12.5"), 12.5)
        self.assertEqual(safe_float(None, 0.0), 0.0)
        self.assertEqual(safe_float("abc", 1.23), 1.23)

    def test_safe_binary_int(self):
        self.assertEqual(safe_binary_int(1), 1)
        self.assertEqual(safe_binary_int(0), 0)
        self.assertEqual(safe_binary_int("1"), 1)
        self.assertEqual(safe_binary_int("0"), 0)
        self.assertEqual(safe_binary_int("1.0"), 1)
        self.assertEqual(safe_binary_int(1.0), 1)
        self.assertIsNone(safe_binary_int(2))
        self.assertIsNone(safe_binary_int("abc"))
        self.assertIsNone(safe_binary_int(None))

    def test_number_helper(self):
        self.assertEqual(number("123", 0), 123)
        self.assertEqual(number("12.5", 0.0), 12.5)
        self.assertEqual(number("abc", 99), 99)
        self.assertEqual(number(None, 42), 42)


class TestAgreementMetrics(unittest.TestCase):
    def test_perfect_agreement(self):
        # Perfect agreement on AUTH and SP principles
        # rows: list of dicts, representing values from CSV
        rows = [
            {"AUTH_arb1": "1", "AUTH_arb2": "1", "AUTH_arb3": "1", "SP_arb1": "0", "SP_arb2": "0", "SP_arb3": "0"},
            {"AUTH_arb1": "0", "AUTH_arb2": "0", "AUTH_arb3": "0", "SP_arb1": "1", "SP_arb2": "1", "SP_arb3": "1"},
        ]
        principles = ["AUTH", "SP"]
        arbiter_ids = ["arb1", "arb2", "arb3"]

        metrics = compute_agreement_metrics(rows, principles, arbiter_ids)

        self.assertEqual(metrics["global"]["observed_agreement"], 1.0)
        self.assertEqual(metrics["global"]["fleiss_kappa"], 1.0)
        self.assertEqual(metrics["global"]["pairwise_cohen_kappa_avg"], 1.0)
        self.assertEqual(metrics["global"]["krippendorff_alpha_nominal"], 1.0)

    def test_no_agreement(self):
        # Total disagreement
        rows = [
            {"AUTH_arb1": "1", "AUTH_arb2": "0", "AUTH_arb3": "1"},
            {"AUTH_arb1": "0", "AUTH_arb2": "1", "AUTH_arb3": "0"},
        ]
        principles = ["AUTH"]
        arbiter_ids = ["arb1", "arb2", "arb3"]

        metrics = compute_agreement_metrics(rows, principles, arbiter_ids)
        # Observed agreement should be around 0.33 (since only 1 pair out of 3 agrees per row)
        # pairs are: (arb1, arb2), (arb1, arb3), (arb2, arb3)
        # Row 1: arb1=1, arb2=0, arb3=1 -> only (arb1, arb3) agrees (1/3)
        # Row 2: arb1=0, arb2=1, arb3=0 -> only (arb1, arb3) agrees (1/3)
        self.assertAlmostEqual(metrics["global"]["observed_agreement"], 0.3333, places=4)

    def test_compute_consensus_metrics(self):
        # In-memory results format (dict of dicts, values as integers)
        results = {
            "0": {"AUTH_arb1": 1, "AUTH_arb2": 1, "AUTH_arb3": 1},
            "1": {"AUTH_arb1": 0, "AUTH_arb2": 0, "AUTH_arb3": 0},
        }
        principles = ["AUTH"]
        arbiters = [{"id": "arb1"}, {"id": "arb2"}, {"id": "arb3"}]

        metrics = compute_consensus_metrics(results, principles, arbiters)
        self.assertEqual(metrics["global"]["observed_agreement"], 1.0)


class TestSafetyPolicy(unittest.TestCase):
    def test_validate_safety_policy_allowed(self):
        cfg = {
            "safety": {
                "free_only_mode": True,
                "allowed_provider_types": ["gemini", "vertex_gemini"],
            },
            "arbiters": [
                {"id": "arb1", "provider": {"type": "gemini", "model": "gemini-2.5-pro"}},
                {"id": "arb2", "provider": {"type": "vertex_gemini", "model": "gemini-2.5-flash"}},
            ],
        }
        # Should execute without error
        try:
            validate_safety_policy(cfg)
        except RuntimeError:
            self.fail("validate_safety_policy raised RuntimeError unexpectedly!")

    def test_validate_safety_policy_blocked(self):
        cfg = {
            "safety": {
                "free_only_mode": True,
                "allowed_provider_types": ["gemini"],
            },
            "arbiters": [
                {"id": "arb1", "provider": {"type": "gemini", "model": "gemini-2.5-pro"}},
                {"id": "arb2", "provider": {"type": "vertex_gemini", "model": "gemini-2.5-flash"}},
            ],
        }
        # Should raise RuntimeError because vertex_gemini is not allowed
        with self.assertRaises(RuntimeError):
            validate_safety_policy(cfg)


class TestRunResume(unittest.TestCase):
    def test_fresh_disables_run_state_reuse(self):
        state = {
            "status": "running",
            "input_file": "data/input.csv",
            "config_file": "config/arbiters_config.json",
            "total_rows": 10,
        }
        args = Namespace(
            input="data/input.csv",
            config="config/arbiters_config.json",
            fresh=True,
        )

        self.assertFalse(should_reuse_run_state(state, args, 10))


class TestArbiterConfigSafety(unittest.TestCase):
    def test_api_key_override_is_migrated_and_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("labelforge_core.arbiters_io.BASE_DIR", Path(tmp)):
                arbiter = normalize_arbiter({
                    "id": "arb1",
                    "name": "Arbitro",
                    "persona": "Persona",
                    "system_prompt": "Prompt",
                    "provider": {
                        "type": "openai",
                        "model": "gpt-4o",
                        "api_key_override": "sk-test-secret",
                    },
                })

            self.assertEqual(arbiter["provider"]["api_key_env"], "OPENAI_API_KEY")
            self.assertEqual(arbiter["provider"]["api_key_override"], "")
            secrets_env = Path(tmp) / "secrets" / "secrets.env"
            self.assertIn("OPENAI_API_KEY=sk-test-secret", secrets_env.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
