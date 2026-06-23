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
from labelforge_core.agreement_metrics import (
    cohen_kappa_for_pairs,
    compute_agreement_metrics,
    compute_consensus_metrics,
    compute_entry_consensus_metrics,
    flatten_entry_consensus_metrics,
)
from labelforge_core import checkpoint_io
from labelforge_core.dataset_io import DatasetError, inspect_dataset, load_dataset, resolve_dataset_path
from labelforge_core.run_reporting import (
    build_run_stats,
    detect_arbiters,
    detect_principles,
    principle_counts_from_rows,
    row_processing_status,
)
from labelforge_core.provider_preflight import validate_provider_preflight
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

    def test_entry_consensus_metrics_are_core_contract(self):
        record = {
            "AUTH_arb1": 1,
            "AUTH_arb2": 1,
            "AUTH_arb3": 0,
            "AUTH_consensus": 1,
            "SP_arb1": 0,
            "SP_arb2": 0,
            "SP_arb3": 0,
            "SP_consensus": 0,
        }
        arbiters = [{"id": "arb1"}, {"id": "arb2"}, {"id": "arb3"}]
        principles = ["AUTH", "SP"]

        metrics = compute_entry_consensus_metrics(record, arbiters, principles)
        flat = flatten_entry_consensus_metrics(metrics, principles)

        self.assertEqual(metrics["observed_agreement"], 0.6667)
        self.assertEqual(metrics["unanimous_principles"], 1)
        self.assertEqual(metrics["split_principles"], 1)
        self.assertEqual(flat["AUTH_entry_consensus_support"], 2)
        self.assertEqual(flat["SP_entry_consensus_unanimous"], True)

    def test_cohen_kappa_for_pairs_is_shared(self):
        self.assertEqual(cohen_kappa_for_pairs([(1, 1), (0, 0)]), 1.0)


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


class TestProviderPreflight(unittest.TestCase):
    def test_non_local_config_does_not_require_hw_profile(self):
        cfg = {
            "arbiters": [
                {
                    "id": "remote1",
                    "provider": {
                        "type": "openai_compat",
                        "model": "local-server-model",
                        "base_url": "http://localhost:11434/v1",
                    },
                }
            ]
        }

        with mock.patch("labelforge_core.provider_preflight.HW_PROFILE_PATH", Path("missing-hw-profile.json")):
            self.assertEqual(validate_provider_preflight(cfg), [])


class TestCheckpointIO(unittest.TestCase):
    def test_clear_runtime_checkpoints_can_include_run_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "CHECKPOINT_PATH": root / "checkpoint.json",
                "FAILED_QUEUE_PATH": root / "failed_queue.jsonl",
                "CLAIM_RESULTS_PATH": root / "claim_results.jsonl",
                "ARBITER_RESULTS_PATH": root / "arbiter_results.jsonl",
                "RUN_STATE_PATH": root / "run_state.json",
            }
            for path in paths.values():
                path.write_text("{}", encoding="utf-8")

            with mock.patch.multiple(checkpoint_io, **paths):
                cleared = checkpoint_io.clear_runtime_checkpoints(include_run_state=True)

            self.assertEqual(set(cleared), {p.name for p in paths.values()})
            self.assertFalse(any(path.exists() for path in paths.values()))

    def test_load_checkpoint_merges_incremental_claim_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "CHECKPOINT_PATH": root / "checkpoint.json",
                "FAILED_QUEUE_PATH": root / "failed_queue.jsonl",
                "CLAIM_RESULTS_PATH": root / "claim_results.jsonl",
                "ARBITER_RESULTS_PATH": root / "arbiter_results.jsonl",
                "RUN_STATE_PATH": root / "run_state.json",
            }

            with mock.patch.multiple(checkpoint_io, **paths):
                checkpoint_io.save_checkpoint(0, {"0": {"AUTH_consensus": 0}})
                checkpoint_io.append_claim_result(1, {"AUTH_consensus": 1}, "ok")
                loaded = checkpoint_io.load_checkpoint()

            self.assertEqual(loaded["last_index"], 1)
            self.assertEqual(loaded["results"]["0"]["AUTH_consensus"], 0)
            self.assertEqual(loaded["results"]["1"]["AUTH_consensus"], 1)


