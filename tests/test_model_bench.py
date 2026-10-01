"""Regressões de avaliação, denominador e HTTP; sem GUI ou modelos reais."""
from __future__ import annotations

import importlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from evals import model_bench as bench


def response(content, finish="stop"):
    return {"model": "fake", "usage": {"completion_tokens": 12},
            "choices": [{"finish_reason": finish, "message": {"content": content}}]}


class TestModelBench(unittest.TestCase):
    def test_every_case_has_a_valid_oracle_and_no_oracle_in_prompt(self):
        cases = bench.load_cases()
        self.assertEqual(len(cases), 32)
        for case in cases:
            messages = bench.make_messages(case)
            user = json.loads(messages[1]["content"])
            self.assertNotIn("alternatives", user)
            self.assertNotIn("category", user)
            for alternative in case["alternatives"]:
                raw = dict(alternative)
                for key, value in list(raw.items()):
                    if isinstance(value, dict):
                        raw[key] = value.get("contains", value.get("one_of", [None])[0])
                if raw["type"] == "ask":
                    raw.setdefault("text", "Qual opção você deseja?")
                result = bench.evaluate_response(case, response(json.dumps(raw)), 2)
                self.assertTrue(result["passed"], (case["id"], result))

    def test_correct_type_wrong_argument_fails(self):
        case = bench.load_cases()[0]
        result = bench.evaluate_response(case, response('{"type":"hotkey","keys":"ctrl+t"}'), 1)
        self.assertTrue(result["format_valid"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_kind"], "semantic")

    def test_errors_never_disappear_from_denominator(self):
        rows = [{"passed": True, "format_valid": True, "category": "a", "elapsed_ms": 5}]
        rows += [{"error_kind": "format", "category": "a", "elapsed_ms": 10}]*9
        result = bench.summarize(rows, 10)
        self.assertEqual(result["success_rate"], .1)
        self.assertEqual(result["format_rate"], .1)
        self.assertEqual(result["errors"]["format"], 9)
        self.assertEqual(bench.summarize(rows[:1], 10)["not_attempted"], 9)
        self.assertFalse(bench.summarize(rows[:1], 10)["complete"])

    def test_truncated_valid_json_is_failure(self):
        result = bench.evaluate_response(bench.load_cases()[0],
                                        response('{"type":"hotkey","keys":"ctrl+l"}', "length"), 1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_kind"], "truncated")

    def test_closed_thinking_and_fence_are_distinct_from_pure_json(self):
        raw, pure = bench.parse_response('<think>análise</think>\n```json\n{"type":"ask"}\n```')
        self.assertFalse(pure)
        self.assertEqual(raw["type"], "ask")
        with self.assertRaises(ValueError):
            bench.parse_response('<think>reasoning incompleto {"type":"done"}')
        with self.assertRaises(ValueError):
            bench.parse_response('{"type":"ask"}{"type":"done"}')

    def test_no_coords_missing_args_or_arbitrary_sequence(self):
        for raw in ({"type": "uia_click", "x": .5, "y": .5},
                    {"type": "type_text"}, {"type": "wait", "ms": -1},
                    {"type": "open_app", "app": "powershell"},
                    {"type": "sequence", "steps": [{"type": "open_app", "app": "chrome"}]}):
            with self.assertRaises(ValueError):
                bench.validate_decision(raw)

    def test_resolve_models_exact_substring_single_and_ambiguous(self):
        avail = ["minicpm5-1b-v2", "minicpm5-2b", "qwen3-vl-2b"]
        self.assertEqual(bench.resolve_models(["qwen"], avail), ["qwen3-vl-2b"])
        self.assertEqual(bench.resolve_models(["minicpm5-2b"], avail), ["minicpm5-2b"])
        self.assertEqual(bench.resolve_models(None, ["só"]), ["só"])
        for requested in (None, ["minicpm"], ["nada"]):
            with self.assertRaises(ValueError):
                bench.resolve_models(requested, avail)

    def test_aborts_after_consecutive_infra_failures(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(500, text="fora")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            rows = bench.run_requests(client, "http://127.0.0.1/v1", "fake",
                                      bench.load_cases(), 3,
                                      {"format": "prompt", "temperature": .1,
                                       "max_tokens": 100, "thinking": "native"}, lambda _: None)
        self.assertEqual(len(rows), bench.MAX_CONSECUTIVE_INFRA)
        self.assertEqual(len(calls), bench.MAX_CONSECUTIVE_INFRA)

    def test_summary_lists_always_failed_cases(self):
        rows = [{"case_id": "a", "category": "x", "passed": True, "elapsed_ms": 1},
                {"case_id": "a", "category": "x", "error_kind": "semantic", "elapsed_ms": 1},
                {"case_id": "b", "category": "x", "error_kind": "format", "elapsed_ms": 1}]
        self.assertEqual(bench.summarize(rows, 3)["always_failed"], ["b"])

    def test_done_rejects_existing_evidence_with_pending_requirement(self):
        case = next(c for c in bench.load_cases() if c["id"] == "salvar-pendente")
        result = bench.evaluate_response(case, response('{"type":"done","evidences":["ev-texto"]}'), 1)
        self.assertFalse(result["passed"])
        self.assertTrue(any("cobertura" in e for e in result["errors"]))

    def test_http_records_errors_and_does_not_retry_inputs(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                return httpx.Response(500, text="modelo não carregado")
            return httpx.Response(200, json=response('{"type":"hotkey","keys":"ctrl+l"}'))

        emitted = []
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            rows = bench.run_requests(client, "http://127.0.0.1/v1", "fake",
                                      bench.load_cases()[:1], 2,
                                      {"format": "prompt", "temperature": .1,
                                       "max_tokens": 100, "thinking": "native"}, emitted.append)
        self.assertEqual(len(calls), 2)
        self.assertEqual(rows[0]["error_kind"], "infra")
        self.assertTrue(rows[1]["passed"])
        self.assertEqual(rows, emitted)
        self.assertNotIn("response_format", calls[0])
        self.assertNotIn("chat_template_kwargs", calls[0])

    def test_schema_and_thinking_are_explicit_no_silent_fallback(self):
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(400, text="schema não suportado")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            rows = bench.run_requests(client, "http://127.0.0.1/v1", "fake",
                                      bench.load_cases()[:1], 1,
                                      {"format": "schema", "temperature": .1,
                                       "max_tokens": 100, "thinking": "off"}, lambda _: None)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["response_format"]["type"], "json_schema")
        self.assertFalse(payloads[0]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(rows[0]["error_kind"], "infra")

    def test_cli_writes_portable_bundle_and_refuses_overwrite(self):
        original_client = httpx.Client

        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "fake"}]})
            return httpx.Response(200, json=response('{"type":"hotkey","keys":"ctrl+l"}'))

        def client(**kwargs):
            return original_client(transport=httpx.MockTransport(handler), **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(bench.httpx, "Client", client):
            out = Path(temp) / "report"
            args = ["--model", "fake", "--limit", "1", "--reps", "1", "--out", str(out)]
            self.assertEqual(bench.main(args), 0)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["results"][0]["summary"]["success_rate"], 1)
            with zipfile.ZipFile(out / "resultado.zip") as archive:
                self.assertEqual(set(archive.namelist()),
                                 {"rows.jsonl", "summary.json", "cases.json", "relatorio.md"})
            self.assertEqual(bench.main(args), 2)

    def test_local_only(self):
        for url in ("https://example.org/v1", "http://127.0.0.1.evil/v1", "file:///tmp/a"):
            with self.assertRaises(ValueError):
                bench.local_url(url)
        self.assertEqual(bench.local_url("http://localhost:1234/v1/"), "http://localhost:1234/v1")

    def test_interrupt_saves_partial_report(self):
        original_client = httpx.Client

        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "fake"}]})
            raise KeyboardInterrupt

        def client(**kwargs):
            return original_client(transport=httpx.MockTransport(handler), **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(bench.httpx, "Client", client):
            out = Path(temp) / "partial"
            self.assertEqual(bench.main(["--model", "fake", "--out", str(out)]), 130)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["manifest"]["interrupted"])
            self.assertEqual(summary["results"][0]["summary"]["not_attempted"], 96)
            self.assertEqual(summary["results"][0]["summary"]["success_rate"], 0)
            self.assertTrue((out / "resultado.zip").is_file())

    def test_import_does_not_load_gui_or_agent_runtime(self):
        import sys

        before = set(sys.modules)
        importlib.reload(bench)
        added = set(sys.modules) - before
        self.assertFalse(added & {"loop", "actions", "pyautogui", "server", "uia"})


if __name__ == "__main__":
    unittest.main()
