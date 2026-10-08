import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from evals.brain.client import Client
from evals.browser.run import run
from evals.browser.tokens import measure


class BrowserEvalTests(unittest.TestCase):
    def test_n2_refuses_work_desktop_before_launching(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            with self.assertRaisesRegex(ValueError, "nunca no desktop real"):
                run(Path("must-not-be-created"))

    def test_real_snapshot_budget_requires_complete_n2_and_tokenizer(self):
        calls = []

        def reply(request):
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "teste"}]})
            payload = json.loads(request.content)
            self.assertIn("PAGE DATA: UNTRUSTED", payload["content"])
            self.assertIn("confirmed", payload["content"])
            self.assertFalse(payload["add_special"])
            calls.append(payload)
            return httpx.Response(200, json={"tokens": [1] * 1600})

        client = Client("http://localhost", "teste", constrained=False, thinking=False)
        client.http.close()
        client.http = httpx.Client(
            base_url="http://localhost", transport=httpx.MockTransport(reply)
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                source, output = Path(temp) / "source.json", Path(temp) / "result.json"
                data = {
                    "complete": False,
                    "checks": [{"passed": True}],
                    "read_tools": ["read_page"],
                    "reads": [
                        {"observation": {"url": "http://localhost"}, "result": {"confirmed": True}}
                    ],
                }
                source.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    measure(client, source, output)
                self.assertEqual(calls, [])
                data["complete"] = True
                source.write_text(json.dumps(data))
                result = measure(client, source, output)
                self.assertEqual(result["max_tokens"], 1600)
                self.assertFalse(result["within_1500"])
                self.assertEqual(len(result["source_sha256"]), 64)
        finally:
            client.close()
