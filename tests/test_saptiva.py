"""Tests del proveedor Saptiva InferNode y del modo por lotes (sin red)."""

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
    def test_end_to_end_with_fake_server(self):
        answers = {0: {"AUTH": 1, "SP": 0, "DIS": 0}, 1: {"AUTH": 0, "SP": 1, "DIS": 1}, 2: {"AUTH": 0, "SP": 0, "DIS": 0}}
        client = FakeBatchClient(answers, fail_rows={2})
        arbs = [arbiter("a1", "Saptiva Turbo"), arbiter("a2", "Saptiva Omni")]
        sleeps = []
        with tempfile.TemporaryDirectory() as tmp:
            state = saptiva_batch.BatchState(Path(tmp) / "s.json", "fp")
            results, failures = saptiva_batch.run_batches(
                arbs, {0: "p0", 1: "p1", 2: "p2"}, PRINCIPLES, lambda p: "sk-saptiva-x", state,
                poll_seconds=30, client_factory=lambda k, u: client, sleep=sleeps.append,
            )
            saved = json.loads((Path(tmp) / "s.json").read_text())
        self.assertEqual(results[0]["a1"]["AUTH"], 1)
        self.assertEqual(results[1]["a2"]["DIS"], 1)
        self.assertNotIn(2, results)
        self.assertEqual(set(failures[2]), {"a1", "a2"})
        self.assertEqual(sleeps, [30])  # una espera: primera consulta in_progress, segunda completed
        self.assertEqual(len(client.batches_store()), 2)  # un lote por arbitro
        self.assertTrue(all(j["downloaded"] for j in saved["jobs"].values()))

    def test_resume_does_not_recreate_batches(self):
        answers = {0: {"AUTH": 1, "SP": 1, "DIS": 1}}
        client = FakeBatchClient(answers)
        arbs = [arbiter("a1")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            state = saptiva_batch.BatchState(path, "fp")
            # Simula una corrida previa que alcanzo a crear el lote y se interrumpio
            client._create_file(("f", __import__("io").BytesIO(
                ("\n".join(saptiva_batch.build_batch_lines(arbs[0], {0: "p0"})) + "\n").encode())), "batch")
            client._create_batch("file-0", "/v1/chat/completions", "24h")
            state.job("a1#0").update({"batch_id": "batch_0", "status": "in_progress", "lines": 1})
            state.save()

            resumed = saptiva_batch.BatchState(path, "fp")
            results, failures = saptiva_batch.run_batches(
                arbs, {0: "p0"}, PRINCIPLES, lambda p: "k", resumed,
                client_factory=lambda k, u: client, sleep=lambda s: None,
            )
        self.assertEqual(len(client.batches_store()), 1)
        self.assertEqual(results[0]["a1"]["SP"], 1)
        self.assertEqual(failures, {})

    def test_state_with_other_fingerprint_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps({"fingerprint": "old", "jobs": {"a1#0": {"batch_id": "b"}}}))
            state = saptiva_batch.BatchState(path, "new")
            self.assertEqual(state.data["jobs"], {})


if __name__ == "__main__":
    unittest.main()
