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
    compute_majority_consensus,
    flatten_entry_consensus_metrics,
)
from labelforge_core import checkpoint_io
from labelforge_core import rate_limits
from labelforge_core.dataset_io import DatasetError, inspect_dataset, load_dataset, resolve_dataset_path
from labelforge_core.llm_json import extract_first_json_object, parse_llm_json, strip_markdown_json
from labelforge_core.run_reporting import (
    build_run_stats,
    detect_arbiters,
    detect_principles,
    principle_counts_from_rows,
    row_processing_status,
)
from labelforge_core.provider_preflight import validate_provider_preflight
from labelforge_core.prompting import (
    build_output_instruction,
    build_principles_catalog,
    build_user_prompt,
    arbiter_context_window,
    split_claim_for_arbiter,
)
from labelforge_core.usage_metrics import (
    build_entry_usage_metric_fieldnames,
    compute_entry_usage_metrics,
    entry_usage_metrics_from_row,
)
from labelforge_core.label_persuasion import (
    classify_llm_error,
    populate_arbiter_major_results,
    summarize_arbiter_checkpoints,
    should_reuse_run_state,
)
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


class TestLlmErrorClassification(unittest.TestCase):
    def test_local_context_failure_is_not_a_server_error(self):
        result = classify_llm_error(ValueError("Failed to create llama_context"))
        self.assertEqual(result["kind"], "local_context_memory")
        self.assertTrue(result["pause"])

    def test_local_model_load_failure_is_not_an_invalid_response(self):
        result = classify_llm_error(ValueError("Failed to load model from file: model.gguf"))
        self.assertEqual(result["kind"], "local_model_load")
        self.assertTrue(result["pause"])
        self.assertFalse(result["retryable"])


class TestExecutionOrder(unittest.TestCase):
    @mock.patch("labelforge_core.label_persuasion.emit_run_metric")
    @mock.patch("labelforge_core.label_persuasion.append_arbiter_result")
    @mock.patch("labelforge_core.label_persuasion.call_arbiter")
    def test_arbiter_major_finishes_all_rows_before_switching_model(
        self, call_arbiter_mock, append_result_mock, _emit_mock,
    ):
        calls = []

        def fake_call(arbiter, claim, _limiter, _task, _processing):
            calls.append((arbiter["id"], claim))
            return {"AUTH": 0, "AUTH_why": "", "_usage": {}, "_latency_s": 0.1}

        call_arbiter_mock.side_effect = fake_call
        cfg = {
            "task": {"principles": {"AUTH": "Authority"}},
            "processing": {"checkpoint_every": 10, "arbiter_major_retry_attempts": 0},
            "arbiters": [{"id": "arb1"}, {"id": "arb2"}],
        }
        partial = {}
        ok, kind, reason = populate_arbiter_major_results(
            [{"txt": "uno"}, {"txt": "dos"}], 2, "txt", cfg,
            {"arb1": object(), "arb2": object()}, partial, {}, "test-run",
        )

        self.assertTrue(ok, (kind, reason))
        self.assertEqual(
            calls,
            [("arb1", "uno"), ("arb1", "dos"), ("arb2", "uno"), ("arb2", "dos")],
        )
        self.assertEqual(append_result_mock.call_count, 4)


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

    def test_majority_consensus_is_core_contract(self):
        consensus = compute_majority_consensus(
            [{"AUTH": 1, "SP": 0}, {"AUTH": 1, "SP": 1}, {"AUTH": 0, "SP": 0}],
            ["AUTH", "SP"],
        )

        self.assertEqual(consensus, {"AUTH": 1, "SP": 0})