class TestDatasetIO(unittest.TestCase):
    def test_load_and_inspect_csv_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_text("claim,class\nhello,ok\nbye,no\n", encoding="utf-8")

            rows, fieldnames = load_dataset(path, text_column="claim")
            info = inspect_dataset(path)

            self.assertEqual(fieldnames, ["claim", "class"])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["claim"], "hello")
            self.assertEqual(info["columns"], ["claim", "class"])
            self.assertFalse(info["is_excel"])

    def test_load_dataset_requires_text_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_text("text\nhello\n", encoding="utf-8")

            with self.assertRaises(DatasetError):
                load_dataset(path, text_column="claim")

    def test_resolve_dataset_path_restricts_allowed_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            root.mkdir()
            allowed = root / "ok.csv"
            outside = Path(tmp) / "no.csv"
            allowed.write_text("claim\nok\n", encoding="utf-8")
            outside.write_text("claim\nno\n", encoding="utf-8")

            self.assertEqual(resolve_dataset_path(allowed, allowed_root=root), allowed.resolve())
            with self.assertRaises(PermissionError):
                resolve_dataset_path(outside, allowed_root=root)


class TestRunReporting(unittest.TestCase):
    def test_detects_schema_and_counts_principles(self):
        fieldnames = [
            "claim",
            "AUTH_arb1",
            "AUTH_arb2",
            "AUTH_consensus",
            "SC_arb1",
            "SC_arb2",
            "SC_consensus",
            "prompt_tokens_arb1",
            "prompt_tokens_arb2",
        ]
        rows = [
            {"AUTH_consensus": "1", "SC_consensus": "0"},
            {"AUTH_consensus": "0", "SC_consensus": "1"},
            {"AUTH_consensus": "-1", "SC_consensus": "-1"},
            {},
        ]

        self.assertEqual(detect_principles(fieldnames), ["AUTH", "SC"])
        self.assertEqual(detect_arbiters(fieldnames), ["arb1", "arb2"])
        self.assertEqual(row_processing_status(rows[0], ["AUTH", "SC"]), "ok")
        self.assertEqual(row_processing_status(rows[2], ["AUTH", "SC"]), "failed")
        self.assertEqual(row_processing_status(rows[3], ["AUTH", "SC"]), "pending")
        self.assertEqual(
            principle_counts_from_rows(rows, ["AUTH", "SC"]),
            {"AUTH": {"ones": 1, "zeros": 1}, "SC": {"ones": 1, "zeros": 1}},
        )

    def test_build_run_stats_matches_output_contract(self):
        rows = [
            {
                "claim": "A",
                "class": "fake",
                "AUTH_arb1": "1",
                "AUTH_arb2": "1",
                "AUTH_consensus": "1",
                "prompt_tokens_arb1": "10",
                "completion_tokens_arb1": "2",
                "total_tokens_arb1": "12",
                "latency_s_arb1": "0.5",
                "prompt_tokens_arb2": "9",
                "completion_tokens_arb2": "1",
                "total_tokens_arb2": "10",
                "latency_s_arb2": "0.25",
            },
            {
                "claim": "B",
                "class": "true",
                "AUTH_arb1": "0",
                "AUTH_arb2": "1",
                "AUTH_consensus": "1",
                "prompt_tokens_arb1": "7",
                "completion_tokens_arb1": "1",
                "total_tokens_arb1": "8",
                "latency_s_arb1": "0.4",
                "prompt_tokens_arb2": "8",
                "completion_tokens_arb2": "1",
                "total_tokens_arb2": "9",
                "latency_s_arb2": "0.3",
            },
        ]
        fieldnames = list(rows[0].keys())

        stats = build_run_stats(rows, fieldnames, "labeled_test.csv", result_limit=1)

        self.assertEqual(stats["source_file"], "labeled_test.csv")
        self.assertEqual(stats["total_rows"], 2)
        self.assertEqual(stats["successful_rows"], 2)
        self.assertEqual(stats["total_tokens_global"], 39)
        self.assertEqual(stats["results_count"], 1)
        self.assertEqual(stats["results"][0]["index"], 1)
        self.assertEqual(stats["arbiters"]["arb1"]["total_tokens"], 20)
        self.assertIn("paper_summary", stats)


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
