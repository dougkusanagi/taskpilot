import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from evals.brain.client import Client, Completion
from evals.brain.metrics import VramMonitor, percentile, summarize
from evals.brain.runner import run
from evals.brain.scenes import load_scenes


class RunnerTests(unittest.TestCase):
    def test_errors_in_denominator_and_missing_vram(self):
        rows = [
            {
                "split": "blind",
                "correct": True,
                "valid_json": True,
                "forbidden": False,
                "latency_s": 1,
            },
            {"split": "blind", "latency_s": 60, "infrastructure_error": "timeout"},
        ]
        result = summarize(rows, 4000)
        self.assertEqual(result["blind_accuracy"], 0.5)
        self.assertEqual(result["latency_p95_s"], 60)
        self.assertFalse(result["gate_n1"])
        self.assertFalse(summarize(rows[:1], None)["gate_n1"])
        self.assertTrue(summarize(rows[:1], 4000)["gate_n1"])
        self.assertEqual(percentile([3, 1, 2], 0.5), 2)

    def test_vram_only_requested_pid(self):
        monitor = VramMonitor(42)
        with patch("evals.brain.metrics.subprocess.run") as mock:
            mock.return_value.stdout = "41, 5000\n42, 2048\n"
            monitor.sample()
        self.assertEqual(monitor.peak_mib, 2048)

    def test_http_request_conditions_and_model_identity(self):
        requests = []

        def respond(request):
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "teste"}]})
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": '{"tool":"ask","question":"Qual?"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                },
            )

        for constrained, thinking in [(False, False), (True, True)]:
            client = Client(
                "http://localhost:8091", "teste", constrained=constrained, thinking=thinking
            )
            client.http.close()
            client.http = httpx.Client(
                base_url="http://localhost:8091", transport=httpx.MockTransport(respond)
            )
            client.check_model()
            response = client.complete({"goal": "Pergunte"})
            self.assertEqual(response.completion_tokens, 10)
            self.assertEqual("response_format" in requests[-1], constrained)
            self.assertEqual(requests[-1]["chat_template_kwargs"]["enable_thinking"], thinking)
            client.close()
        with self.assertRaises(ValueError):
            Client("https://externo.invalid", "teste", constrained=False, thinking=False)

    def test_runner_matrix_keeps_failures(self):
        scene = load_scenes("blind")[0]
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("evals.brain.runner.Client") as mock,
            patch("evals.brain.runner.load_scenes", return_value=[scene]),
        ):
            mock.return_value.complete.side_effect = [
                Completion('{"tool":"ask","question":"Qual?"}', 1, 10, 5),
                RuntimeError("infra"),
            ] * 4
            path = Path(temp) / "result.json"
            report = run("teste", "http://localhost", repetitions=1, output=path)
            self.assertTrue(report["complete"])
            self.assertEqual(len(report["conditions"]), 4)
            self.assertEqual(json.loads(path.read_text())["model"], "teste")
            for condition in report["conditions"]:
                self.assertEqual(condition["summary"]["count"], 1)
                self.assertEqual(condition["summary"]["infrastructure_errors"], 1)
                self.assertFalse(condition["summary"]["gate_n1"])

    def test_preflight_failure_stays_in_denominator(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("evals.brain.runner.Client") as mock,
            patch("evals.brain.runner.load_scenes", return_value=load_scenes("blind")[:1]),
        ):
            mock.return_value.check_model.side_effect = RuntimeError("endpoint indisponível")
            report = run(
                "teste",
                "http://localhost",
                repetitions=3,
                mode="free-off",
                output=Path(temp) / "result.json",
            )
            summary = report["conditions"][0]["summary"]
            self.assertEqual(summary["count"], 3)
            self.assertEqual(summary["infrastructure_errors"], 3)
            self.assertFalse(summary["gate_n1"])
