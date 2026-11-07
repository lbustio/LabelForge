"""Tests del proveedor Saptiva InferNode y del modo por lotes (sin red)."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from labelforge_core import saptiva, saptiva_batch
from labelforge_core.provider_preflight import validate_provider_preflight

PRINCIPLES = ["AUTH", "SP", "DIS"]


def arbiter(arb_id="arb1", model="Saptiva Turbo", **gen):
    return {
        "id": arb_id,
        "system_prompt": "Return JSON only.",
        "provider": {"type": "saptiva", "model": model, "api_key_env": "SAPTIVA_API_KEY"},
        "generation_config": {"temperature": 0.1, "max_output_tokens": 256, **gen},
    }


class TestSaptivaRequest(unittest.TestCase):
    def test_body_turns_off_reasoning_by_default(self):
        body = saptiva.build_chat_body(arbiter(), "TEXT: hola")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["model"], "Saptiva Turbo")
        self.assertEqual(body["max_tokens"], 256)
        self.assertEqual(body["messages"][1]["content"], "TEXT: hola")

    def test_reasoning_can_be_enabled_per_arbiter(self):
        body = saptiva.build_chat_body(arbiter(reasoning=True), "x")
        self.assertNotIn("chat_template_kwargs", body)

    def test_base_url_defaults_to_ibero_lab(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SAPTIVA_BASE_URL", None)
            self.assertEqual(saptiva.resolve_base_url({}), "https://api.laboratorioia.ibero.mx/infernode/v1")
            self.assertEqual(saptiva.resolve_base_url({"base_url": "https://api.x.mx/infernode/v1/"}),
                             "https://api.x.mx/infernode/v1")

    def test_empty_content_from_reasoning_is_explicit_error(self):
        with self.assertRaises(saptiva.EmptyCompletionError) as ctx:
            saptiva.extract_content({"content": None, "reasoning": "pensando..."}, "length")
        self.assertIn("razonamiento", str(ctx.exception))
        self.assertIsInstance(ctx.exception, ValueError)  # se clasifica como invalid_response

    def test_call_chat_sends_extra_body(self):
        fake = mock.MagicMock()
        fake.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(model_dump=lambda: {"content": '{"AUTH":1,"SP":0,"DIS":1}'}),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=12),
        )
        with mock.patch.object(saptiva, "make_client", return_value=fake):
            text, usage = saptiva.call_chat(arbiter(), "TEXT: x", "sk-saptiva-test")
        kwargs = fake.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("chat_template_kwargs", kwargs)
        self.assertEqual(text, '{"AUTH":1,"SP":0,"DIS":1}')
        self.assertEqual(usage, {"prompt_tokens": 50, "completion_tokens": 12})


class TestSaptivaPreflight(unittest.TestCase):
    def test_missing_key_blocks_run(self):
        cfg = {"arbiters": [arbiter()]}
        with mock.patch.dict(os.environ, {"SAPTIVA_API_KEY": ""}):
            issues = validate_provider_preflight(cfg)
        self.assertEqual(len(issues), 1)
        self.assertIn("SAPTIVA_API_KEY", issues[0])

    def test_key_present_passes(self):
        cfg = {"arbiters": [arbiter()]}
        with mock.patch.dict(os.environ, {"SAPTIVA_API_KEY": "sk-saptiva-x"}):
            self.assertEqual(validate_provider_preflight(cfg), [])


class TestDistinctModels(unittest.TestCase):
    def _cfg(self, models, required=True):
        return {"safety": {"require_distinct_models": required},
                "arbiters": [arbiter(f"a{i}", m) for i, m in enumerate(models)]}

    def test_repeated_model_blocks_run(self):
        with mock.patch.dict(os.environ, {"SAPTIVA_API_KEY": "sk-saptiva-x"}):
            issues = validate_provider_preflight(self._cfg(["Saptiva Turbo", "Saptiva Omni", "saptiva turbo"]))
        self.assertEqual(len(issues), 1)
        self.assertIn("a2", issues[0])
        self.assertIn("a0", issues[0])

    def test_three_distinct_models_pass(self):
        with mock.patch.dict(os.environ, {"SAPTIVA_API_KEY": "sk-saptiva-x"}):
            self.assertEqual(validate_provider_preflight(self._cfg(["Saptiva Turbo", "Saptiva Omni", "Saptiva Cortex"])), [])

    def test_rule_is_opt_in(self):
        with mock.patch.dict(os.environ, {"SAPTIVA_API_KEY": "sk-saptiva-x"}):
            self.assertEqual(validate_provider_preflight(self._cfg(["Saptiva Turbo"] * 2, required=False)), [])

    def test_lab_config_uses_three_distinct_models(self):
        cfg = json.loads((ROOT / "config/arbiters_config.saptiva.json").read_text(encoding="utf-8"))
        self.assertTrue(cfg["safety"]["require_distinct_models"])
        models = [a["provider"]["model"] for a in cfg["arbiters"]]
        self.assertEqual(sorted(models), ["Saptiva Cortex", "Saptiva Omni", "Saptiva Turbo"])


class TestArbiterEditKeepsReasoning(unittest.TestCase):
    def test_webapp_edit_keeps_reasoning_and_seed(self):
        from labelforge_core.arbiters_io import normalize_arbiter
        existing = arbiter("arb3", "Saptiva Omni", reasoning=True, reasoning_budget=1024, seed=42)
        edited = normalize_arbiter({"name": "Nuevo nombre", "generation_config": {"temperature": 0.0, "max_output_tokens": 2048}}, existing)
        gc = edited["generation_config"]
        self.assertEqual((gc["reasoning"], gc["reasoning_budget"], gc["seed"]), (True, 1024, 42))
        self.assertEqual((gc["temperature"], gc["max_output_tokens"]), (0.0, 2048))


class TestBatchFiles(unittest.TestCase):
    def test_lines_follow_batch_contract(self):
        lines = saptiva_batch.build_batch_lines(arbiter(), {3: "p3", 1: "p1"})
        parsed = [json.loads(l) for l in lines]
        self.assertEqual([p["custom_id"] for p in parsed], ["row-1", "row-3"])
        for p in parsed:
            self.assertEqual(p["method"], "POST")
            self.assertEqual(p["url"], "/v1/chat/completions")
            self.assertNotIn("stream", p["body"])
            self.assertEqual(p["body"]["chat_template_kwargs"], {"enable_thinking": False})

    def test_chunking_respects_line_and_byte_limits(self):
        lines = ["x" * 10] * 7
        self.assertEqual([len(c) for c in saptiva_batch.chunk_lines(lines, max_lines=3)], [3, 3, 1])
        self.assertEqual([len(c) for c in saptiva_batch.chunk_lines(lines, max_bytes=25)], [2, 2, 2, 1])

    def test_parse_success_error_and_bad_json(self):
        ok = {"custom_id": "row-4", "error": None, "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": '{"AUTH":1,"SP":"0","DIS":2}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3}}}}
        idx, res, reason = saptiva_batch.parse_result_line(ok, PRINCIPLES)
        self.assertEqual((idx, reason), (4, ""))
        self.assertEqual((res["AUTH"], res["SP"], res["DIS"]), (1, 0, 1))
        self.assertEqual(res["_usage"], {"prompt_tokens": 9, "completion_tokens": 3})

        cancelled = {"custom_id": "row-5", "response": None, "error": {"code": "batch_cancelled"}}
        idx, res, reason = saptiva_batch.parse_result_line(cancelled, PRINCIPLES)
        self.assertEqual(idx, 5)
        self.assertIsNone(res)
        self.assertIn("batch_cancelled", reason)

        empty = {"custom_id": "row-6", "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": None, "reasoning": "..."}, "finish_reason": "length"}]}}}
        idx, res, reason = saptiva_batch.parse_result_line(empty, PRINCIPLES)
        self.assertIsNone(res)
        self.assertIn("respuesta invalida", reason)


class FakeBatchClient:
    """Imita files/batches del SDK de OpenAI para un servidor InferNode."""

    def __init__(self, answers: dict, fail_rows=()):
        self.answers = answers
        self.fail_rows = set(fail_rows)
        self.uploaded = {}
        self.polls = 0
        self.files = SimpleNamespace(create=self._create_file, content=self._content)
        self.batches = SimpleNamespace(create=self._create_batch, retrieve=self._retrieve)

    def _create_file(self, file, purpose):
        assert purpose == "batch"
        fid = f"file-{len(self.uploaded)}"
        self.uploaded[fid] = file[1].read().decode("utf-8")
        return SimpleNamespace(id=fid)

    def _create_batch(self, input_file_id, endpoint, completion_window, metadata=None):
        assert endpoint == "/v1/chat/completions" and completion_window == "24h"
        bid = f"batch_{len(self.batches_store())}"
        self.batches_store()[bid] = {"input": input_file_id, "polls": 0}
        return SimpleNamespace(id=bid, status="validating")

    def batches_store(self):
        return self.__dict__.setdefault("_store", {})

    def _retrieve(self, bid):
        b = self.batches_store()[bid]
        b["polls"] += 1
        self.polls += 1
        if b["polls"] < 2:
            return SimpleNamespace(status="in_progress", output_file_id=None, error_file_id=None,
                                   request_counts=SimpleNamespace(completed=0, failed=0), errors=None)
        out, err = [], []
        for raw in self.uploaded[b["input"]].splitlines():
            line = json.loads(raw)
            idx = int(line["custom_id"][4:])
            if idx in self.fail_rows:
                err.append({"custom_id": line["custom_id"], "response": {"status_code": 500, "body": {"error": "boom"}}, "error": None})
            else:
                out.append({"custom_id": line["custom_id"], "error": None, "response": {"status_code": 200, "body": {
                    "choices": [{"message": {"content": json.dumps(self.answers[idx])}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}}})
        self.uploaded[f"{bid}-out"] = "\n".join(json.dumps(x) for x in out)
        self.uploaded[f"{bid}-err"] = "\n".join(json.dumps(x) for x in err)
        return SimpleNamespace(status="completed", output_file_id=f"{bid}-out",
                               error_file_id=f"{bid}-err" if err else None,
                               request_counts=SimpleNamespace(completed=len(out), failed=len(err)), errors=None)

    def _content(self, fid):
        return SimpleNamespace(text=self.uploaded[fid])


class TestRunBatches(unittest.TestCase):
    def _run(self, client, arbs, prompts_by_arb, state, **kw):
        return saptiva_batch.run_batches(
            arbs, prompts_by_arb, PRINCIPLES, lambda p: "sk-saptiva-x", state,
            poll_seconds=30, client_factory=lambda k, u: client, **kw,
        )

    def test_end_to_end_with_fake_server(self):
        answers = {0: {"AUTH": 1, "SP": 0, "DIS": 0}, 1: {"AUTH": 0, "SP": 1, "DIS": 1}, 2: {"AUTH": 0, "SP": 0, "DIS": 0}}
        client = FakeBatchClient(answers, fail_rows={2})
        arbs = [arbiter("a1", "Saptiva Turbo"), arbiter("a2", "Saptiva Omni")]
        prompts = {0: "p0", 1: "p1", 2: "p2"}
        sleeps, stored = [], []
        with tempfile.TemporaryDirectory() as tmp:
            state = saptiva_batch.BatchState(Path(tmp) / "s.json", "fp")
            results, failures = self._run(client, arbs, {"a1": prompts, "a2": prompts}, state,
                                          sleep=sleeps.append, on_result=lambda i, a, r: stored.append((i, a)))
            saved = json.loads((Path(tmp) / "s.json").read_text())
        self.assertEqual(results[0]["a1"]["AUTH"], 1)
        self.assertEqual(results[1]["a2"]["DIS"], 1)
        self.assertNotIn(2, results)
        self.assertEqual(set(failures[2]), {"a1", "a2"})
        self.assertEqual(sleeps, [30])
        self.assertEqual(len(client.batches_store()), 2)  # un lote por arbitro
        self.assertEqual(sorted(stored), [(0, "a1"), (0, "a2"), (1, "a1"), (1, "a2")])
        self.assertTrue(all(j["downloaded"] and j["rows"] == [0, 1, 2] for j in saved["jobs"].values()))

    def test_already_resolved_rows_are_not_resent(self):
        client = FakeBatchClient({0: {"AUTH": 1, "SP": 0, "DIS": 0}, 1: {"AUTH": 0, "SP": 0, "DIS": 0}})
        arbs = [arbiter("a1"), arbiter("a2")]
        with tempfile.TemporaryDirectory() as tmp:
            state = saptiva_batch.BatchState(Path(tmp) / "s.json", "fp")
            self._run(client, arbs, {"a1": {0: "p0", 1: "p1"}, "a2": {1: "p1"}}, state, sleep=lambda s: None)
        sent = [len(t.splitlines()) for k, t in client.uploaded.items() if k.startswith("file-")]
        self.assertEqual(sorted(sent), [1, 2])

    def test_arbiter_without_pending_rows_creates_no_batch(self):
        client = FakeBatchClient({0: {"AUTH": 1, "SP": 0, "DIS": 0}})
        with tempfile.TemporaryDirectory() as tmp:
            state = saptiva_batch.BatchState(Path(tmp) / "s.json", "fp")
            self._run(client, [arbiter("a1"), arbiter("a2")], {"a1": {0: "p0"}, "a2": {}}, state, sleep=lambda s: None)
        self.assertEqual(len(client.batches_store()), 1)

    def test_resume_uses_saved_rows_even_if_pending_set_shrank(self):
        answers = {0: {"AUTH": 1, "SP": 1, "DIS": 1}, 1: {"AUTH": 0, "SP": 0, "DIS": 1}}
        client = FakeBatchClient(answers)
        arbs = [arbiter("a1")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            state = saptiva_batch.BatchState(path, "fp")
            lines = saptiva_batch.build_batch_lines(arbs[0], {0: "p0", 1: "p1"})
            client._create_file(("f", io.BytesIO(("\n".join(lines) + "\n").encode())), "batch")
            client._create_batch("file-0", "/v1/chat/completions", "24h")
            state.job("a1#0").update({"arbiter_id": "a1", "rows": [0, 1], "lines": 2,
                                      "input_file_id": "file-0", "batch_id": "batch_0", "status": "in_progress"})
            state.save()

            resumed = saptiva_batch.BatchState(path, "fp")
            # La fila 0 se termino entre corridas: solo queda la 1 pendiente
            results, failures = self._run(client, arbs, {"a1": {1: "p1"}}, resumed, sleep=lambda s: None)
        self.assertEqual(len(client.batches_store()), 1)  # no se crea un lote nuevo
        self.assertEqual(results[1]["a1"]["DIS"], 1)
        self.assertEqual(failures, {})

    def test_uploaded_file_is_reused_if_batch_creation_failed(self):
        client = FakeBatchClient({0: {"AUTH": 1, "SP": 0, "DIS": 0}})
        real_create = client.batches.create
        calls = {"n": 0}

        def flaky_create(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("503 Service Unavailable")
            return real_create(**kw)

        client.batches.create = flaky_create
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            with self.assertRaises(RuntimeError):
                self._run(client, [arbiter("a1")], {"a1": {0: "p0"}}, saptiva_batch.BatchState(path, "fp"), sleep=lambda s: None)
            self._run(client, [arbiter("a1")], {"a1": {0: "p0"}}, saptiva_batch.BatchState(path, "fp"), sleep=lambda s: None)
        self.assertEqual(len([k for k in client.uploaded if k.startswith("file-")]), 1)

    def test_transient_poll_errors_do_not_abort(self):
        client = FakeBatchClient({0: {"AUTH": 1, "SP": 0, "DIS": 0}})
        real_retrieve = client.batches.retrieve
        calls = {"n": 0}

        class Unavailable(Exception):
            status_code = 503

        def flaky_retrieve(bid):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Unavailable("503")
            return real_retrieve(bid)

        client.batches.retrieve = flaky_retrieve
        with tempfile.TemporaryDirectory() as tmp:
            results, failures = self._run(client, [arbiter("a1")], {"a1": {0: "p0"}},
                                          saptiva_batch.BatchState(Path(tmp) / "s.json", "fp"), sleep=lambda s: None)
        self.assertEqual(results[0]["a1"]["AUTH"], 1)

    def test_state_with_other_fingerprint_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps({"fingerprint": "old", "jobs": {"a1#0": {"batch_id": "b"}}}))
            state = saptiva_batch.BatchState(path, "new")
            self.assertEqual(state.data["jobs"], {})

    def test_fingerprint_changes_with_dataset_content_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "d.csv"
            cfg = {"task": {"user_prompt_template": "A"}, "processing": {"claim_max_chars": 3000}, "arbiters": [arbiter()]}
            data.write_text("claim\nhola\n", encoding="utf-8")
            fp1 = saptiva_batch.run_fingerprint(str(data), cfg, 1)
            data.write_text("claim\nadios\n", encoding="utf-8")
            fp2 = saptiva_batch.run_fingerprint(str(data), cfg, 1)
            cfg["task"]["user_prompt_template"] = "B"
            fp3 = saptiva_batch.run_fingerprint(str(data), cfg, 1)
        self.assertEqual(len({fp1, fp2, fp3}), 3)


class TestReasoningBudget(unittest.TestCase):
    def test_budget_and_seed_go_in_body(self):
        body = saptiva.build_chat_body(arbiter(reasoning=True, reasoning_budget=256, seed=7), "x")
        self.assertEqual(body["thinking_token_budget"], 256)
        self.assertEqual(body["seed"], 7)
        self.assertNotIn("chat_template_kwargs", body)

    def test_budget_ignored_when_reasoning_off(self):
        body = saptiva.build_chat_body(arbiter(reasoning_budget=256), "x")
        self.assertNotIn("thinking_token_budget", body)


class TestCsvEncoding(unittest.TestCase):
    def test_latin1_csv_keeps_accents(self):
        from labelforge_core.dataset_io import load_dataset
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l1.csv"
            path.write_bytes("txt,label\nCorreo leg\u00edtimo,Leg\u00edtimo\n".encode("latin-1"))
            rows, _ = load_dataset(path, text_column="txt")
        self.assertEqual(rows[0]["label"], "Leg\u00edtimo")

    def test_utf8_bom_csv(self):
        from labelforge_core.dataset_io import load_dataset
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "u8.csv"
            path.write_bytes("\ufefftxt\nacci\u00f3n\n".encode("utf-8"))
            rows, fields = load_dataset(path, text_column="txt")
        self.assertEqual((fields, rows[0]["txt"]), (["txt"], "acci\u00f3n"))


if __name__ == "__main__":
    unittest.main()
