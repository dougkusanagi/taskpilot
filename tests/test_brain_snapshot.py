import json
import tempfile
import unittest
from pathlib import Path

import httpx

from evals.brain.client import Client
from evals.brain.snapshot import snapshots
from evals.brain.snapshot_bench import measure


class SnapshotTests(unittest.TestCase):
    def test_pruning_pagination_preserves_catalog_items(self):
        for page in ["loja", "webmail", "noticias"]:
            raw = snapshots(page, "read_page", prune=False, paginate=False)
            pruned = snapshots(page, "read_page", prune=True, paginate=True)
            self.assertGreater(len(raw[0]), max(map(len, pruned)))
            self.assertGreater(len(pruned), 1)
            self.assertIn("UNTRUSTED", pruned[0])
            self.assertIn("Menu institucional", raw[0])
            self.assertNotIn("SEGREDO_OCULTO", "".join(raw + pruned))
            self.assertNotIn("Menu institucional", "".join(pruned))
        shop = "".join(snapshots("loja", "get_page_text", prune=True, paginate=True))
        self.assertIn("Boreal", shop)
        self.assertIn("starting_at", shop)
        self.assertIn("Waterblock", shop)

    def test_counts_use_tokenizer_response(self):
        def reply(request):
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "teste"}]})
            payload = json.loads(request.content)
            self.assertFalse(payload["add_special"])
            return httpx.Response(200, json={"tokens": [17, 23, 41]})

        client = Client("http://localhost", "teste", constrained=False, thinking=False)
        client.http.close()
        client.http = httpx.Client(
            base_url="http://localhost", transport=httpx.MockTransport(reply)
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                report = measure(client, Path(temp) / "result.json")
                self.assertEqual(len(report["rows"]), 24)
                self.assertTrue(all(row["max_tokens"] == 3 for row in report["rows"]))
        finally:
            client.close()
