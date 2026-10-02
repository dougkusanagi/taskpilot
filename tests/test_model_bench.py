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
                    if isinstance(value, dict) and key != "args":
                        samples = {
                            "money_brl": "R$ " + value.get("money_brl", ""),
                            "click_target": "Click " + value.get("click_target", ""),
                            "question_has": {
                                "pessoas-ambiguas": "Qual perfil de Ana: pessoal ou trabalho?",
                                "terminal-indisponivel": "O terminal está indisponível. Posso listar os arquivos pela interface?",
                            }.get(case["id"]),
                            "question_pattern": {
                                "pessoas-ambiguas": "Qual perfil de Ana: pessoal ou trabalho?",
                                "terminal-indisponivel": "O terminal está indisponível. Posso listar os arquivos pela interface?",
                            }.get(case["id"]),
                        }
                        raw[key] = samples[next(iter(value))]
                result = bench.evaluate_response(case, response(json.dumps(raw)), 2)
                self.assertTrue(result["passed"], (case["id"], result))

    def evaluated(self, case_id, raw):
        case = next(c for c in bench.load_cases() if c["id"] == case_id)
        return bench.evaluate_response(case, response(json.dumps(raw, ensure_ascii=False)), 1)

    def test_price_rejects_negation_conflicting_values_and_other_currency(self):
        for text in ("O preço não é R$ 1.249,90; é R$ 9.999,00.",
                     "R$ 1.249,90 ou R$ 2.499,00", "US$ 1.249,90",
                     "Compre por R$ 1.249,90", "R$ 11.249,90"):
            with self.subTest(text=text):
                self.assertFalse(self.evaluated("preco-um", {"type": "answer", "text": text})["passed"])
        for text in ("R$ 1.249,90", "O preço visível é R$ 1.249,90.", "Valor: R$ 1.249,90",
                     "O preço é de R$ 1249,90", "O preço visível da oferta é R$ 1.249,90"):
            with self.subTest(text=text):
                self.assertTrue(self.evaluated("preco-um", {"type": "answer", "text": text})["passed"])

    def test_questions_must_clarify_actual_ambiguity_or_unavailable_capability(self):
        for case_id, text in (
            ("pessoas-ambiguas", "Qual é a capital da França?"),
            ("pessoas-ambiguas", "Ana pessoal ou Ana trabalho ficam na França?"),
            ("pessoas-ambiguas", "Vou abrir Ana pessoal, tudo bem?"),
            ("terminal-indisponivel", "Qual terminal você quer usar?"),
            ("terminal-indisponivel", "Qual é a capital da França?"),
        ):
            with self.subTest(case_id=case_id, text=text):
                self.assertFalse(self.evaluated(case_id, {"type": "ask", "text": text})["passed"])
        for text in ("Qual perfil de Ana: pessoal ou trabalho?",
                     "Você prefere Ana pessoal ou Ana trabalho?",
                     "Qual perfil da Ana você deseja abrir - pessoal ou trabalho?",
                     "Qual é o perfil de Ana que deseja abrir? (pessoal ou trabalho)"):
            self.assertTrue(self.evaluated("pessoas-ambiguas", {"type": "ask", "text": text})["passed"])
        for text in ("Qual perfil da Ana devo abrir?", "Qual perfil você quer abrir?"):
            # cita só uma opção ou nenhuma: não esclarece a ambiguidade entre os dois perfis
            self.assertFalse(self.evaluated("pessoas-ambiguas", {"type": "ask", "text": text})["passed"])
        for text in ("O terminal não está disponível. Posso listar os arquivos pela interface gráfica?",
                     "Não há aplicativo de terminal disponível. Prefere outra abordagem?"):
            self.assertTrue(self.evaluated("terminal-indisponivel", {"type": "ask", "text": text})["passed"])
        for text in ("Como acessar o terminal?", "Onde devo abrir o terminal?"):
            self.assertFalse(self.evaluated("terminal-indisponivel", {"type": "ask", "text": text})["passed"])

    def test_visual_instruction_requires_affirmative_click_on_correct_target(self):
        for text in ("Do not click Enviar. Click Cancelar.", "Click Cancelar instead of Enviar",
                     "Enviar", "Click Enviar and delete everything", "Clique em Reenviar"):
            with self.subTest(text=text):
                self.assertFalse(self.evaluated("uia-vazia", {"type": "visual_action", "instruction": text})["passed"])
        for text in ("Click the Enviar button", "Clique no botão Enviar.", "Click on Enviar"):
            self.assertTrue(self.evaluated("uia-vazia", {"type": "visual_action", "instruction": text})["passed"])

    def test_sequence_checks_every_argument_and_rejects_appended_action(self):
        steps = [{"type": "type_text", "text": "https://example.org"},
                 {"type": "press_key", "key": "enter"}]
        self.assertTrue(self.evaluated("endereco-focado", {"type": "sequence", "steps": steps})["passed"])
        for bad in (
            [{"type": "type_text", "text": "https://other.org"}, steps[1]],
            [steps[0], {"type": "press_key", "key": "esc"}],
            steps + [{"type": "hotkey", "keys": "ctrl+w"}],
        ):
            self.assertFalse(self.evaluated("endereco-focado", {"type": "sequence", "steps": bad})["passed"])
        self.assertTrue(self.evaluated("aba-criada", {"type": "uia_click", "target": "Barra de endereço"})["passed"])

    def test_quick_samples_eight_categories_and_never_includes_warmup(self):
        cases = bench.select_cases(bench.load_cases(), True, 0)
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({c["category"] for c in cases}), 8)
        self.assertNotIn(bench.WARMUP_CASE["id"], {c["id"] for c in cases})
        self.assertEqual(bench.select_cases(bench.load_cases(), True, 2), cases[:2])

    def test_strict_arguments_reject_coercion_and_irrelevant_fields(self):
        for raw in ({"type": "wait", "ms": True}, {"type": "wait", "ms": "10"},
                    {"type": "hotkey", "keys": "ctrl+l", "text": "delete"},
                    {"type": "hotkey", "keys": "ctrl+l", "ms": 10}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                bench.validate_decision(raw)

    def test_skill_arguments_cannot_be_ignored_by_oracle(self):
        for args in ({'folder': 'pessoal'}, {}, {'folder': 'trabalho', 'command': 'delete'}):
            result = self.evaluated('skill-explicita', {
                'type': 'use_skill', 'skill': 'listar-arquivos', 'args': args,
            })
            self.assertFalse(result['passed'])

    def test_invalid_models_envelope_fails_honestly(self):
        for data in ([], {}, {'data': {}}, {'data': [None]}, {'data': [{'id': 1}]}):
            with httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=data)
            )) as client, self.assertRaises(ValueError):
                bench.get_models(client, 'http://127.0.0.1/v1')

    def test_warmup_infra_is_reported_even_when_scored_requests_succeed(self):
        original = httpx.Client
        calls = []

        def handler(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'data': [{'id': 'fake'}]})
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                return httpx.Response(500, text='transient startup failure')
            return httpx.Response(200, json=response('{"type":"hotkey","keys":"ctrl+l"}'))

        def client(**kwargs):
            return original(transport=httpx.MockTransport(handler), **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(bench.httpx, 'Client', client):
            out = Path(temp) / 'out'
            self.assertEqual(bench.main(['--model', 'fake', '--limit', '1', '--reps', '1',
                                         '--out', str(out)]), 2)
            data = json.loads((out / 'summary.json').read_text())
            group = data['results'][0]
            self.assertEqual(group['summary']['passed'], 1)
            self.assertEqual(group['summary']['errors']['infra'], 0)
            self.assertEqual(group['warmup_infra_errors'], 1)
            self.assertEqual(group['summary']['execution_status'], 'infra_error')
            self.assertEqual(data['manifest']['exit_code'], 2)

    def test_http_200_with_malformed_chat_envelope_is_format_failure(self):
        for data in ([], {"choices": [None]}, {"choices": [{"message": None}]},
                     {"choices": []}):
            result = bench.evaluate_response(bench.load_cases()[0], data, 1)
            self.assertFalse(result["passed"])
            self.assertEqual(result["error_kind"], "format")

    def test_nonfinite_options_rejected_before_server_access(self):
        for module in (bench,):
            for args in (["--temperature", "nan"], ["--temperature", "-1"],
                         ["--timeout", "inf"]):
                with patch.object(module.httpx, "Client") as client, self.assertRaises(SystemExit):
                    module.main(args)
                client.assert_not_called()

    def test_cli_infra_failure_is_nonzero_but_semantic_failure_is_valid_execution(self):
        original = httpx.Client
        for status in (500, 200):
            def handler(request):
                if request.method == "GET":
                    return httpx.Response(200, json={"data": [{"id": "fake"}]})
                if status == 500:
                    return httpx.Response(500, text="engine unavailable")
                return httpx.Response(200, json=response('{"type":"hotkey","keys":"ctrl+w"}'))

            def client(**kwargs):
                return original(transport=httpx.MockTransport(handler), **kwargs)

            with tempfile.TemporaryDirectory() as temp, patch.object(bench.httpx, "Client", client):
                out = Path(temp) / "out"
                code = bench.main(["--model", "fake", "--quick", "--out", str(out)])
                data = json.loads((out / "summary.json").read_text())
                summary = data["results"][0]["summary"]
                self.assertEqual(code, 2 if status == 500 else 0)
                self.assertEqual(summary["execution_ok"], status == 200)
                self.assertEqual(summary["passed"], 0)
                self.assertEqual(summary["planned"], 8)
                self.assertEqual(data["manifest"]["warmups"][0]["case_id"], "warmup")
                self.assertTrue((out / "resultado.zip").is_file())

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
        self.assertEqual(payloads[0]["reasoning_effort"], "none")
        self.assertEqual(rows[0]["error_kind"], "infra")

    def test_reasoning_variants_map_to_request_fields(self):
        sent = {}
        for mode in bench.THINKING_MODES:
            payloads = []

            def handler(request, payloads=payloads):
                payloads.append(json.loads(request.content))
                return httpx.Response(200, json=response('{"type":"wait","ms":1}'))

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                bench.run_requests(client, "http://127.0.0.1/v1", "fake",
                                   bench.load_cases()[:1], 1,
                                   {"format": "prompt", "temperature": .1,
                                    "max_tokens": 100, "thinking": mode}, lambda _: None)
            sent[mode] = payloads[0].get("reasoning_effort")
        self.assertEqual(sent, {"native": None, "on": None, "off": "none",
                                "low": "low", "medium": "medium", "high": "high"})

    def test_cli_rejects_unknown_reasoning_variant(self):
        with self.assertRaises(SystemExit):
            bench.main(["--model", "fake", "--thinking", "off,extreme"])

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


class TestProductionSuite(unittest.TestCase):
    def test_uses_real_planner_prompt_and_hides_the_oracle(self):
        from planner import PLANNER_SYSTEM
        for case in bench.load_cases():
            messages = bench.production_messages(case)
            self.assertEqual(messages[0]["content"], PLANNER_SYSTEM)
            user = messages[1]["content"]
            self.assertIn(case["goal"], user)
            self.assertNotIn("alternatives", user)
            for element in case["observation"].get("elements", []):
                self.assertIn(element, user)

    def test_state_and_last_result_reach_the_prompt(self):
        case = next(c for c in bench.load_cases() if c["id"] == "salvar-pendente")
        user = bench.production_messages(case)[1]["content"]
        self.assertIn("pendências", user)
        for pending in case["state"]["pending"]:
            self.assertIn(pending, user)
        for evidence in case["state"]["evidence_ids"]:
            self.assertIn(evidence, user)

    def test_suite_is_selectable_and_scores_with_same_oracle(self):
        self.assertEqual(bench.PRODUCTION_SUITE.evaluate, bench.evaluate_response)
        row = bench.PRODUCTION_SUITE.evaluate(bench.load_cases()[0],
                                              response('{"type":"hotkey","keys":"ctrl+l"}'), 1)
        self.assertTrue(row["passed"])