class TestPrompting(unittest.TestCase):
    def test_build_output_instruction_without_justifications(self):
        task_cfg = {
            "principles": {"AUTH": "Authority", "SP": "Social proof"},
            "include_justifications": False,
        }

        self.assertEqual(
            build_output_instruction(task_cfg),
            'Return JSON only: {"AUTH":0,"SP":0}',
        )

    def test_build_output_instruction_with_justifications(self):
        task_cfg = {
            "principles": {"AUTH": "Authority"},
            "include_justifications": True,
        }

        instruction = build_output_instruction(task_cfg)

        self.assertIn('"AUTH":0', instruction)
        self.assertIn('"AUTH_why":"..."', instruction)
        self.assertIn("Each *_why <=8 words", instruction)

    def test_build_user_prompt_adds_missing_sections_and_truncates(self):
        task_cfg = {
            "principles": {"AUTH": "Authority"},
            "user_prompt_template": "TEXT: {item}",
        }

        prompt = build_user_prompt(task_cfg, "abcdef", max_chars=3)

        self.assertIn(build_principles_catalog(task_cfg), prompt)
        self.assertIn("TEXT: abc", prompt)
        self.assertIn('Return JSON only: {"AUTH":0}', prompt)
        self.assertNotIn("abcdef", prompt)

    def test_build_user_prompt_zero_keeps_complete_entry(self):
        task_cfg = {
            "principles": {"AUTH": "Authority"},
            "user_prompt_template": "TEXT: {item}",
        }

        prompt = build_user_prompt(task_cfg, "abcdef", max_chars=0)

        self.assertIn("TEXT: abcdef", prompt)

    def test_context_window_is_provider_independent(self):
        proc = {"context_window_fallback": 8192}
        self.assertEqual(
            arbiter_context_window({"provider": {"type": "openai", "context_window": 128000}}, proc),
            128000,
        )
        self.assertEqual(
            arbiter_context_window({"provider": {"type": "anthropic"}}, proc),
            8192,
        )

    def test_long_entry_is_split_without_discarding_ends(self):
        task_cfg = {
            "principles": {"AUTH": "Authority"},
            "user_prompt_template": "TEXT: {item}\n{principles_catalog}\n{output_instruction}",
        }
        arbiter = {
            "system_prompt": "Classify",
            "provider": {"type": "openai", "context_window": 2048},
            "generation_config": {"max_output_tokens": 100},
        }
        claim = "BEGIN " + ("persuasion sentence. " * 600) + " END"

        chunks = split_claim_for_arbiter(
            arbiter, task_cfg,
            {"context_safety_margin_tokens": 100, "chunk_overlap_chars": 120},
            claim,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(chunks[0].startswith("BEGIN"))
        self.assertTrue(chunks[-1].endswith("END"))


class TestLlmJson(unittest.TestCase):
    def test_parse_direct_json(self):
        result, mode = parse_llm_json('{"AUTH": 1}', ["AUTH"])

        self.assertEqual(result, {"AUTH": 1})
        self.assertEqual(mode, "direct")

    def test_parse_markdown_json_with_trailing_comma(self):
        cleaned = strip_markdown_json('```json\n{"AUTH": 1,}\n```')
        result, mode = parse_llm_json('```json\n{"AUTH": 1,}\n```', ["AUTH"])

        self.assertEqual(cleaned, '{"AUTH": 1,}')
        self.assertEqual(result, {"AUTH": 1})
        self.assertEqual(mode, "trim_trailing_commas")

    def test_parse_unquoted_keys(self):
        result, mode = parse_llm_json("{AUTH: true, SP: false}", ["AUTH", "SP"])

        self.assertEqual(result, {"AUTH": True, "SP": False})
        self.assertEqual(mode, "quote_unquoted_keys")

    def test_salvage_principle_fields(self):
        raw = "AUTH: yes, AUTH_why: official claim, SP: no, SP_why: no cue"

        result, mode = parse_llm_json(raw, ["AUTH", "SP"])

        self.assertEqual(result["AUTH"], 1)
        self.assertEqual(result["SP"], 0)
        self.assertEqual(result["AUTH_why"], "official claim")
        self.assertEqual(result["SP_why"], "no cue")
        self.assertEqual(mode, "salvage_fields")

    def test_extract_first_json_object_ignores_surrounding_text(self):
        self.assertEqual(
            extract_first_json_object('preface {"AUTH": {"nested": 1}} trailing'),
            '{"AUTH": {"nested": 1}}',
        )

    def test_invalid_response_raises(self):
        with self.assertRaises(Exception):
            parse_llm_json("AUTH maybe", ["AUTH"])


class TestUsageMetrics(unittest.TestCase):
    def test_compute_entry_usage_metrics_from_arbiter_columns(self):
        record = {
            "prompt_tokens_arb1": "10",
            "completion_tokens_arb1": "5",
            "total_tokens_arb1": "15",
            "latency_s_arb1": "1.2",
            "prompt_tokens_arb2": "3",
            "completion_tokens_arb2": "2",
            "total_tokens_arb2": "5",
            "latency_s_arb2": "0.8",
        }

        metrics = compute_entry_usage_metrics(record, [{"id": "arb1"}, {"id": "arb2"}])

        self.assertEqual(metrics["entry_prompt_tokens"], 13)
        self.assertEqual(metrics["entry_completion_tokens"], 7)
        self.assertEqual(metrics["entry_total_tokens"], 20)
        self.assertEqual(metrics["entry_latency_s"], 2.0)
        self.assertEqual(metrics["entry_latency_avg_s"], 1.0)
        self.assertEqual(metrics["entry_arbiter_calls"], 2)
        self.assertEqual(metrics["entry_tokens_per_call"], 10.0)

    def test_entry_usage_metrics_from_row_prefers_stored_entry_fields(self):
        row = {
            "entry_prompt_tokens": "9",
            "entry_completion_tokens": "1",
            "entry_total_tokens": "10",
            "entry_latency_s": "0.5",
            "entry_latency_avg_s": "0.5",
            "entry_arbiter_calls": "1",
            "entry_tokens_per_call": "10",
            "prompt_tokens_arb1": "999",
        }

        metrics = entry_usage_metrics_from_row(row, ["arb1"])

        self.assertEqual(metrics["prompt_tokens"], 9)
        self.assertEqual(metrics["total_tokens"], 10)
        self.assertEqual(metrics["latency_s"], 0.5)
        self.assertEqual(metrics["arbiter_calls"], 1)

    def test_entry_usage_fieldnames_are_stable_csv_contract(self):
        self.assertEqual(
            build_entry_usage_metric_fieldnames(),
            [
                "entry_prompt_tokens",
                "entry_completion_tokens",
                "entry_total_tokens",
                "entry_latency_s",
                "entry_latency_avg_s",
                "entry_arbiter_calls",
                "entry_tokens_per_call",
            ],
        )


class TestRateLimitsJson(unittest.TestCase):
    def setUp(self):
        rate_limits._config_cache = None

    def tearDown(self):
        rate_limits._config_cache = None

    def test_repository_json_is_the_runtime_source(self):
        config = rate_limits.load_config(force=True)
        self.assertIn("gemini", config)
        self.assertIn("_default", config["gemini"])
        profile = rate_limits.get_rate_limit_profile("gemini", "modelo-no-listado", "free")
        self.assertEqual(profile["pattern_matched"], "_default")
        self.assertEqual(profile["source_label"], "config/rate_limits.json")

    def test_missing_json_fails_instead_of_inventing_defaults(self):
        missing = Path(tempfile.gettempdir()) / "labelforge-missing-rate-limits.json"
        with mock.patch.object(rate_limits, "RATE_LIMITS_CONFIG_PATH", missing):
            with self.assertRaises(FileNotFoundError):
                rate_limits.load_config(force=True)

    def test_invalid_json_structure_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rate_limits.json"
            path.write_text('{"gemini": {}}', encoding="utf-8")
            with mock.patch.object(rate_limits, "RATE_LIMITS_CONFIG_PATH", path):
                with self.assertRaises(ValueError):
                    rate_limits.load_config(force=True)


class TestLocalRuntimeErrors(unittest.TestCase):
    def test_llama_context_allocation_is_not_a_server_error(self):
        result = classify_llm_error(ValueError("Failed to create llama_context"))
        self.assertEqual(result["kind"], "local_context_memory")
        self.assertTrue(result["pause"])
        self.assertTrue(result["retryable"])


class TestArbiterCheckpointSummary(unittest.TestCase):
    def test_preserves_tokens_latency_evaluations_and_fragment_calls(self):
        summary = summarize_arbiter_checkpoints({
            "0": {"a1": {
                "_usage": {"prompt_tokens": 10, "completion_tokens": 2},
                "_latency_s": 1.25, "_chunks": 3,
            }},
        }, [{"id": "a1"}])
        self.assertEqual(summary["total_tokens_global"], 12)
        self.assertEqual(summary["arbiters"]["a1"]["evaluations"], 1)
        self.assertEqual(summary["arbiters"]["a1"]["model_calls"], 3)
        self.assertEqual(summary["arbiters"]["a1"]["latency_total_s"], 1.25)


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

    def test_higher_limit_can_reuse_legacy_run_state(self):
        state = {
            "status": "paused", "input_file": "data/input.csv",
            "config_file": "config/arbiters_config.json", "total_rows": 10,
        }
        args = Namespace(
            input="data/input.csv", config="config/arbiters_config.json", fresh=False,
        )
        self.assertTrue(should_reuse_run_state(state, args, 20))
        self.assertFalse(should_reuse_run_state(state, args, 5))


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
                "RUN_INCIDENTS_PATH": root / "run_incidents.jsonl",
                "RUN_PERFORMANCE_PATH": root / "run_performance.jsonl",
            }
            for path in paths.values():
                path.write_text("{}", encoding="utf-8")

            with mock.patch.multiple(checkpoint_io, **paths):
                cleared = checkpoint_io.clear_runtime_checkpoints(
                    include_run_state=True,
                    include_claim_results=True,
                )

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
                "RUN_INCIDENTS_PATH": root / "run_incidents.jsonl",
                "RUN_PERFORMANCE_PATH": root / "run_performance.jsonl",
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
        self.assertEqual(stats["tokens_per_processed_row"], 20)
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
