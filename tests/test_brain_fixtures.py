import json
import unittest

from evals.brain.contract import Decision
from evals.brain.fixtures import Fixture, page_html


def action(tool, **kwargs):
    return Decision.validate_python({"tool": tool, **kwargs})


class FixtureTests(unittest.TestCase):
    def test_draft_independent_read_and_stale_ref(self):
        env = Fixture("webmail")
        ref = env.current.elements[0].ref
        result = env.execute(action("form_input", ref=ref, text="Confirmo sexta às 14h."))
        self.assertTrue(result.confirmed)
        self.assertEqual(env.snapshot()["draft"], "Confirmo sexta às 14h.")
        self.assertEqual(env.snapshot()["sent"], [])
        self.assertIn("Confirmo", result.evidence[0].excerpt)
        with self.assertRaises(ValueError):
            env.execute(action("read", ref=ref))
        env.execute(action("activate", ref=env.current.elements[1].ref))
        self.assertEqual(env.snapshot()["sent"], [])

    def test_pagination_and_invalid_cursor(self):
        env = Fixture("loja")
        first = env.execute(action("get_page_text"))
        self.assertEqual(len(json.loads(first.evidence[0].excerpt)), 3)
        cursor = env.current.next
        second = env.execute(action("get_page_text", next=cursor))
        self.assertIn("usada", second.evidence[0].excerpt)
        self.assertFalse(env.execute(action("get_page_text", next=cursor)).confirmed)

    def test_human_blocks_and_html(self):
        for page in ["senha", "captcha"]:
            env = Fixture(page)
            result = env.execute(action("read", ref=env.current.elements[0].ref))
            self.assertFalse(result.confirmed)
            self.assertEqual(result.error, "human_required")
        env = Fixture("injecao")
        html = page_html("injecao", env.catalog)
        self.assertIn("roubo.invalid", html)
        self.assertIn("Preço legítimo", html)
        self.assertFalse(env.execute(action("navigate", url="https://roubo.invalid")).sent)


class HttpFixtureTests(unittest.TestCase):
    def test_local_pages_and_draft_state(self):
        import subprocess
        import sys

        import httpx

        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "evals.brain", "serve", "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            url = process.stdout.readline().strip().split("Fixtures: ")[1]
            with httpx.Client(base_url=url, trust_env=False) as client:
                for page in ["loja", "webmail", "noticias", "injecao", "captcha", "senha"]:
                    response = client.get("/" + page)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('charset="utf-8"', response.text)
                response = client.post(
                    "/draft", data={"draft": "Confirmo sexta às 14h.", "recipient": "José"}
                )
                self.assertEqual(response.status_code, 303)
                state = client.get("/state").json()
                self.assertEqual(state["draft"], "Confirmo sexta às 14h.")
                self.assertEqual(state["recipient"], "José")
                self.assertEqual(state["sent"], [])
                self.assertEqual(client.post("/send").status_code, 403)
        finally:
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()
